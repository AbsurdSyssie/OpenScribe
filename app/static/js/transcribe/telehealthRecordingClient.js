import { createTelehealthCapture } from './telehealthCapture.js?v=20261104-telehealth-source';
import { createTelehealthWindows } from './telehealthWindows.js?v=20261104-telehealth-windows';
import { encodeTelehealthWavWindow } from './telehealthWav.js?v=20261105-telehealth-wav';
import {
  createTelehealthRecoveryJournal,
  createTelehealthUploadPacer,
  submitTelehealthRecoveryWindow,
} from './telehealthRecoveryJournal.js?v=20261105-crash-recovery-1';

// Telehealth recorded-upload v1. Each track is submitted independently;
// silence is retained in the uploaded audio, while the original context-frame
// clock remains stable across both source streams. The caller owns consent UX.
const CHANNELS = ['clinician', 'patient'];
const MAX_EARLY_FRAMES = 96000;

function normalizeCaptureFailure(reason) {
  const mapped = {
    ended: 'source_ended',
    audio_clock_interrupted: 'audio_clock_interrupted',
    audio_backpressure: 'audio_backpressure',
    processor_failed: 'audio_processor_failed',
    invalid_frames: 'audio_processor_failed',
    consumer_failed: 'audio_backpressure',
    audio_flush_timeout: 'upload_failed',
    audio_flush_failed: 'upload_failed',
  };
  return mapped[reason] || 'browser_capture_failed';
}

function frameLevel(samples) {
  let squared = 0;
  for (const sample of samples) squared += sample * sample;
  return Math.sqrt(squared / samples.length);
}

async function safeJson(response, fallback) {
  if (!response.ok) {
    let payload;
    try { payload = await response.json(); } catch (_) {}
    throw new Error(payload?.error?.message || fallback);
  }
  return response.json();
}

