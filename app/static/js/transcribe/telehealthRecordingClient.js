import { createTelehealthCapture } from './telehealthCapture.js?v=20261104-telehealth-source';
import { createTelehealthWindows } from './telehealthWindows.js?v=20261104-telehealth-windows';
import { encodeTelehealthWavWindow } from './telehealthWav.js?v=20261105-telehealth-wav';

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
  onWarning = () => {}, mediaDevices, AudioContextCtor, AudioWorkletNodeCtor,
} = {}) {
  if (!transcriptId || typeof csrfFetch !== 'function') {
    throw new TypeError('A consultation and authorized request function are required.');
  }
  const base = '/api/v1/transcripts/' + encodeURIComponent(transcriptId) + '/telehealth/captures';
  let state = 'idle', capture = null, windows = null, captureId = null;
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

  const uploadWindow = async (window) => {
    if (!window._telehealthUpload) {
      // Bind identity to the WINDOW, not the HTTP attempt. If the server
      // accepted audio but a response was lost, retry uses the same key.
      window._telehealthUpload = {
        sequence: sequence[window.channel]++,
        key: globalThis.crypto.randomUUID(),
      };
    }
    const { sequence: partNo, key } = window._telehealthUpload;
    const currentId = await ready;
    if (!currentId) throw new Error('Recording session was not accepted.');
    const encoded = encodeTelehealthWavWindow(window);
    const data = new FormData();
    data.append('channel', window.channel);
    data.append('sequence_no', String(partNo));
    data.append('spans_json', JSON.stringify(encoded.spans));
    data.append('audio', new Blob([encoded.bytes], { type: 'audio/wav' }),
      'telehealth-' + window.channel + '-' + partNo + '.wav');
    const response = await csrfFetch(
      base + '/' + encodeURIComponent(currentId) + '/audio-parts',
      { method: 'POST', credentials: 'include',
        headers: { 'Idempotency-Key': key }, body: data },
    );
    await safeJson(response, 'Could not store one audio part.');
    accepted[window.channel] += 1;
  };

  const post = async (url, payload) => safeJson(await csrfFetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }), 'Could not update telehealth capture.');

  const start = async () => {
    if (state !== 'idle') throw new Error('Recording was already started.');
    state = 'starting';
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
      const { sampleRate } = await startingCapture;
      windows = createTelehealthWindows({
        sampleRate, windowSeconds: 10, maxPendingWindows: 4,
        onWindow: uploadWindow,
        onGap: (gap) => {
          if (gap.endFrame - gap.startFrame > sampleRate / 2) {
            warn(gap.channel, 'audio_clock_interrupted');
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
      state = 'recording';
      return started;
    } catch (error) {
      resolveSession(null);
      state = 'failed';
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
    state = 'stopping';
    stopPromise = (async () => {
      try { await capture.stop(); }
      catch (_) { warn(null, 'audio_flush_failed'); }
      const id = await ready;
      if (!id) {
        state = 'failed';
        throw new Error('The recording could not be stored on the server.');
      }
      const stopUrl = base + '/' + encodeURIComponent(id) + '/stop';
      await post(stopUrl, interruption || {});
      try {
        await windows.finish();
      } catch (error) {
        warn(null, 'upload_failed');
        await post(stopUrl, interruption);
        state = 'recovery_required';
        throw error; // Pending PCM remains in the bounded explicit retry queue.
      }
      const sealed = await post(base + '/' + encodeURIComponent(id) + '/seal', {
        expected_clinician_parts: accepted.clinician,
        expected_patient_parts: accepted.patient,
      });
      state = sealed.status === 'interrupted' ? 'incomplete' : 'sealed';
      return sealed;
    })();
    return stopPromise;
  };

  const retryPendingUploads = async () => {
    if (state !== 'recovery_required' || !windows || !captureId) {
      throw new Error('No pending telehealth audio upload can be retried.');
    }
    await windows.retryPending();
    const sealed = await post(base + '/' + encodeURIComponent(captureId) + '/seal', {
      expected_clinician_parts: accepted.clinician,
      expected_patient_parts: accepted.patient,
    });
    state = sealed.status === 'interrupted' ? 'incomplete' : 'sealed';
    return sealed;
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
    state = 'transcribing';
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
    start, stop, retryPendingUploads, acknowledgeIncomplete, transcribe, getStatus,
    get state() { return state; },
    get captureSessionId() { return captureId; },
    get uploadedParts() { return { ...accepted }; },
    get interruption() { return interruption; },
  };
}
