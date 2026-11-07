import { createTelehealthRecordingClient } from './telehealthRecordingClient.js?v=20261106-preselected-source';
import { prepareTelehealthDisplaySource } from './telehealthCapture.js?v=20261106-preselected-source';
import { createTelehealthDiagnostics } from './telehealthDiagnostics.js?v=20261106-server-events';
import {
  createTelehealthRecoveryJournal,
  createTelehealthUploadPacer,
  replayTelehealthRecoveryWindows,
} from './telehealthRecoveryJournal.js?v=20261105-cross-tab-journal';

// The recording bar is controlled only when Telehealth is explicitly selected.
// Existing microphone/Deepgram recording flows remain untouched otherwise.
const SOURCES = ['clinician', 'patient'];
const SPEECH_CONFIRM_MS = 3000;

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
  setClinicianMicLevel = () => {},
  resetClinicianMicLevel = () => {},
} = {}) {
  const toggle = document.querySelector('[data-telehealth-toggle]');
  const mainRecord = document.querySelector('[data-record-toggle]');
  const modeSelect = document.querySelector('[data-recording-mode-select]');
  const modeDropdown = document.querySelector('[data-record-mode-dropdown]');
  const modeOptions = [...document.querySelectorAll('[data-record-mode-menu] [data-mode]')];
  const uploadInput = document.querySelector('[data-audio-file-input]');
  const uploadAction = document.querySelector('[data-audio-action-trigger]');
  const uploadForm = document.querySelector('[data-upload-form]');
  const recordLabel = document.querySelector('[data-record-toggle-label]');
  const recordIcon = document.querySelector('[data-record-toggle-icon]');
  const receipt = document.querySelector('[data-telehealth-receipt]');
  const message = document.querySelector('[data-telehealth-receipt-message]');
  const setReceiptNotice = (text = '') => {
    if (!message) return;
    const visible = Boolean(text);
    message.textContent = visible ? text : '';
    message.hidden = !visible;
  };
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
    return { sync: () => {}, isActive: () => false, setOrdinaryControlsSync: () => {} };
  }

  let enabled = false;
  let preparedSource = null;
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
  let cancelSelection = null;
  let selectionEpoch = 0;
  let submissionInFlight = false;
  let stopPhase = null;
  let discardInFlight = false;
  let telehealthOwnsRecordLock = false;
  let sourceLoss = false;
  let telehealthMeterActive = false;
  let ordinaryControlsSync = () => {};
  const confirmed = { clinician: 0, patient: 0 };
  const status = { clinician: 'waiting', patient: 'waiting' };
  let lastClinicianRms = 0;
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

  const resetMeters = () => {
    confirmed.clinician = confirmed.patient = 0;
    status.clinician = status.patient = 'waiting';
    lastClinicianRms = 0;
    telehealthMeterActive = false;
    setReceiptNotice();
    resetClinicianMicLevel();
    receipt.classList.remove('is-verified', 'is-glowing', 'is-interrupted');
    for (const channel of SOURCES) {
      nodes[channel].root?.classList.remove('is-heard');
      if (nodes[channel].level) nodes[channel].level.style.width = '3%';
      if (nodes[channel].status) nodes[channel].status.textContent = 'Waiting for audio';
    }
  };
  const releasePreparedSource = () => {
    preparedSource?.release?.();
    preparedSource = null;
  };
  const setPreparedSourceState = (next) => {
    if (next === 'connected') {
      sourceLoss = false;
      setReceiptNotice();
      if (nodes.patient.status) nodes.patient.status.textContent = 'Tab connected';
    } else if (next === 'ended') {
      preparedSource = null;
      sourceLoss = true;
      if (nodes.patient.status) nodes.patient.status.textContent = 'Source ended';
      setReceiptNotice('The selected source ended. Turn Telehealth off and on to select it again.');
      render();
    } else if (next === 'muted' && nodes.patient.status) {
      nodes.patient.status.textContent = 'Tab audio paused';
    } else if (next === 'live' && nodes.patient.status) {
      nodes.patient.status.textContent = preparedSource ? 'Tab connected' : 'Waiting for audio';
    }
  };
  const render = () => {
    const unresolved = Boolean(restoredCapture) || Boolean(client
      && ['starting', 'recording', 'stopping', 'recovery_required', 'incomplete', 'sealed'].includes(client.state));
    const inputLocked = activating || enabled || unresolved || client?.state === 'transcribing';
    // Hidden-by-preference users must still be able to recover a capture
    // persisted before a tab crash. The phone control becomes visible only
    // while there is an unresolved server receipt or active local capture.
    toggle.hidden = getPreferences?.().show_telehealth_control === false
      && !restoredCapture && !enabled;
    toggle.setAttribute('aria-pressed', String(enabled));
    receipt.hidden = !enabled;
    toggle.disabled = activating || blockSwitch() || client?.state === 'transcribing' || !getTranscriptId?.();
    if (inputLocked) {
      if (modeSelect) modeSelect.disabled = true;
      if (modeDropdown) {
        modeDropdown.disabled = true;
        modeDropdown.setAttribute('aria-expanded', 'false');
      }
      if (modeOptions.length) {
        modeOptions.forEach((option) => { option.disabled = true; });
        const menu = document.querySelector('[data-record-mode-menu]');
        if (menu) menu.hidden = true;
      }
      if (uploadInput) uploadInput.disabled = true;
      if (uploadAction) uploadAction.disabled = true;
    }
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
        : 'Start recording';
      if (recordIcon) {
        recordIcon.dataset.lucide = activelyRecording ? 'square'
          : stoppedButSaving || submitting ? 'loader-circle' : 'disc';
        window.refreshLucideIcons?.(recordIcon.parentElement || recordIcon);
      }
    }
    // Native button accessibility state reflects the stop-save operation;
    // the receipt's live region announces each phase separately.
    mainRecord.setAttribute('aria-busy', String(Boolean(
      client?.state === 'stopping'
      || (client?.state === 'sealed' && stopPhase === 'submitting')
    )));
    const actionsInFlight = submissionInFlight || discardInFlight;
    const recordBlocked = activating || actionsInFlight || Boolean(restoredCapture)
      || Boolean(client && ['stopping', 'recovery_required', 'incomplete', 'sealed', 'transcribing'].includes(client.state))
      || Boolean(enabled && !client && !preparedSource?.isLive?.());
    if ((enabled || activating) && recordBlocked) {
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
          ordinaryControlsSync();
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
      setReceiptNotice(saved.status === 'processing'
        ? ''
        : saved.status === 'ready'
          ? 'Sealed Telehealth audio is awaiting transcription. Review the stored parts and submit them.'
          : saved.parts?.some((part) => part.ingestion_job_id)
            ? 'A Telehealth transcription failed. Retry the retained source audio using the consultation recovery controls.'
            : 'The Telehealth recording ended unexpectedly. Encrypted unacknowledged audio may be recoverable on this browser; the final unflushed seconds might be missing. Recover or discard explicitly.');
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
      if (nodes[channel].status) nodes[channel].status.textContent = 'Source muted';
      if (channel === 'clinician') resetClinicianMicLevel();
    } else if (next === 'live' && confirmed[channel] < SPEECH_CONFIRM_MS) {
      if (nodes[channel].status) nodes[channel].status.textContent = 'Waiting for voice';
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
    if (next === 'loading' && nodes[channel].status) nodes[channel].status.textContent = 'Checking speech…';
    if (next === 'ready' && confirmed[channel] < SPEECH_CONFIRM_MS) {
      if (nodes[channel].status) nodes[channel].status.textContent = 'Waiting for speech';
    }
    if (next === 'unavailable') {
      if (nodes[channel].status) nodes[channel].status.textContent = 'Speech check unavailable — audio still recording';
    }
  };
  const onSpeechFrame = ({ channel, speechMs }) => {
    if (!telehealthMeterActive || !SOURCES.includes(channel) || sourceLoss || status[channel] === 'muted'
      || !Number.isFinite(speechMs) || speechMs <= 0) return;
    if (confirmed[channel] >= SPEECH_CONFIRM_MS) return;
    confirmed[channel] = Math.min(SPEECH_CONFIRM_MS, confirmed[channel] + speechMs);
    if (confirmed[channel] < SPEECH_CONFIRM_MS) return;
    onDiagnostic({ event: 'vad_speech_confirmed', channel });
    if (channel === 'clinician') {
      setClinicianMicLevel(lastClinicianRms, true);
    }
    nodes[channel].root?.classList.add('is-heard');
    if (nodes[channel].status) nodes[channel].status.textContent = 'Speech detected';
    if (channel === 'patient' && !receipt.classList.contains('is-verified')) {
      receipt.classList.add('is-verified', 'is-glowing');
      window.setTimeout(() => {
        if (!enabled || sourceLoss) return;
        receipt.classList.remove('is-glowing');
      }, 650);
    }
  };
  const onLevel = ({ channel, rms }) => {
    if (!telehealthMeterActive || !SOURCES.includes(channel) || sourceLoss || status[channel] === 'muted') return;
    // RMS drives the animated meter ONLY. Speech confirmation uses Silero VAD.
    const level = Math.min(100, Math.max(3, Math.round(rms * 420)));
    if (channel === 'clinician') {
      lastClinicianRms = rms;
      setClinicianMicLevel(rms, confirmed.clinician >= SPEECH_CONFIRM_MS);
    } else if (nodes.patient.level) {
      nodes.patient.level.style.width = level + '%';
    }
  };
  const onWarning = ({ channel, failure_code: reason, recoverable = false }) => {
    if (!recoverable) {
      sourceLoss = true;
      telehealthMeterActive = false;
      receipt.classList.add('is-interrupted');
      resetClinicianMicLevel();
    }
    setReceiptNotice(recoverable
      ? 'Audio upload could not be confirmed. Retry the retained audio.'
      : 'Telehealth audio may be incomplete. Stop and review before transcribing.');
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
    let chosenSource = null;
    const epoch = ++selectionEpoch;
    const finish = (answer) => {
      if (settled) {
        answer?.release?.();
        return;
      }
      settled = true;
      if (!answer) {
        if (selectionEpoch === epoch) selectionEpoch += 1;
        chosenSource?.release?.();
        diagnostics.clear();
      } else {
        preparedSource = answer;
      }
      cancelSelection = null;
      guidance.removeEventListener('close', onClose);
      guidanceContinue.removeEventListener('click', onContinue);
      guidanceCancel.removeEventListener('click', onCancel);
      guidance.close();
      guidanceContinue.disabled = false;
      resolve(answer);
    };
    const onClose = () => finish(false);
    const onCancel = () => finish(false);
    const onContinue = () => {
      chosenSource = null;
      guidanceContinue.disabled = true;
      guidanceError.hidden = true;
      // Invoke getDisplayMedia synchronously from this consent click. The
      // server acknowledgement may complete later without losing activation.
      diagnostics.begin(getTranscriptId());
      if (debugReference) debugReference.textContent = diagnostics.diagnosticId || 'Unavailable';
      if (debugEvents) debugEvents.textContent = '';
      let selection;
      let acknowledgement;
      try {
        selection = prepareTelehealthDisplaySource({
          onDiagnostic: (event) => { if (selectionEpoch === epoch) onDiagnostic(event); },
          onState: (next) => { if (selectionEpoch === epoch) setPreparedSourceState(next); },
          confirmWindowAudio: () => window.confirm(
            'Window audio is experimental. Depending on your browser and operating system, sharing a window can capture ALL system audio, not just the call. Continue only if other application sounds are safe to record. Continue?'
          ),
        }).then((source) => {
          chosenSource = source;
          if (selectionEpoch !== epoch || settled) {
            source.release();
            throw new Error('Call-tab selection was cancelled.');
          }
          return source;
        });
        acknowledgement = Promise.resolve().then(() => (
          getPreferences?.().telehealth_guidance_acknowledged === true
            ? undefined : persistPreferences({ telehealth_guidance_acknowledged: true })
        ));
      } catch (error) {
        diagnostics.clear();
        guidanceContinue.disabled = false;
        guidanceError.textContent = error instanceof Error && error.message
          ? error.message : 'Could not start call-tab selection. Try again.';
        guidanceError.hidden = false;
        return;
      }
      cancelSelection = () => finish(false);
      Promise.all([selection, acknowledgement]).then(([source]) => {
        if (!source.isLive()) throw new Error('The selected source ended. Select the call tab again.');
        finish(source);
      }).catch(async (error) => {
        if (settled) return;
        if (!chosenSource) {
          try { chosenSource = await selection; } catch (_) {}
        }
        chosenSource?.release?.();
        if (selectionEpoch !== epoch) return;
        diagnostics.clear();
        if (debugReference) debugReference.textContent = 'Unavailable';
        guidanceError.textContent = error instanceof Error && error.message
          ? error.message : 'Could not save your acknowledgement or select the call tab. Try again.';
        guidanceError.hidden = false;
      }).finally(() => {
        if (!settled) guidanceContinue.disabled = false;
      });
    };
    cancelSelection = () => finish(false);
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
    if (!preparedSource?.isLive?.()) {
      releasePreparedSource();
      enabled = false;
      resetMeters();
      render();
      ordinaryControlsSync();
      notice('The selected source ended. Enable Telehealth again and select the call tab.', 'warning');
      return;
    }
    const selectedSource = preparedSource;
    preparedSource = null; // Ownership passes to the recording client below.
    try {
      resetMeters();
      if (nodes.patient.status) nodes.patient.status.textContent = 'Tab connected';
      stopPhase = null;
      telehealthMeterActive = true;
      if (debugReference) debugReference.textContent = diagnostics.diagnosticId || 'Unavailable';
      if (debugEvents) debugEvents.textContent = '';
      sourceLoss = false;
      setReceiptNotice();
      client = createTelehealthRecordingClient({
        transcriptId: getTranscriptId(),
        csrfFetch, onLevel, onSourceState, onWarning,
        onStateChange: (next) => {
          if (['stopping', 'sealed', 'transcribing', 'completed', 'failed', 'discarded', 'incomplete', 'recovery_required'].includes(next)) {
            telehealthMeterActive = false;
            resetClinicianMicLevel();
          }
          render();
        },
        onSpeechFrame, onVadState, onDiagnostic,
        onStopProgress: (phase) => {
          stopPhase = phase;
          render();
        },
        preparedSource: selectedSource,
        recoveryJournal,
      });
      const attempt = client.start(); // Start creates microphone/audio capture; no picker is opened here.
      render();
      await attempt;
    } catch (error) {
      selectedSource.release();
      telehealthMeterActive = false;
      resetClinicianMicLevel();
      notice((error instanceof Error ? error.message : 'Telehealth recording could not start.') + diagnosticSuffix(), 'error');
      client = null;
      enabled = false;
      diagnostics.clear();
      resetMeters();
      render();
      ordinaryControlsSync();
    }
  };

  const stopRecording = async () => {
    if (!client || submissionInFlight || discardInFlight) return;
    telehealthMeterActive = false;
    resetClinicianMicLevel();
    try {
      await withSubmission(async () => {
        const result = await client.stop();
        render();
        if (result.status === 'interrupted') {
          notice('Audio was interrupted. Review and acknowledge the incomplete recording before transcription.', 'warning');
          return;
        }
        stopPhase = 'submitting';
        setReceiptNotice();
        render();
        await client.transcribe();
        setReceiptNotice();
        notice('Both Telehealth audio sources were sent for transcription.', 'success');
        enabled = false;
        diagnostics.clear();
        resetMeters();
        await refreshWorkspace?.();
        render();
        ordinaryControlsSync();
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
    if (activating || blockSwitch() || client?.state === 'transcribing') return;
    if (!enabled && (modeSelect?.dataset.modeChangeInFlight === 'true'
      || uploadForm?.dataset.uploadInFlight === 'true')) {
      notice('Finish the current recording-mode change or audio upload before selecting Telehealth.', 'warning');
      return;
    }
    if (enabled) {
      enabled = false;
      releasePreparedSource();
      diagnostics.clear();
      stopPhase = null;
      resetMeters();
      sourceLoss = false;
      render();
      ordinaryControlsSync();
      return;
    }
    if (!getTranscriptId?.()) {
      notice('Create or select a consultation first.', 'warning');
      return;
    }
    activating = true;
    render();
    const activationTranscriptId = getTranscriptId();
    try {
      await switchToRecordedMode();
      if (getTranscriptId() !== activationTranscriptId) throw new Error('The consultation changed. Select Telehealth again.');
      preparedSource = await confirmGuidance();
      if (!preparedSource) return;
      if (getTranscriptId() !== activationTranscriptId) {
        releasePreparedSource();
        throw new Error('The consultation changed. Select Telehealth again.');
      }
      if (!preparedSource.isLive()) {
        releasePreparedSource();
        throw new Error('The selected source ended. Enable Telehealth again and select the call tab.');
      }
      enabled = true;
      client = null;
      stopPhase = null;
      resetMeters();
      if (nodes.patient.status) nodes.patient.status.textContent = 'Tab connected';
    } catch (error) {
      notice(error instanceof Error ? error.message : 'Telehealth could not be enabled.', 'error');
    } finally {
      activating = false;
      render();
      ordinaryControlsSync();
    }
  };

  toggle.addEventListener('click', () => { void toggleTelehealth(); });
  uploadInput?.addEventListener('change', (event) => {
    const unresolved = Boolean(restoredCapture) || Boolean(client
      && ['starting', 'recording', 'stopping', 'recovery_required', 'incomplete', 'sealed'].includes(client.state));
    if (!activating && !enabled && !unresolved && client?.state !== 'transcribing') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    uploadInput.value = '';
    notice('Turn off Telehealth before selecting an audio file.', 'warning');
  }, true);
  // Capture phase: prevent the pre-existing microphone recorder from
  // responding to the SAME click while this separate capture is selected.
  mainRecord.addEventListener('click', (event) => {
    if (!enabled && !activating) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (activating) return;
    if (submissionInFlight || discardInFlight) return;
    if (recordingInProgress()) {
      if (client?.state !== 'stopping') void stopRecording();
    } else if (!client || ['discarded'].includes(client.state)) {
      void startRecording();
    }
  }, true);
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
      setReceiptNotice();
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
      setReceiptNotice();
      notice('Telehealth transcription was submitted.', 'success');
      await refreshWorkspace?.();
      render();
      ordinaryControlsSync();
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
      ordinaryControlsSync();
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
        const result = await client.transcribe();
        setReceiptNotice();
        return result;
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
        setReceiptNotice();
        notice('Encrypted local windows were reconciled with saved Telehealth audio. Transcription is processing; review for missing final speech.', 'warning');
        await refreshWorkspace?.();
        render();
        ordinaryControlsSync();
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
  window.addEventListener('pagehide', () => {
    cancelSelection?.();
    if (preparedSource) {
      releasePreparedSource();
      enabled = false;
      resetMeters();
      render();
      ordinaryControlsSync();
    }
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
        enabled = false;
        diagnostics.clear();
        resetMeters();
        if (mainRecord) mainRecord.disabled = false;
        setReceiptNotice();
        render();
        ordinaryControlsSync();
      } else if (result?.status === 'failed') {
        sourceLoss = true;
        receipt.classList.add('is-interrupted');
        setReceiptNotice('Telehealth transcription failed. Retry the retained source from the consultation controls.');
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
      cancelSelection?.();
      releasePreparedSource();
      enabled = false;
      client = null;
      restoredCapture = null;
      lastCheckedTranscriptId = null;
      stopPhase = null;
      diagnostics.clear();
      resetMeters();
      if (recordLabel) recordLabel.textContent = 'Start recording';
      if (mainRecord) mainRecord.disabled = false;
      ordinaryControlsSync();
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
    isSelectedOrUnresolved: () => Boolean(activating || enabled || restoredCapture || (client
      && ['starting', 'recording', 'stopping', 'recovery_required', 'incomplete', 'sealed', 'transcribing'].includes(client.state))),
    setOrdinaryControlsSync: (callback) => { ordinaryControlsSync = callback || (() => {}); },
    isRecording: recordingInProgress,
    isUnresolved: () => Boolean(restoredCapture) || Boolean(client
      && ['starting', 'recording', 'stopping', 'recovery_required', 'incomplete', 'sealed'].includes(client.state)),
  };
}
