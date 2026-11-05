import { createTelehealthRecordingClient } from './telehealthRecordingClient.js?v=20261105-telehealth-recording';

// The recording bar is controlled only when Telehealth is explicitly selected.
// Existing microphone/Deepgram recording flows remain untouched otherwise.
const SOURCES = ['clinician', 'patient'];
const SPEECH_CONFIRM_MS = 5000;
const AUDIO_ACTIVITY_THRESHOLD = 0.018;

export function attachTelehealthControls({
  getTranscriptId,
  getIngestionMode,
  isOtherRecordingActive,
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
  const discard = document.querySelector('[data-telehealth-discard]');
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
  let lastCheckedTranscriptId = null;
  let boundTranscriptId = getTranscriptId?.() || null;
  let activating = false;
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

  const notice = (text, kind = 'info') => {
    if (typeof showNotice === 'function') showNotice(text, kind);
  };
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
    toggle.setAttribute('aria-pressed', String(enabled));
    receipt.hidden = !enabled;
    toggle.disabled = activating || blockSwitch() || !getTranscriptId?.();
    if (enabled && recordLabel) {
      recordLabel.textContent = recordingInProgress() ? 'Stop' :
        restoredCapture ? 'Resolve telehealth' :
        client?.state === 'transcribing' ? 'Transcribing…' : 'Start telehealth';
      if (recordIcon) {
        recordIcon.dataset.lucide = recordingInProgress() ? 'square' : 'disc';
        window.refreshLucideIcons?.(recordIcon.parentElement || recordIcon);
      }
      if (client?.state === 'transcribing' || restoredCapture) mainRecord.disabled = true;
    }
    if (retry) retry.hidden = client?.state !== 'recovery_required';
    const hasRestoredJob = restoredCapture?.parts?.some((part) => part.ingestion_job_id);
    const canRestorePartial = restoredCapture
      && ['active', 'stopping', 'ready', 'interrupted'].includes(restoredCapture.status)
      && restoredCapture.parts?.some((part) => part.status === 'staged');
    if (partial) {
      partial.hidden = client?.state !== 'incomplete' && !canRestorePartial;
      partial.textContent = canRestorePartial ? 'Recover stored audio' : 'Acknowledge and transcribe';
    }
    if (discard) discard.hidden = !(
      ['recovery_required', 'incomplete', 'sealed'].includes(client?.state)
      || (restoredCapture && !hasRestoredJob)
    );
    if (actions) actions.hidden = [retry, partial, discard].every((item) => item?.hidden !== false);
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
    if (!id || id === lastCheckedTranscriptId || enabled || client) return;
    lastCheckedTranscriptId = id;
    try {
      const response = await csrfFetch(
        '/api/v1/transcripts/' + encodeURIComponent(id) + '/telehealth/unresolved',
        { credentials: 'include' },
      );
      if (!response.ok) return;
      const saved = await response.json();
      if (id !== getTranscriptId?.() || enabled || client || !saved) return;
      if (!['active', 'stopping', 'ready', 'interrupted', 'failed'].includes(saved.status)) return;
      restoredCapture = saved;
      enabled = true;
      sourceLoss = true;
      resetMeters();
      receipt.classList.add('is-interrupted');
      if (message) message.textContent = saved.parts?.some((part) => part.ingestion_job_id)
        ? 'A Telehealth transcription failed. Retry the retained source audio using the consultation recovery controls.'
        : 'An earlier Telehealth recording is unresolved. Unuploaded browser audio cannot be recovered after a reload. Review retained parts or discard explicitly.';
      render();
    } catch (_) {
      // Do not infer that the remote source is complete from a failed check.
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
  const onLevel = ({ channel, rms, startFrame, endFrame, sampleRate }) => {
    if (!SOURCES.includes(channel) || sourceLoss) return;
    if (status[channel] === 'muted') return;
    const level = Math.min(100, Math.max(3, Math.round(rms * 420)));
    lastLevel[channel] = level;
    if (nodes[channel].level) nodes[channel].level.style.width = level + '%';
    if (combinedLevel) combinedLevel.style.width = Math.max(...SOURCES.map((id) => lastLevel[id])) + '%';
    // An audio-level receipt is a UI heuristic, not clinical speaker proof or
    // a signal to delete silence. Both PCM streams were recorded from Start.
    if (rms > AUDIO_ACTIVITY_THRESHOLD && confirmed[channel] < SPEECH_CONFIRM_MS) {
      const millis = (endFrame - startFrame) * 1000 / sampleRate;
      if (Number.isFinite(millis) && millis > 0) confirmed[channel] = Math.min(
        SPEECH_CONFIRM_MS, confirmed[channel] + millis,
      );
    }
    if (confirmed[channel] >= SPEECH_CONFIRM_MS) {
      nodes[channel].root?.classList.add('is-heard');
      if (nodes[channel].status) nodes[channel].status.textContent = 'Audio detected';
    }
    if (SOURCES.every((id) => confirmed[id] >= SPEECH_CONFIRM_MS)
      && !receipt.classList.contains('is-verified')) {
      receipt.classList.add('is-verified', 'is-glowing');
      if (message) message.textContent = 'Both audio sources detected';
      collapseTimer = window.setTimeout(() => {
        if (!enabled || sourceLoss) return;
        receipt.classList.remove('is-glowing');
        collapsed = true;
        channels.hidden = true;
        combined.hidden = false;
      }, 650);
    }
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
    if (getPreferences?.().telehealth_guidance_acknowledged === true) {
      resolve(true);
      return;
    }
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
        await persistPreferences({ telehealth_guidance_acknowledged: true });
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
    if (client?.state === 'transcribing') {
      notice('The consultation is still being transcribed.', 'info');
      return;
    }
    if (getIngestionMode?.() !== 'whole_file') {
      notice('Select Recorded upload before starting Telehealth.', 'warning');
      return;
    }
    resetMeters();
    sourceLoss = false;
    if (message) message.textContent = 'Checking both audio sources';
    client = createTelehealthRecordingClient({
      transcriptId: getTranscriptId(),
      csrfFetch, onLevel, onSourceState, onWarning, onStateChange: render,
    });
    const attempt = client.start(); // Direct user activation -> getDisplayMedia.
    render();
    try {
      await attempt;
    } catch (error) {
      notice(error instanceof Error ? error.message : 'Telehealth recording could not start.', 'error');
      client = null;
      render();
    }
  };

  const stopRecording = async () => {
    if (!client) return;
    try {
      const result = await client.stop();
      render();
      if (result.status === 'interrupted') {
        notice('Audio was interrupted. Review and acknowledge the incomplete recording before transcription.', 'warning');
        return;
      }
      await client.transcribe();
      notice('Both Telehealth audio sources were sent for transcription.', 'success');
      await refreshWorkspace?.();
    } catch (error) {
      notice(error instanceof Error ? error.message : 'Telehealth audio needs recovery.', 'error');
      render();
    }
  };

  const toggleTelehealth = async () => {
    if (activating || blockSwitch()) return;
    if (enabled) {
      enabled = false;
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
    if (recordingInProgress()) {
      if (client?.state !== 'stopping') void stopRecording();
    } else if (!client || ['discarded'].includes(client.state)) {
      void startRecording();
    }
  }, true);
  combined?.addEventListener('click', () => expand(true));
  retry?.addEventListener('click', () => {
    if (!client) return;
    void client.retryPendingUploads().then(async (result) => {
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
      render();
    });
  });
  discard?.addEventListener('click', () => {
    if ((!client && !restoredCapture) || !window.confirm(
      'Permanently discard this unsubmitted recording? Clinician and patient audio staged so far will be queued for deletion. No transcription will be created.',
    )) return;
    discard.disabled = true;
    const discardRequest = client ? client.discard() : postRecovery(restoredCapture, 'discard');
    void discardRequest.then(async (result) => {
      if (result.status !== 'discarded') throw new Error('Discard was not confirmed by the server.');
      restoredCapture = null;
      resetMeters();
      enabled = false;
      notice('Unsubmitted Telehealth audio was discarded.', 'info');
      await refreshWorkspace?.();
      render();
    }).catch((error) => {
      notice(error instanceof Error ? error.message : 'Could not discard the recording.', 'error');
      render();
    }).finally(() => { discard.disabled = false; });
  });
  partial?.addEventListener('click', () => {
    if ((!client && !restoredCapture) || !window.confirm(
      'This recorded consultation may be incomplete. Unuploaded audio cannot be recovered after a browser reload. Review the transcript for missing clinical information before using generated notes. Continue?',
    )) return;
    partial.disabled = true;
    const continueCapture = async () => {
      if (client) {
        await client.acknowledgeIncomplete();
        return client.transcribe();
      }
      const saved = restoredCapture;
      if (['active', 'stopping'].includes(saved.status)) {
        const stopDetails = saved.failure_code
          ? { failure_code: saved.failure_code, interrupted_channel: saved.failure_channel }
          : { failure_code: 'browser_capture_failed' };
        await postRecovery(saved, 'stop', stopDetails);
        const counts = (channel) => saved.parts.filter((part) => part.channel === channel).length;
        await postRecovery(saved, 'seal', {
          expected_clinician_parts: counts('clinician'),
          expected_patient_parts: counts('patient'),
        });
      }
      if (saved.status !== 'ready') await postRecovery(saved, 'acknowledge-incomplete');
      const queued = await postRecovery(saved, 'transcribe');
      restoredCapture = null;
      enabled = false;
      return queued;
    };
    void continueCapture().then(async () => {
      notice('Recovered Telehealth audio was submitted for transcription. Review for omissions.', 'warning');
      await refreshWorkspace?.();
      render();
    }).catch((error) => {
      notice(error instanceof Error ? error.message : 'Could not recover the saved recording.', 'error');
      render();
    }).finally(() => { partial.disabled = false; });
  });
  window.addEventListener('beforeunload', (event) => {
    if (!blockSwitch()) return;
    event.preventDefault();
    event.returnValue = '';
  });
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
      resetMeters();
      if (recordLabel) recordLabel.textContent = 'Start recording';
      if (mainRecord) mainRecord.disabled = false;
    }
    render();
    void checkPersistedCapture();
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
