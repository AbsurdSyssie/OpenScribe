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

// Display sharing is selected separately from recording so a clinician can
// prepare the call tab before the consultation begins. This object owns only
// the browser-provided display tracks until the one-shot handoff to capture.
export function prepareTelehealthDisplaySource({
  mediaDevices = navigator.mediaDevices,
  audioScope = 'call',
  confirmWindowAudio = () => false,
  onDiagnostic = () => {},
  onState = () => {},
} = {}) {
  if (!mediaDevices?.getDisplayMedia) throw new Error('Browser audio sharing is unsupported.');
  if (!['call', 'system'].includes(audioScope)) throw new TypeError('Invalid Telehealth audio capture scope.');
  const log = (event, fields = {}) => { try { onDiagnostic({ event, ...fields }); } catch (_) {} };
  log('picker_requested', { audio_scope: audioScope });
  let picker;
  try {
    picker = mediaDevices.getDisplayMedia({
      video: audioScope === 'system' ? { displaySurface: 'window' } : true,
      audio: true, selfBrowserSurface: 'exclude',
      surfaceSwitching: 'exclude', monitorTypeSurfaces: 'exclude',
      // windowAudio governs window sharing, systemAudio governs monitor sharing.
      // Entire-monitor capture remains excluded in BOTH modes.
      windowAudio: audioScope === 'system' ? 'system' : 'window',
      systemAudio: 'exclude',
    });
  } catch (error) {
    return Promise.reject(error);
  }
  let acquiredStream = null;
  return Promise.resolve(picker).then(async (stream) => {
    acquiredStream = stream;
    let owner = 'prepared';
    let interruptionHandler = null;
    const cleanups = [];
    const tracks = [...(stream.getVideoTracks?.() || []), ...(stream.getAudioTracks?.() || [])];
    const stop = () => { try { stream.getTracks?.().forEach((track) => track.stop()); } catch (_) {} };
    const release = () => {
      if (owner === 'released') return;
      owner = 'released';
      cleanups.splice(0).forEach((cleanup) => { try { cleanup(); } catch (_) {} });
      stop();
    };
    const interrupt = (reason) => {
      if (owner === 'released') return;
      if (owner === 'prepared') {
        log('picker_rejected', { surface, code: 'source_ended' });
        release();
        try { onState('ended'); } catch (_) {}
        return;
      }
      try { interruptionHandler?.({ channel: 'patient', reason }); } catch (_) {}
    };
    const video = stream.getVideoTracks?.()[0];
    const audio = stream.getAudioTracks?.()[0];
    const displaySurface = video?.getSettings?.().displaySurface || 'unknown';
    const surface = ['browser', 'window', 'monitor'].includes(displaySurface) ? displaySurface : 'unknown';
    log('picker_selected', { surface, audio_scope: audioScope });
    const reject = (error) => { stop(); throw error; };
    if (!isLive(video)) {
      log('picker_rejected', { surface, code: 'missing_video' });
      return reject(new Error('The selected sharing source has no live video track.'));
    }
    if (surface !== 'browser' && surface !== 'window') {
      log('picker_rejected', { surface, code: 'unsupported_surface' });
      return reject(new Error('Choose a browser tab or application window. Full-screen sharing is not supported.'));
    }
    if (audioScope === 'system' && surface !== 'window') {
      log('picker_rejected', { surface, code: 'unsupported_surface', audio_scope: audioScope });
      return reject(new Error('Select a desktop application window for system audio. Browser tabs and entire-screen sharing are not supported in this mode.'));
    }
    if (!isLive(audio)) {
      log('picker_rejected', { surface, code: 'missing_audio', audio_scope: audioScope });
      return reject(new Error(audioScope === 'system'
        ? 'No system audio was shared. Select an application window and enable Share system audio, or use browser tab audio.'
        : surface === 'window'
          ? 'No audio was shared with that window. Enable window audio in the picker, or use a browser call tab.'
          : 'No tab audio was shared. Select the call tab and enable Share tab audio.'));
    }
    if (surface === 'window') {
      let acknowledged = false;
      try { acknowledged = await Promise.resolve(confirmWindowAudio({ audioScope, surface })); }
      catch (error) { return reject(error); }
      if (!acknowledged) {
        log('picker_rejected', { surface, code: 'window_declined' });
        return reject(new Error('Window-audio capture was cancelled.'));
      }
      log('window_acknowledged', { surface, audio_scope: audioScope });
    }
    if (!isLive(video) || !isLive(audio)) {
      log('picker_rejected', { surface, code: 'source_ended' });
      return reject(new Error('The selected source ended. Select the call tab again.'));
    }
    const source = {
      stream, surface, audioScope,
      isLive: () => owner !== 'released' && isLive(video) && isLive(audio),
      setInterruptionHandler(handler) { interruptionHandler = handler; },
      release,
      transfer(handler) {
        if (owner !== 'prepared') throw new Error('The selected source is no longer available. Select it again.');
        if (!isLive(video) || !isLive(audio)) {
          release();
          throw new Error('The selected source ended. Select the call tab again.');
        }
        owner = 'capture';
        interruptionHandler = handler;
        return stream;
      },
    };
    for (const track of tracks) {
      const ended = () => interrupt('ended');
      const isPatientAudio = track === audio;
      const muted = () => {
        if (!isPatientAudio) return;
        if (owner === 'prepared') {
          try { onState('muted'); } catch (_) {}
        } else if (owner === 'capture') interruptionHandler?.({ channel: 'patient', reason: 'source_muted' });
      };
      const unmuted = () => {
        if (!isPatientAudio) return;
        if (owner === 'prepared') { try { onState(source.isLive() ? 'live' : 'ended'); } catch (_) {} }
      };
      track.addEventListener('ended', ended);
      if (isPatientAudio) {
        track.addEventListener('mute', muted);
        track.addEventListener('unmute', unmuted);
      }
      cleanups.push(() => {
        track.removeEventListener('ended', ended);
        if (isPatientAudio) {
          track.removeEventListener('mute', muted);
          track.removeEventListener('unmute', unmuted);
        }
      });
    }
    try { onState('connected'); } catch (_) {}
    return source;
  }).catch((error) => {
    stopTracks(acquiredStream);
    throw error;
  });
}