export function createTelehealthRecordingClient({
  transcriptId, csrfFetch, onLevel = () => {}, onSourceState = () => {},
  onWarning = () => {}, onStateChange = () => {}, mediaDevices, AudioContextCtor, AudioWorkletNodeCtor,
  recoveryJournal = null, pacedUpload = null,
} = {}) {
  if (!transcriptId || typeof csrfFetch !== 'function') {
    throw new TypeError('A consultation and authorized request function are required.');
  }
  const base = '/api/v1/transcripts/' + encodeURIComponent(transcriptId) + '/telehealth/captures';
  const journal = recoveryJournal || createTelehealthRecoveryJournal();
  const pace = pacedUpload || createTelehealthUploadPacer();
  const acknowledged = { clinician: new Set(), patient: new Set() };
  let state = 'idle', capture = null, windows = null, captureId = null;
  const setState = (next) => {
    state = next;
    try { onStateChange(next); } catch (_) {}
  };
  let stopPromise = null, interruption = null, resolveSessionReady = null;
  const ready = new Promise((resolve) => { resolveSessionReady = resolve; });
  let readyResolved = false, earlyFrames = 0;
  const earlyPackets = [];
  const sequence = { clinician: 0, patient: 0 };
  const accepted = { clinician: 0, patient: 0 };

  const resolveSession = (id) => {
    if (readyResolved) return;
    readyResolved = true;
    resolveSessionReady(id);
  };
  const warn = (channel, reason) => {
    if (!interruption) interruption = {
      channel: CHANNELS.includes(channel) ? channel : null,
      failure_code: normalizeCaptureFailure(reason),
    };
    try { onWarning(interruption); } catch (_) {}
  };
  const acceptFrames = (packet) => {
    try {
      onLevel({
        channel: packet.channel, startFrame: packet.startFrame,
        endFrame: packet.endFrame, sampleRate: packet.sampleRate,
        rms: frameLevel(packet.samples),
      });
    } catch (_) {}
    if (!windows) {
      if (earlyFrames + packet.samples.length > MAX_EARLY_FRAMES) {
        warn(packet.channel, 'audio_backpressure');
        return false;
      }
      earlyPackets.push({ ...packet, samples: packet.samples.slice() });
      earlyFrames += packet.samples.length;
      return true;
    }
    const acceptedNow = windows.ingest(packet);
    if (!acceptedNow) warn(packet.channel, 'audio_backpressure');
    return acceptedNow;
  };

  const prepareWindow = (window) => {
    if (window._telehealthPrepared) return window._telehealthPrepared;
    if (!window._telehealthUpload) {
      // Allocate once at WINDOW CREATION, never per HTTP attempt.
      window._telehealthUpload = {
        sequence: sequence[window.channel]++,
        key: globalThis.crypto.randomUUID(),
      };
    }
    const { sequence: partNo, key } = window._telehealthUpload;
    window._telehealthPrepared = (async () => {
      const currentId = await ready;
      if (!currentId) throw new Error('Recording session was not accepted.');
      const encoded = encodeTelehealthWavWindow(window);
      const part = {
        captureId: currentId, transcriptId, channel: window.channel,
        sequence: partNo, key, spans: encoded.spans, bytes: encoded.bytes,
      };
      // Every bounded window gets an encrypted IndexedDB transaction
      // immediately, EVEN WHEN an earlier HTTP request is still pending.
      // A lost response cannot change its bytes, role, sequence or identity.
      try {
        await journal.persist(part);
      } catch (error) {
        warn(null, 'browser_capture_failed');
        void stop().catch(() => {});
        throw error;
      }
      return part;
    })();
    // An independently queued durable promise can reject while a different
    // window's HTTP request is pending. The upload queue awaits the same
    // promise and surfaces the error at its original sequence position.
    void window._telehealthPrepared.catch(() => {});
    return window._telehealthPrepared;
  };

  const uploadWindow = async (window) => {
    const part = await (window._telehealthPrepared || prepareWindow(window));
    await submitTelehealthRecoveryWindow({ part, csrfFetch, pace });
    await journal.remove(part); // Only delete after matching server acceptance.
    if (!acknowledged[part.channel].has(part.sequence)) {
      acknowledged[part.channel].add(part.sequence);
      accepted[part.channel] += 1;
    }
    part.bytes.fill(0);
  };

  const post = async (url, payload) => safeJson(await csrfFetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }), 'Could not update telehealth capture.');

  const start = async () => {
    if (state !== 'idle') throw new Error('Recording was already started.');
    setState('starting');
    capture = createTelehealthCapture({
      mediaDevices, AudioContextCtor, AudioWorkletNodeCtor,
      onFrames: acceptFrames, onSourceState,
      onInterrupted: ({ channel, reason }) => {
        warn(channel, reason);
        void stop().catch(() => {});
      },
    });
    try {
      // This MUST be called directly in the Start click; display capture
      // consumes transient user activation before any awaiting server work.
      const startingCapture = capture.start();
      // Start the local storage preflight while the user chooses a call tab.
      // The display picker MUST be invoked first to preserve its user gesture.
      const [{ sampleRate }] = await Promise.all([
        startingCapture, journal.check(),
      ]);
      windows = createTelehealthWindows({
        sampleRate, windowSeconds: 10, maxPendingWindows: 4,
        onWindow: uploadWindow, onEnqueue: prepareWindow,
        onGap: (gap) => {
          // No time loss is acceptable to silently label as complete.
          // Even a sub-second gap could omit a medication/dose/negation.
          // Keep the already captured parts, stop, and require explicit
          // clinician review of the incomplete audio before transcription.
          if (gap.endFrame > gap.startFrame) {
            warn(gap.channel, 'audio_clock_interrupted');
            void stop().catch(() => {});
          }
        },
      });
      for (const packet of earlyPackets.splice(0)) {
        if (!windows.ingest(packet)) {
          warn(packet.channel, 'audio_backpressure');
          throw new Error('Initial telehealth audio buffer was not accepted.');
        }
      }
      earlyFrames = 0;
      const started = await post(base, { timeline_sample_rate: sampleRate });
      captureId = started.capture_session_id;
      resolveSession(captureId);
      // A track may have ended while the start request was in flight. Never
      // turn a stopped or interrupted capture back into a running one.
      if (state === 'starting') setState('recording');
      return started;
    } catch (error) {
      resolveSession(null);
      setState('failed');
      warn(null, 'browser_capture_failed');
      try { await capture.stop(); } catch (_) {}
      throw error;
    }
  };

  const stop = () => {
    if (stopPromise) return stopPromise;
    if (!capture || !['starting', 'recording'].includes(state)) {
      return Promise.reject(new Error('No active telehealth recording.'));
    }
    setState('stopping');
    stopPromise = (async () => {
      try { await capture.stop(); }
      catch (_) { warn(null, 'audio_flush_failed'); }
      const id = await ready;
      if (!id) {
        setState('failed');
        throw new Error('The recording could not be stored on the server.');
      }
      const stopUrl = base + '/' + encodeURIComponent(id) + '/stop';
      try {
        await post(stopUrl, interruption || {});
      } catch (error) {
        setState('recovery_required');
        throw error;
      }
      try {
        await windows.finish();
      } catch (error) {
        // The original audio is still in the bounded retry queue. A transient
        // HTTP failure is NOT a missing-audio interval. Do not make the server
        // permanently mark the consultation incomplete if exact replay fixes it.
        try {
          onWarning({ channel: null, failure_code: 'upload_failed', recoverable: true });
        } catch (_) {}
        setState('recovery_required');
        throw error; // Pending PCM remains available for exact-key replay.
      }
      let sealed;
      try {
        sealed = await post(base + '/' + encodeURIComponent(id) + '/seal', {
          expected_clinician_parts: accepted.clinician,
          expected_patient_parts: accepted.patient,
        });
      } catch (error) {
        setState('recovery_required');
        throw error;
      }
      setState(sealed.status === 'interrupted' ? 'incomplete' : 'sealed');
      return sealed;
    })();
    return stopPromise;
  };

  const retryPendingUploads = async () => {
    if (state !== 'recovery_required' || !windows || !captureId) {
      throw new Error('No pending telehealth audio upload can be retried.');
    }
    await post(base + '/' + encodeURIComponent(captureId) + '/stop', interruption || {});
    if (windows.status().finished) await windows.retryPending();
    else await windows.finish();
    const sealed = await post(base + '/' + encodeURIComponent(captureId) + '/seal', {
      expected_clinician_parts: accepted.clinician,
      expected_patient_parts: accepted.patient,
    });
    setState(sealed.status === 'interrupted' ? 'incomplete' : 'sealed');
    return sealed;
  };
  const discard = async () => {
    if (!captureId || !['recovery_required', 'incomplete', 'sealed'].includes(state)) {
      throw new Error('Only stopped and unsubmitted Telehealth audio can be discarded.');
    }
    const discarded = await post(
      base + '/' + encodeURIComponent(captureId) + '/discard', {},
    );
    if (discarded.status !== 'discarded') {
      throw new Error('The capture discard could not be verified.');
    }
    await journal.clear(captureId, transcriptId);
    windows = null;
    setState('discarded');
    return discarded;
  };
  const acknowledgeIncomplete = async () => {
    if (state !== 'incomplete' || !captureId) {
      throw new Error('No incomplete recording awaits clinician acknowledgement.');
    }
    return post(base + '/' + encodeURIComponent(captureId) + '/acknowledge-incomplete', {});
  };
  const transcribe = async () => {
    if (!captureId || !['incomplete', 'sealed'].includes(state)) {
      throw new Error('Stop and seal the source audio before transcription.');
    }
    const queued = await post(
      base + '/' + encodeURIComponent(captureId) + '/transcribe', {},
    );
    setState('transcribing');
    return queued;
  };
  const getStatus = async () => {
    if (!captureId) return null;
    const response = await csrfFetch(
      base + '/' + encodeURIComponent(captureId),
      { credentials: 'include' },
    );
    return safeJson(response, 'Could not load telehealth capture status.');
  };
  return {
    start, stop, retryPendingUploads, acknowledgeIncomplete, discard, transcribe, getStatus,
    get state() { return state; },
    get captureSessionId() { return captureId; },
    get uploadedParts() { return { ...accepted }; },
    get interruption() { return interruption; },
    get recoveryJournal() { return journal; },
  };
}
