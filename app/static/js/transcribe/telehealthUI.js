import { createTelehealthRecordingClient } from './telehealthRecordingClient.js?v=20261106-stop-feedback';
import { createTelehealthDiagnostics } from './telehealthDiagnostics.js?v=20261106-server-events';
import {
  createTelehealthRecoveryJournal,
  createTelehealthUploadPacer,
  replayTelehealthRecoveryWindows,
} from './telehealthRecoveryJournal.js?v=20261105-cross-tab-journal';

// The recording bar is controlled only when Telehealth is explicitly selected.
// Existing microphone/Deepgram recording flows remain untouched otherwise.
const SOURCES = ['clinician', 'patient'];
const SPEECH_CONFIRM_MS = 5000;

export function attachTelehealthControls({
  getTranscriptId,
  getIngestionMode,
  isOtherRecordingActive,
  canUseRecording = () => true,
  getPreferences,
  persistPreferences,
  csrfFetch,
  refreshWorkspace,
  showNotice,
} = {}) {
  const toggle = document.querySelector('[data-telehealth-toggle]');
  const mainRecord = document.querySelector('[data-record-toggle]');
  const recordLabel = document.querySelector('[data-record-toggle-label]');
  const recordIcon = document.querySelector('[data-record-toggle-icon]');
  const receipt = document.querySelector('[data-telehealth-receipt]');
  const channels = document.querySelector('[data-telehealth-channels]');
  const combined = document.querySelector('[data-telehealth-combined]');
  const combinedLevel = document.querySelector('[data-telehealth-combined-level]');
  const message = document.querySelector('[data-telehealth-receipt-message]');
  const actions = document.querySelector('[data-telehealth-actions]');
  const retry = document.querySelector('[data-telehealth-retry]');
  const partial = document.querySelector('[data-telehealth-transcribe]');
  const submitRetry = document.querySelector('[data-telehealth-submit-retry]');
  const discard = document.querySelector('[data-telehealth-discard]');
  const debugPanel = document.querySelector('[data-telehealth-debug-panel]');
  const debugReference = document.querySelector('[data-telehealth-debug-reference]');
  const debugEvents = document.querySelector('[data-telehealth-debug-events]');
  const guidance = document.querySelector('[data-telehealth-guidance]');
  const guidanceCancel = document.querySelector('[data-telehealth-guidance-cancel]');
  const guidanceContinue = document.querySelector('[data-telehealth-guidance-continue]');
  const guidanceError = document.querySelector('[data-telehealth-guidance-error]');
  if (!toggle || !mainRecord || !receipt || !guidance || typeof csrfFetch !== 'function') {
    return { sync: () => {}, isActive: () => false };
  }

  let enabled = false;
  let client = null;
  let restoredCapture = null;
  const recoveryJournal = createTelehealthRecoveryJournal();
  const diagnostics = createTelehealthDiagnostics({
    sendBatch: async (targetTranscriptId, diagnosticId, events) => {
      const response = await csrfFetch(
        '/api/v1/transcripts/' + encodeURIComponent(targetTranscriptId) + '/telehealth/diagnostics',
        {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ diagnostic_id: diagnosticId, events }),
        },
      );
      if (!response.ok) throw new Error('Capture diagnostics delivery failed.');
      return response.json();
    },
  });
  let lastCheckedTranscriptId = null;
  let captureStatusCheckInFlight = false;
  let ownProcessingCheckInFlight = false;
  let ownProcessingLastPollAt = 0;
  let boundTranscriptId = getTranscriptId?.() || null;
  let activating = false;
  let submissionInFlight = false;
  let stopPhase = null;
  let discardInFlight = false;
  let telehealthOwnsRecordLock = false;
  let sourceLoss = false;
  let collapsed = false;
  let collapseTimer = null;
  const confirmed = { clinician: 0, patient: 0 };
  const lastLevel = { clinician: 0, patient: 0 };
  const status = { clinician: 'waiting', patient: 'waiting' };
  const nodes = Object.fromEntries(SOURCES.map((name) => [
    name, {
      root: receipt.querySelector('[data-telehealth-source="' + name + '"]'),
      level: receipt.querySelector('[data-telehealth-level="' + name + '"]'),
      status: receipt.querySelector('[data-telehealth-speech-status="' + name + '"]'),
    },
  ]));

  const diagnosticSuffix = () => diagnostics.diagnosticId
    ? ' (reference ' + diagnostics.diagnosticId.slice(0, 12) + ')' : '';
  const notice = (text, kind = 'info') => {
    if (typeof showNotice === 'function') showNotice(text, kind);
  };
  // Include the stop pipeline in lifecycle guards, even once the local mic
  // and tab tracks have stopped: buffered audio is not durably sealed yet.
  const recordingInProgress = () => client && ['starting', 'recording', 'stopping'].includes(client.state);
  const blockSwitch = () => Boolean(restoredCapture) || recordingInProgress()
    || (client && ['recovery_required', 'incomplete', 'sealed'].includes(client.state))
    || Boolean(isOtherRecordingActive?.());

  const expand = (forced = false) => {
    if (!forced && !collapsed) return;
    collapsed = false;
    channels.hidden = false;
    combined.hidden = true;
    if (collapseTimer !== null) window.clearTimeout(collapseTimer);
    collapseTimer = null;
  };
  const resetMeters = () => {
    confirmed.clinician = confirmed.patient = 0;
    lastLevel.clinician = lastLevel.patient = 0;
    status.clinician = status.patient = 'waiting';
    expand(true);
    receipt.classList.remove('is-verified', 'is-glowing', 'is-interrupted');
    for (const channel of SOURCES) {
      nodes[channel].root?.classList.remove('is-heard');
      if (nodes[channel].level) nodes[channel].level.style.width = '3%';
      if (nodes[channel].status) nodes[channel].status.textContent = 'Waiting for audio';
    }
  };
  const render = () => {
    // Hidden-by-preference users must still be able to recover a capture
    // persisted before a tab crash. The phone control becomes visible only
    // while there is an unresolved server receipt or active local capture.
    toggle.hidden = getPreferences?.().show_telehealth_control === false
      && !restoredCapture && !enabled;
    toggle.setAttribute('aria-pressed', String(enabled));
    receipt.hidden = !enabled;
    toggle.disabled = activating || blockSwitch() || !getTranscriptId?.();
    if (enabled && recordLabel) {
      const stoppedButSaving = client?.state === 'stopping';
      const submitting = client?.state === 'sealed' && stopPhase === 'submitting';
      const activelyRecording = client?.state === 'starting' || client?.state === 'recording';
      // Never display 'Stop' once a stop was requested. The recorder can
      // remain safely locked while flush, uploads, seal and submission finish.
      recordLabel.textContent = stoppedButSaving
        ? stopPhase === 'sealing' ? 'Finalising audio…'
          : stopPhase === 'uploading' ? 'Saving audio…' : 'Stopping…'
        : submitting ? 'Submitting…'
        : activelyRecording ? 'Stop'
        : restoredCapture?.status === 'processing' ? 'Transcribing…'
        : restoredCapture ? 'Resolve telehealth'
        : client?.state === 'transcribing' ? 'Transcribing…'
        : client?.state === 'recovery_required' ? 'Recover audio'
        : client?.state === 'incomplete' ? 'Review recording'
        : client?.state === 'sealed' ? 'Retry submission'
        : 'Start telehealth';
      if (recordIcon) {
        recordIcon.dataset.lucide = activelyRecording ? 'square'
          : stoppedButSaving || submitting ? 'loader-circle' : 'disc';
        window.refreshLucideIcons?.(recordIcon.parentElement || recordIcon);
      }
    }
    const actionsInFlight = submissionInFlight || discardInFlight;
    const recordBlocked = actionsInFlight || Boolean(restoredCapture)
      || Boolean(client && ['stopping', 'recovery_required', 'incomplete', 'sealed', 'transcribing'].includes(client.state));
    if (enabled && recordBlocked) {
      mainRecord.disabled = true;
      telehealthOwnsRecordLock = true;
    } else if (telehealthOwnsRecordLock) {
      mainRecord.disabled = !canUseRecording();
      telehealthOwnsRecordLock = false;
    }
    if (retry) retry.hidden = client?.state !== 'recovery_required';
    if (submitRetry) submitRetry.hidden = client?.state !== 'sealed';
    const hasRestoredJob = restoredCapture?.parts?.some((part) => part.ingestion_job_id);
    const canRestorePartial = restoredCapture
      && ['active', 'stopping', 'ready', 'interrupted'].includes(restoredCapture.status);
    if (partial) {
      partial.hidden = client?.state !== 'incomplete' && !canRestorePartial;
      partial.textContent = canRestorePartial ? 'Recover audio and transcribe' : 'Acknowledge and transcribe';
    }
    if (discard) discard.hidden = !(
      ['recovery_required', 'incomplete', 'sealed'].includes(client?.state)
      || (restoredCapture && !hasRestoredJob)
    );
    if (actions) actions.hidden = [retry, partial, submitRetry, discard].every((item) => item?.hidden !== false);

    for (const action of [retry, partial, submitRetry]) {
      if (action) action.disabled = actionsInFlight;
    }
    if (discard) discard.disabled = actionsInFlight;
  };
  const withSubmission = async (action) => {
    if (submissionInFlight) return;
    submissionInFlight = true;
    render();
    try {
      return await action();
    } finally {
      submissionInFlight = false;
      render();
    }
  };

  const postRecovery = async (capture, suffix, payload = {}) => {
    const response = await csrfFetch(
      '/api/v1/transcripts/' + encodeURIComponent(capture.transcript_id)
      + '/telehealth/captures/' + encodeURIComponent(capture.capture_session_id)
      + '/' + suffix,
      {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    );
    if (!response.ok) {
      let errorMessage = 'Could not resolve the saved Telehealth capture.';
      try { errorMessage = (await response.json())?.error?.message || errorMessage; } catch (_) {}
      throw new Error(errorMessage);
    }
    return response.json();
  };

  const checkPersistedCapture = async () => {
    const id = getTranscriptId?.();
    const recheckProcessing = Boolean(restoredCapture
      && ['processing', 'failed'].includes(restoredCapture.status));
    if (!id || captureStatusCheckInFlight || (id === lastCheckedTranscriptId && !recheckProcessing)
      || (enabled && !restoredCapture) || client) return;
    captureStatusCheckInFlight = true;
    lastCheckedTranscriptId = id;
    try {
      const response = await csrfFetch(
        '/api/v1/transcripts/' + encodeURIComponent(id) + '/telehealth/unresolved',
        { credentials: 'include' },
      );
      if (!response.ok) {
        lastCheckedTranscriptId = null;
        return;
      }
      const saved = await response.json();
      if (id !== getTranscriptId?.() || client) return;
      if (!saved) {
        // The pending capture has completed/disappeared, so return to
        // ordinary recording state rather than retaining a stale blocker.
        if (restoredCapture) {
          restoredCapture = null;
          enabled = false;
          resetMeters();
          if (recordLabel) recordLabel.textContent = 'Start recording';
          if (mainRecord) mainRecord.disabled = false;
          render();
        }
        return;
      }
      if (!['active', 'stopping', 'ready', 'interrupted', 'failed', 'processing'].includes(saved.status)) return;
      restoredCapture = saved;
      enabled = true;
      sourceLoss = true;
      resetMeters();
      if (['active', 'stopping', 'interrupted', 'failed'].includes(saved.status)) {
        receipt.classList.add('is-interrupted');
      } else {
        receipt.classList.add('is-verified');
      }
      if (message) {
        message.textContent = saved.status === 'processing'
          ? 'Both Telehealth sources are being transcribed. Wait for completion or retry a failed source if prompted.'
          : saved.status === 'ready'
            ? 'Sealed Telehealth audio is awaiting transcription. Review the stored parts and submit them.'
            : saved.parts?.some((part) => part.ingestion_job_id)
              ? 'A Telehealth transcription failed. Retry the retained source audio using the consultation recovery controls.'
              : 'The Telehealth recording ended unexpectedly. Encrypted unacknowledged audio may be recoverable on this browser; the final unflushed seconds might be missing. Recover or discard explicitly.';
      }
      render();
    } catch (_) {
      // Do not infer that the remote source is complete from a failed check.
      lastCheckedTranscriptId = null;
    } finally {
      captureStatusCheckInFlight = false;
    }
  };

  const onSourceState = ({ channel, status: next }) => {
    if (!SOURCES.includes(channel)) return;
    status[channel] = next;
    if (next === 'muted') {
      nodes[channel].status.textContent = 'Source muted';
    } else if (next === 'live' && confirmed[channel] < SPEECH_CONFIRM_MS) {
      nodes[channel].status.textContent = 'Waiting for voice';
    }
  };
  const onDiagnostic = ({ event, ...fields }) => {
    diagnostics.emit(event, fields);
    if (debugPanel && !debugPanel.hidden) {
      if (debugReference) debugReference.textContent = diagnostics.diagnosticId || 'Unavailable';
      if (debugEvents) {
        // textContent only: predefined metadata, never HTML, URLs or raw errors.
        debugEvents.textContent = diagnostics.snapshot().events.slice(-12)
          .map((item) => [item.at, item.event, item.channel, item.surface, item.state, item.code]
            .filter(Boolean).join(' · '))
          .join('\n');
      }
    }
    if (['picker_rejected', 'capture_failed', 'capture_stopped', 'capture_interrupted'].includes(event)) {
      void diagnostics.flush();
    }
  };
  const onVadState = ({ channel, status: next }) => {
    if (!SOURCES.includes(channel) || sourceLoss) return;
    if (next === 'loading') nodes[channel].status.textContent = 'Checking speech…';
    if (next === 'ready' && confirmed[channel] < SPEECH_CONFIRM_MS) {
      nodes[channel].status.textContent = 'Waiting for speech';
    }
    if (next === 'unavailable') {
      nodes[channel].status.textContent = 'Speech check unavailable — audio still recording';
    }
  };
  const onSpeechFrame = ({ channel, speechMs }) => {
    if (!SOURCES.includes(channel) || sourceLoss || status[channel] === 'muted'
      || !Number.isFinite(speechMs) || speechMs <= 0) return;
    if (confirmed[channel] >= SPEECH_CONFIRM_MS) return;
    confirmed[channel] = Math.min(SPEECH_CONFIRM_MS, confirmed[channel] + speechMs);
    if (confirmed[channel] < SPEECH_CONFIRM_MS) return;
    onDiagnostic({ event: 'vad_speech_confirmed', channel });
    nodes[channel].root?.classList.add('is-heard');
    if (nodes[channel].status) nodes[channel].status.textContent = 'Speech detected';
    if (SOURCES.every((id) => confirmed[id] >= SPEECH_CONFIRM_MS)
      && !receipt.classList.contains('is-verified')) {
      receipt.classList.add('is-verified', 'is-glowing');
      if (message) message.textContent = 'Speech detected on both audio sources';
      collapseTimer = window.setTimeout(() => {
        if (!enabled || sourceLoss) return;
        receipt.classList.remove('is-glowing');
        collapsed = true;
        channels.hidden = true;
        combined.hidden = false;
      }, 650);
    }
  };
  const onLevel = ({ channel, rms }) => {
    if (!SOURCES.includes(channel) || sourceLoss || status[channel] === 'muted') return;
    // RMS drives the animated meter ONLY. Speech confirmation uses Silero VAD.
    const level = Math.min(100, Math.max(3, Math.round(rms * 420)));
    lastLevel[channel] = level;
    if (nodes[channel].level) nodes[channel].level.style.width = level + '%';
    if (combinedLevel) combinedLevel.style.width = Math.max(...SOURCES.map((id) => lastLevel[id])) + '%';
  };
  const onWarning = ({ channel, failure_code: reason, recoverable = false }) => {
    if (!recoverable) {
      sourceLoss = true;
      expand(true);
      receipt.classList.add('is-interrupted');
    }
    if (message) message.textContent = recoverable
      ? 'Audio upload could not be confirmed. Retry the retained audio.'
      : 'Telehealth audio may be incomplete. Stop and review before transcribing.';
    if (channel && nodes[channel]?.status) nodes[channel].status.textContent = 'Audio interrupted';
    notice(recoverable
      ? 'Telehealth upload needs a retry. Your accepted audio parts remain recoverable.'
      : 'Telehealth source interrupted. Restart recording or review the incomplete capture.',
      'warning');
    render();
  };

  const confirmGuidance = () => new Promise((resolve) => {
    // Show the current crash-buffer/privacy disclosure on every selection,
    // including for owners who acknowledged an older guidance version.
    // The server-side first-use acknowledgement is still persisted once.
    let settled = false;
    const finish = (answer) => {
      if (settled) return;
      settled = true;
      guidance.removeEventListener('close', onClose);
      guidanceContinue.removeEventListener('click', onContinue);
      guidanceCancel.removeEventListener('click', onCancel);
      guidance.close();
      resolve(answer);
    };
    const onClose = () => finish(false);
    const onCancel = () => finish(false);
    const onContinue = async () => {
      guidanceContinue.disabled = true;
      guidanceError.hidden = true;
      try {
        if (getPreferences?.().telehealth_guidance_acknowledged !== true) {
          await persistPreferences({ telehealth_guidance_acknowledged: true });
        }
        finish(true);
      } catch (_) {
        guidanceError.textContent = 'Could not save your acknowledgement. Try again.';
        guidanceError.hidden = false;
      } finally {
        guidanceContinue.disabled = false;
      }
    };
    guidance.addEventListener('close', onClose);
    guidanceContinue.addEventListener('click', onContinue);
    guidanceCancel.addEventListener('click', onCancel);
    guidance.showModal();
    guidanceContinue.focus();
  });

  const switchToRecordedMode = async () => {
    if (getIngestionMode?.() === 'whole_file') return;
    if (!window.confirm('Telehealth requires recorded upload mode. Switch this consultation to recorded upload?')) {
      throw new Error('Telehealth was not enabled.');
    }
    const id = getTranscriptId();
    const response = await csrfFetch('/api/v1/transcripts/' + encodeURIComponent(id), {
      method: 'PATCH', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ingestion_mode: 'whole_file' }),
    });
    if (!response.ok) {
      throw new Error('Recording mode could not be changed. Stop or resolve the current capture first.');
    }
    await persistPreferences({ preferred_recording_mode: 'whole_file' });
    await refreshWorkspace?.();
  };

  const startRecording = async () => {
    if (submissionInFlight || discardInFlight) return;
    if (client?.state === 'transcribing') {
      notice('The consultation is still being transcribed.', 'info');
      return;
    }
    if (getIngestionMode?.() !== 'whole_file') {
      notice('Select Recorded upload before starting Telehealth.', 'warning');
      return;
    }
    resetMeters();
    stopPhase = null;
    diagnostics.begin(getTranscriptId());
    if (debugReference) debugReference.textContent = diagnostics.diagnosticId || 'Unavailable';
    if (debugEvents) debugEvents.textContent = '';
    sourceLoss = false;
    if (message) message.textContent = 'Checking both audio sources';
    client = createTelehealthRecordingClient({
      transcriptId: getTranscriptId(),
      csrfFetch, onLevel, onSourceState, onWarning, onStateChange: render,
      onSpeechFrame, onVadState, onDiagnostic,
      onStopProgress: (phase) => {
        stopPhase = phase;
        if (message && !sourceLoss) {
          message.textContent = phase === 'uploading'
            ? 'Recording stopped. Saving clinician and patient audio…'
            : phase === 'sealing'
              ? 'Audio uploaded. Finalising the recording…'
              : 'Stopping recording and flushing the last audio frames…';
        }
        render();
      },
      confirmWindowAudio: () => window.confirm(
        'Window audio is experimental. Depending on your browser and operating system, sharing a window can capture ALL system audio, not just the call. Continue only if other application sounds are safe to record. Continue?'
      ),
      recoveryJournal,
    });
    const attempt = client.start(); // Direct user activation -> getDisplayMedia.
    render();
    try {
      await attempt;
    } catch (error) {
      notice((error instanceof Error ? error.message : 'Telehealth recording could not start.') + diagnosticSuffix(), 'error');
      client = null;
      render();
    }
  };

  const stopRecording = async () => {
    if (!client || submissionInFlight || discardInFlight) return;
    try {
      await withSubmission(async () => {
        const result = await client.stop();
        render();
        if (result.status === 'interrupted') {
          notice('Audio was interrupted. Review and acknowledge the incomplete recording before transcription.', 'warning');
          return;
        }
        stopPhase = 'submitting';
        if (message && !sourceLoss) message.textContent = 'Audio saved. Submitting for transcription…';
        render();
        await client.transcribe();
        if (message && !sourceLoss) message.textContent = 'Audio submitted. Transcription is processing…';
        notice('Both Telehealth audio sources were sent for transcription.', 'success');
        await refreshWorkspace?.();
      });
    } catch (error) {
      // Stop/submission failed: render the retry or recovery action, never
      // leave a disabled button stuck saying "Submitting…".
      stopPhase = null;
      render();
      notice((error instanceof Error ? error.message : 'Telehealth audio needs recovery.') + diagnosticSuffix(), 'error');
    }
  };

  const toggleTelehealth = async () => {
    if (activating || blockSwitch()) return;
    if (enabled) {
      enabled = false;
      stopPhase = null;
      resetMeters();
      render();
      return;
    }
    if (!getTranscriptId?.()) {
      notice('Create or select a consultation first.', 'warning');
      return;
    }
    activating = true;
    render();
    try {
      await switchToRecordedMode();
      if (!await confirmGuidance()) return;
      enabled = true;
      client = null;
      stopPhase = null;
      resetMeters();
      if (message) message.textContent = 'Recording starts immediately; both source indicators are informational.';
    } catch (error) {
      notice(error instanceof Error ? error.message : 'Telehealth could not be enabled.', 'error');
    } finally {
      activating = false;
      render();
    }
  };

  toggle.addEventListener('click', () => { void toggleTelehealth(); });
  // Capture phase: prevent the pre-existing microphone recorder from
  // responding to the SAME click while this separate capture is selected.
  mainRecord.addEventListener('click', (event) => {
    if (!enabled) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (submissionInFlight || discardInFlight) return;
    if (recordingInProgress()) {
      if (client?.state !== 'stopping') void stopRecording();
    } else if (!client || ['discarded'].includes(client.state)) {
      void startRecording();
    }
  }, true);
  combined?.addEventListener('click', () => expand(true));
  retry?.addEventListener('click', () => {
    if (!client || submissionInFlight || discardInFlight) return;
    void withSubmission(async () => {
      const result = await client.retryPendingUploads();
      render();
      if (result.status === 'interrupted') {
        notice('Review and acknowledge this incomplete recording before transcription.', 'warning');
        return;
      }
      await client.transcribe();
      notice('Recovered Telehealth audio was submitted for transcription.', 'success');
      await refreshWorkspace?.();
    }).catch((error) => {
      notice(error instanceof Error ? error.message : 'Audio recovery failed.', 'error');
    });
  });
  submitRetry?.addEventListener('click', () => {
    if (!client || client.state !== 'sealed' || submissionInFlight || discardInFlight) return;
    const activeClient = client;
    void withSubmission(async () => {
      await activeClient.transcribe();
      if (client !== activeClient) return;
      notice('Telehealth transcription was submitted.', 'success');
      await refreshWorkspace?.();
      render();
    }).catch((error) => {
      notice(error instanceof Error ? error.message : 'Telehealth transcription could not be submitted. Retry when ready.', 'error');
    });
  });
  discard?.addEventListener('click', () => {
    if (submissionInFlight || discardInFlight || (!client && !restoredCapture) || !window.confirm(
      'Permanently discard this unsubmitted recording? Clinician and patient audio staged so far will be queued for deletion. No transcription will be created.',
    )) return;
    discardInFlight = true;
    render();
    const saved = restoredCapture;
    const discardRequest = client ? client.discard() : postRecovery(saved, 'discard');
    void discardRequest.then(async (result) => {
      if (result.status !== 'discarded') throw new Error('Discard was not confirmed by the server.');
      if (saved) await recoveryJournal.clear(saved.capture_session_id, saved.transcript_id);
      restoredCapture = null;
      resetMeters();
      enabled = false;
      notice('Unsubmitted Telehealth audio was discarded.', 'info');
      await refreshWorkspace?.();
      render();
    }).catch((error) => {
      notice(error instanceof Error ? error.message : 'Could not discard the recording.', 'error');
      render();
    }).finally(() => {
      discardInFlight = false;
      render();
    });
  });
  partial?.addEventListener('click', () => {
    if (submissionInFlight || discardInFlight || (!client && !restoredCapture) || !window.confirm(
      'This Telehealth recording may be incomplete after a browser crash. Encrypted local windows will be replayed with their original IDs where available. The final unflushed seconds may still be missing. Review the result before any clinical note. Continue?',
    )) return;
    const continueCapture = async () => {
      if (client) {
        await client.acknowledgeIncomplete();
        return client.transcribe();
      }
      const saved = restoredCapture;
      let current = saved;
      if (['active', 'stopping'].includes(saved.status)) {
        const stopDetails = saved.failure_code
          ? { failure_code: saved.failure_code, interrupted_channel: saved.failure_channel }
          : { failure_code: 'browser_capture_failed' };
        current = await postRecovery(saved, 'stop', stopDetails);
      }
      // Reconstruct the server's accepted sequence ledger and replay ONLY
      // encrypted, unacknowledged windows belonging to the owner-scoped
      // session. An accepted response with mismatched identity is rejected.
      // This cannot recover AudioWorklet frames not yet flushed to a WAV.
      const replayed = await replayTelehealthRecoveryWindows({
        journal: recoveryJournal, capture: current, csrfFetch,
        pace: createTelehealthUploadPacer(),
      });
      const latestResponse = await csrfFetch(
        '/api/v1/transcripts/' + encodeURIComponent(saved.transcript_id)
          + '/telehealth/captures/' + encodeURIComponent(saved.capture_session_id),
        { credentials: 'include' },
      );
      if (!latestResponse.ok) throw new Error('Could not verify the recovered source ledger.');
      current = await latestResponse.json();
      if (!['ready', 'interrupted'].includes(current.status)) {
        const counts = (channel) => current.parts.filter((part) => part.channel === channel).length;
        if (!counts('clinician') && !counts('patient')) {
          throw new Error('No audio was durably retained. Discard this capture and record again.');
        }
        current = await postRecovery(saved, 'seal', {
          expected_clinician_parts: counts('clinician'),
          expected_patient_parts: counts('patient'),
        });
      }
      if (current.status === 'interrupted') {
        await postRecovery(saved, 'acknowledge-incomplete');
      }
      const queued = await postRecovery(saved, 'transcribe');
      restoredCapture = null;
      enabled = false;
      return queued;
    };
    void withSubmission(continueCapture).then(async () => {
      notice('Encrypted local windows were reconciled with saved Telehealth audio. Transcription is processing; review for missing final speech.', 'warning');
      await refreshWorkspace?.();
      render();
    }).catch((error) => {
      notice(error instanceof Error ? error.message : 'Could not recover the saved recording.', 'error');
      render();
    });
  });
  window.addEventListener('beforeunload', (event) => {
    if (!blockSwitch()) return;
    event.preventDefault();
    event.returnValue = '';
  });
  const checkOwnTranscription = async () => {
    if (!client || client.state !== 'transcribing' || ownProcessingCheckInFlight
      || Date.now() - ownProcessingLastPollAt < 2000) return;
    ownProcessingCheckInFlight = true;
    ownProcessingLastPollAt = Date.now();
    const activeClient = client;
    try {
      const result = await activeClient.getStatus();
      if (client !== activeClient) return;
      if (result?.status === 'completed') {
        // Permit a follow-on Telehealth recording in the same consultation
        // only AFTER both sources have been durably applied.
        client = null;
        resetMeters();
        if (mainRecord) mainRecord.disabled = false;
        if (message) message.textContent = 'Telehealth transcription completed. You may start another recording.';
        render();
      } else if (result?.status === 'failed') {
        sourceLoss = true;
        receipt.classList.add('is-interrupted');
        if (message) message.textContent = 'Telehealth transcription failed. Retry the retained source from the consultation controls.';
        render();
      }
    } catch (_) {
      // A failed status poll is not evidence that the ASR work completed.
    } finally {
      ownProcessingCheckInFlight = false;
    }
  };

  const sync = () => {
    const selected = getTranscriptId?.() || null;
    const unresolved = Boolean(restoredCapture) || Boolean(client
      && ['starting', 'recording', 'stopping', 'recovery_required', 'incomplete', 'sealed'].includes(client.state));
    if (boundTranscriptId !== selected && !unresolved) {
      // The prior capture has completed processing or was discarded. A new
      // consultation must never inherit the old session's enabled phone
      // state or disabled primary record button.
      boundTranscriptId = selected;
      enabled = false;
      client = null;
      restoredCapture = null;
      lastCheckedTranscriptId = null;
      stopPhase = null;
      diagnostics.clear();
      resetMeters();
      if (recordLabel) recordLabel.textContent = 'Start recording';
      if (mainRecord) mainRecord.disabled = false;
    }
    render();
    void checkPersistedCapture();
    void checkOwnTranscription();
  };
  render();
  void checkPersistedCapture();
  return {
    sync,
    isActive: () => enabled,
    isRecording: recordingInProgress,
    isUnresolved: () => Boolean(restoredCapture) || Boolean(client
      && ['starting', 'recording', 'stopping', 'recovery_required', 'incomplete', 'sealed'].includes(client.state)),
  };
}
