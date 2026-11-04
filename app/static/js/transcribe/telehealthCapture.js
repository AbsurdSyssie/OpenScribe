import { createTelehealthTimeline } from './telehealthTimeline.js?v=20261104-telehealth-timing';

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
  const nodes = [];
  const taps = [];
  const cleanups = [];
  const flushWaiters = new Map();

  const emitSourceState = (channel, status) => {
    try { onSourceState({ channel, status }); } catch (_) {}
  };

  const interruptedCapture = (channel, reason) => {
    if (interrupted || state === 'stopping' || state === 'stopped') return;
    interrupted = true;
    try { onInterrupted({ channel, reason }); } catch (_) {}
    void stop();
  };

  const setUpTrackEvents = (channel, track) => {
    const ended = () => interruptedCapture(channel, 'ended');
    const muted = () => emitSourceState(channel, 'muted');
    const unmuted = () => emitSourceState(channel, 'live');
    track.addEventListener('ended', ended);
    track.addEventListener('mute', muted);
    track.addEventListener('unmute', unmuted);
    cleanups.push(() => {
      track.removeEventListener('ended', ended);
      track.removeEventListener('mute', muted);
      track.removeEventListener('unmute', unmuted);
    });
    emitSourceState(channel, track.muted ? 'muted' : 'live');
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
      resolve();
    }
  });

  async function stop() {
    if (stopPromise) return stopPromise;
    ++startGeneration; // Cancel pending permission and model startup.
    const shouldFlush = state === 'running';
    state = 'stopping';
    stopPromise = (async () => {
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
      const acquiredDisplay = await mediaDevices.getDisplayMedia({
        video: true, audio: true, selfBrowserSurface: 'exclude',
        surfaceSwitching: 'include',
      });
      if (generation !== startGeneration) {
        stopTracks(acquiredDisplay);
        throw new Error('Telehealth capture was cancelled.');
      }
      displayStream = acquiredDisplay;
      const displayVideo = displayStream.getVideoTracks()[0];
      const patientAudio = displayStream.getAudioTracks()[0];
      if (!isLive(displayVideo) || !isLive(patientAudio)) {
        throw new Error('Select a browser tab and enable Share tab audio.');
      }
      // Reject explicitly non-tab capture, even if system sharing produces
      // audio; native WhatsApp app capture is a separately scoped later phase.
      const displaySurface = displayVideo.getSettings?.().displaySurface;
      if (displaySurface && displaySurface !== 'browser') {
        throw new Error('Telehealth currently requires sharing a browser tab.');
      }
      const acquiredMic = await mediaDevices.getUserMedia({
        audio: { echoCancellation: true },
      });
      if (generation !== startGeneration) {
        stopTracks(acquiredMic);
        throw new Error('Telehealth capture was cancelled.');
      }
      microphoneStream = acquiredMic;
      const microphoneAudio = microphoneStream.getAudioTracks()[0];
      if (!isLive(microphoneAudio) || !isLive(patientAudio)) {
        throw new Error('Both microphone and tab audio must be available.');
      }
      context = new AudioContextCtor();
      if (!context.audioWorklet?.addModule) throw new Error('AudioWorklet is unsupported.');
      await context.suspend();
      await context.audioWorklet.addModule(WORKLET_URL);
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      timeline = createTelehealthTimeline({
        sampleRate: context.sampleRate,
        originFrame: Math.round(context.currentTime * context.sampleRate),
      });
      setUpTrackEvents('patient', patientAudio);
      setUpTrackEvents('clinician', microphoneAudio);
      makeTap('patient', displayStream);
      makeTap('clinician', microphoneStream);
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      state = 'running';
      await context.resume();
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      if (context.state !== 'running') throw new Error('Audio capture clock did not start.');
      context.onstatechange = () => {
        if (state === 'running' && context?.state !== 'running') {
          interruptedCapture(null, 'audio_clock_interrupted');
        }
      };
      return { sampleRate: timeline.snapshot().sampleRate };
    } catch (error) {
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
