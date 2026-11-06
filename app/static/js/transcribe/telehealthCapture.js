import { createTelehealthTimeline } from './telehealthTimeline.js?v=20261104-telehealth-timing';
import { createTelehealthVadMonitor } from './telehealthVad.js?v=20261106-speech-vad';

// This is a capture transport, NOT a transcript uploader. The consumer must
// immediately pass onFrames into a bounded VAD/ingestion pipeline. Raw PCM is
// neither logged nor retained in browser storage by this controller.
const WORKLET_URL = '/static/js/transcribe/telehealthSourceWorklet.js?v=20261104-telehealth-source';

const stopTracks = (stream) => {
  try { stream?.getTracks?.().forEach((track) => track.stop()); } catch (_) {}
};
const closeContext = async (context) => {
  if (!context) return;
  try { await context.close(); } catch (_) {}
};
const isLive = (track) => track && track.readyState === 'live';

export function createTelehealthCapture({
  onFrames,
  onInterrupted = () => {},
  onSourceState = () => {},
  onSpeechFrame = () => {},
  onVadState = () => {},
  onDiagnostic = () => {},
  confirmWindowAudio = () => false,
  mediaDevices = navigator.mediaDevices,
  AudioContextCtor = window.AudioContext || window.webkitAudioContext,
  AudioWorkletNodeCtor = window.AudioWorkletNode,
} = {}) {
  if (typeof onFrames !== 'function') throw new TypeError('A bounded frame consumer is required.');

  let state = 'idle';
  let startGeneration = 0;
  let displayStream = null;
  let microphoneStream = null;
  let context = null;
  let timeline = null;
  let stopPromise = null;
  let interrupted = false;
  let terminalFailure = null;
  let vadMonitor = null;
  const log = (event, fields = {}) => {
    try { onDiagnostic({ event, ...fields }); } catch (_) {}
  };
  const nodes = [];
  const taps = [];
  const cleanups = [];
  const flushWaiters = new Map();

  const emitSourceState = (channel, status) => {
    try { onSourceState({ channel, status }); } catch (_) {}
  };

  const interruptedCapture = (channel, reason) => {
    if (interrupted || state === 'stopped') return;
    interrupted = true;
    terminalFailure = { channel, reason };
    log('capture_interrupted', { channel: channel || 'none', code: reason === 'ended' ? 'source_ended' : reason === 'source_muted' ? 'source_muted' : 'browser_capture_failed' });
    try { onInterrupted({ channel, reason }); } catch (_) {}
    if (state !== 'stopping') void stop().catch(() => {});
  };

  const setUpTrackEvents = (channel, track) => {
    const ended = () => { log('track_ended', { channel }); interruptedCapture(channel, 'ended'); };
    const muted = () => {
      log('track_muted', { channel });
      emitSourceState(channel, 'muted');
      interruptedCapture(channel, 'source_muted');
    };
    const unmuted = () => emitSourceState(channel, 'live');
    track.addEventListener('ended', ended);
    track.addEventListener('mute', muted);
    track.addEventListener('unmute', unmuted);
    cleanups.push(() => {
      track.removeEventListener('ended', ended);
      track.removeEventListener('mute', muted);
      track.removeEventListener('unmute', unmuted);
    });
    if (track.muted) {
      emitSourceState(channel, 'muted');
      interruptedCapture(channel, 'source_muted');
    } else {
      emitSourceState(channel, 'live');
    }
  };

  const makeTap = (channel, stream) => {
    const source = context.createMediaStreamSource(stream);
    const tap = new AudioWorkletNodeCtor(context, 'openscribe-telehealth-source', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { channel },
    });
    const silence = context.createGain();
    silence.gain.value = 0;
    source.connect(tap);
    tap.connect(silence);
    silence.connect(context.destination); // Worklet is rendered but NEVER audible.
    nodes.push(source, tap, silence);
    taps.push({ channel, tap });
    tap.onprocessorerror = () => interruptedCapture(channel, 'processor_failed');
    tap.port.onmessage = (event) => {
      const data = event.data;
      if (data?.type === 'flushed') {
        const waiter = flushWaiters.get(channel);
        if (waiter && waiter.requestId === data.requestId) waiter.resolve();
        return;
      }
      if (state !== 'running' && state !== 'stopping') return;
      if (data?.type === 'overflow') {
        interruptedCapture(channel, 'audio_backpressure');
        return;
      }
      if (data?.type !== 'frames' || data.channel !== channel
        || !(data.samples instanceof Float32Array)) {
        interruptedCapture(channel, 'invalid_frames');
        return;
      }
      try {
        const timing = timeline.ingest({ channel, startFrame: data.startFrame, samples: data.samples });
        // An async consumer could retain unbounded source audio on this hot
        // path. Require synchronous acceptance and fail on backpressure.
        const accepted = onFrames({ ...timing, samples: data.samples });
        if (accepted === false || (accepted && typeof accepted.then === 'function')) {
          throw new TypeError('Telehealth audio consumer must accept samples synchronously.');
        }
        tap.port.postMessage({ type: 'ack' });
      } catch (_) {
        interruptedCapture(channel, 'consumer_failed');
      }
    };
    return tap;
  };

  const flushTap = (channel, tap) => new Promise((resolve) => {
    const requestId = channel + '-' + startGeneration;
    const timeout = window.setTimeout(() => {
      flushWaiters.delete(channel);
      interruptedCapture(channel, 'audio_flush_timeout');
      resolve();
    }, 1500);
    flushWaiters.set(channel, {
      requestId,
      resolve: () => {
        window.clearTimeout(timeout);
        flushWaiters.delete(channel);
        resolve();
      },
    });
    try { tap.port.postMessage({ type: 'flush', requestId }); } catch (_) {
      window.clearTimeout(timeout);
      flushWaiters.delete(channel);
      interruptedCapture(channel, 'audio_flush_failed');
      resolve();
    }
  });

  async function stop() {
    if (stopPromise) return stopPromise;
    ++startGeneration; // Cancel pending permission and model startup.
    const shouldFlush = state === 'running';
    state = 'stopping';
    stopPromise = (async () => {
      log('capture_stopping');
      vadMonitor?.stop();
      vadMonitor = null;
      if (shouldFlush) await Promise.all(taps.map(({ channel, tap }) => flushTap(channel, tap)));
      for (const cleanup of cleanups.splice(0)) {
        try { cleanup(); } catch (_) {}
      }
      stopTracks(displayStream);
      stopTracks(microphoneStream);
      displayStream = null;
      microphoneStream = null;
      for (const node of nodes.splice(0)) {
        try { node.disconnect(); } catch (_) {}
      }
      taps.length = 0;
      await closeContext(context);
      context = null;
      timeline?.close();
      state = 'stopped';
      log('capture_stopped');
      if (terminalFailure) throw new Error('Telehealth recording ended with incomplete audio.');
    })();
    return stopPromise;
  }

  const start = async () => {
    if (state !== 'idle') throw new Error('Capture can only be started once.');
    if (!mediaDevices?.getDisplayMedia || !mediaDevices?.getUserMedia
      || !AudioContextCtor || !AudioWorkletNodeCtor) {
      throw new Error('Dual-source browser audio capture is unsupported.');
    }
    state = 'starting';
    const generation = ++startGeneration;
    try {
      // Trigger display-picker synchronously from a direct user gesture. The
      // selected tab MUST expose an actual audio track.
      log('picker_requested');
      const acquiredDisplay = await mediaDevices.getDisplayMedia({
        video: true, audio: true, selfBrowserSurface: 'exclude',
        surfaceSwitching: 'exclude', monitorTypeSurfaces: 'exclude',
        windowAudio: 'window', systemAudio: 'exclude',
      });
      if (generation !== startGeneration) {
        stopTracks(acquiredDisplay);
        throw new Error('Telehealth capture was cancelled.');
      }
      displayStream = acquiredDisplay;
      const displayVideo = displayStream.getVideoTracks()[0];
      const patientAudio = displayStream.getAudioTracks()[0];
      const displaySurface = displayVideo?.getSettings?.().displaySurface || 'unknown';
      const surface = ['browser', 'window', 'monitor'].includes(displaySurface) ? displaySurface : 'unknown';
      log('picker_selected', { surface });
      if (!isLive(displayVideo)) {
        log('picker_rejected', { surface, code: 'missing_video' });
        throw new Error('The selected sharing source has no live video track.');
      }
      if (surface !== 'browser' && surface !== 'window') {
        log('picker_rejected', { surface, code: 'unsupported_surface' });
        throw new Error('Choose a browser tab or application window. Full-screen sharing is not supported.');
      }
      if (!isLive(patientAudio)) {
        log('picker_rejected', { surface, code: 'missing_audio' });
        throw new Error(surface === 'window'
          ? 'No audio was shared with that window. Enable window audio in the picker, or use a browser call tab.'
          : 'No tab audio was shared. Select the call tab and enable Share tab audio.');
      }
      if (surface === 'window') {
        // Chromium may offer system audio as a fallback: the API does not
        // expose trustworthy proof that audio belongs to this window alone.
        const acknowledged = await Promise.resolve(confirmWindowAudio());
        if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
        if (!acknowledged) {
          log('picker_rejected', { surface, code: 'window_declined' });
          throw new Error('Window-audio capture was cancelled.');
        }
        log('window_acknowledged', { surface });
      }
      log('microphone_requested');
      const acquiredMic = await mediaDevices.getUserMedia({
        audio: { echoCancellation: true },
      });
      if (generation !== startGeneration) {
        stopTracks(acquiredMic);
        throw new Error('Telehealth capture was cancelled.');
      }
      microphoneStream = acquiredMic;
      log('microphone_ready');
      const microphoneAudio = microphoneStream.getAudioTracks()[0];
      if (!isLive(microphoneAudio) || !isLive(patientAudio)) {
        throw new Error('Both microphone and tab audio must be available.');
      }
      context = new AudioContextCtor();
      if (!context.audioWorklet?.addModule) throw new Error('AudioWorklet is unsupported.');
      await context.suspend();
      await context.audioWorklet.addModule(WORKLET_URL);
      log('worklet_ready');
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      timeline = createTelehealthTimeline({
        sampleRate: context.sampleRate,
        originFrame: Math.round(context.currentTime * context.sampleRate),
      });
      setUpTrackEvents('patient', patientAudio);
      setUpTrackEvents('clinician', microphoneAudio);
      const videoEnded = () => interruptedCapture('patient', 'ended');
      displayVideo.addEventListener('ended', videoEnded);
      cleanups.push(() => displayVideo.removeEventListener('ended', videoEnded));
      makeTap('patient', displayStream);
      makeTap('clinician', microphoneStream);
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      state = 'running';
      await context.resume();
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      if (context.state !== 'running') throw new Error('Audio capture clock did not start.');
      log('capture_started', { surface: surface });
      vadMonitor = createTelehealthVadMonitor({ onSpeechFrame, onState: onVadState, onDiagnostic });
      vadMonitor.start({ patient: patientAudio, clinician: microphoneAudio });
      context.onstatechange = () => {
        if (state === 'running' && context?.state !== 'running') {
          interruptedCapture(null, 'audio_clock_interrupted');
        }
      };
      return { sampleRate: timeline.snapshot().sampleRate };
    } catch (error) {
      const code = error?.name === 'NotAllowedError' ? 'permission_denied'
        : error?.name === 'AbortError' ? 'user_cancelled' : 'browser_capture_failed';
      log('capture_failed', { code });
      await stop();
      throw error;
    }
  };

  return {
    start,
    stop,
    get state() { return state; },
    get timelineSnapshot() { return timeline?.snapshot() || null; },
  };
}