export function createTelehealthCapture({
  onFrames,
  onInterrupted = () => {},
  onSourceState = () => {},
  onSpeechFrame = () => {},
  onVadState = () => {},
  onDiagnostic = () => {},
  preparedSource = null,
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
  let hasStarted = false;
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
    // Startup interruptions cancel this attempt before any recording frames
    // exist; they require a new source/device attempt, not incomplete-audio
    // acknowledgement. Running captures keep the established stop path.
    if (state !== 'starting') {
      try { onInterrupted({ channel, reason }); } catch (_) {}
    }
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
      if (preparedSource) preparedSource.release();
      else stopTracks(displayStream);
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
    const source = preparedSource;
    if (state !== 'idle') throw new Error('Capture can only be started once.');
    if (!source || !source.isLive?.() || !mediaDevices?.getUserMedia
      || !AudioContextCtor || !AudioWorkletNodeCtor) {
      throw new Error('Select a live call tab before starting Telehealth.');
    }
    state = 'starting';
    const generation = ++startGeneration;
    try {
      // Transfer the already-selected source before any await. Its lifecycle
      // listener remains armed throughout microphone and worklet startup.
      displayStream = source.transfer(({ channel, reason }) => interruptedCapture(channel, reason));
      const displayVideo = displayStream.getVideoTracks()[0];
      const patientAudio = displayStream.getAudioTracks()[0];
      const displaySurface = displayVideo?.getSettings?.().displaySurface || 'unknown';
      const surface = ['browser', 'window', 'monitor'].includes(displaySurface) ? displaySurface : 'unknown';
      if (surface !== source.surface) {
        log('picker_rejected', { surface, code: 'unsupported_surface' });
        throw new Error('The selected sharing source changed. Enable Telehealth again and select the call tab.');
      }
      if (!isLive(displayVideo)) {
        log('picker_rejected', { surface, code: 'missing_video' });
        throw new Error('The selected sharing source has no live video track.');
      }
      if ((surface !== 'browser' && surface !== 'window')
        || (source.audioScope === 'system' && surface !== 'window')) {
        log('picker_rejected', { surface, code: 'unsupported_surface' });
        throw new Error('The selected audio source is not supported. Select it again.');
      }
      if (!isLive(patientAudio)) {
        log('picker_rejected', { surface, code: 'missing_audio' });
        throw new Error(surface === 'window'
          ? 'No audio was shared with that window. Enable window audio in the picker, or use a browser call tab.'
          : 'No tab audio was shared. Select the call tab and enable Share tab audio.');
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
        throw new Error('Both microphone and shared call audio must be available.');
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
      if (!source.isLive()) throw new Error('The selected source ended. Select the call tab again.');
      await context.resume();
      if (generation !== startGeneration) throw new Error('Telehealth capture was cancelled.');
      if (context.state !== 'running') throw new Error('Audio capture clock did not start.');
      state = 'running';
      hasStarted = true;
      log('capture_started', { surface, audio_scope: source.audioScope || 'call' });
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
      const sourceInterruptedDuringStartup = Boolean(terminalFailure) && !hasStarted;
      try { await stop(); } catch (_) {}
      if (sourceInterruptedDuringStartup) {
        throw new Error('The selected source ended before recording began. Enable Telehealth again and select the call tab.');
      }
      throw error;
    }
  };

  return {
    start,
    stop,
    get state() { return state; },
    get hasStarted() { return hasStarted; },
    get timelineSnapshot() { return timeline?.snapshot() || null; },
  };
}
