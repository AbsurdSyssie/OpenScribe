// Silero verification is informational only. It never gates, trims or stores audio.
// Detector tracks are clones: VAD teardown cannot stop the recorder-owned tracks.
const CHANNELS = ['clinician', 'patient'];
const SPEECH_THRESHOLD = 0.3;
const SAMPLE_RATE = 16000;

export function createTelehealthVadMonitor({
  onSpeechFrame = () => {},
  onState = () => {},
  onDiagnostic = () => {},
  VadCtor = typeof window !== 'undefined' ? window.vad?.MicVAD : null,
  MediaStreamCtor = typeof MediaStream !== 'undefined' ? MediaStream : null,
} = {}) {
  let generation = 0;
  const running = new Map();
  const clones = new Set();
  const emit = (event, channel) => {
    try { onDiagnostic({ event, channel }); } catch (_) {}
  };
  const setState = (channel, status) => {
    try { onState({ channel, status }); } catch (_) {}
  };
  const dispose = (instance) => {
    if (!instance) return;
    try { void Promise.resolve(instance.pause?.()).catch(() => {}); } catch (_) {}
    try { void Promise.resolve(instance.destroy?.()).catch(() => {}); } catch (_) {}
  };
  const stop = () => {
    ++generation;
    for (const instance of running.values()) dispose(instance);
    running.clear();
    for (const track of clones) {
      try { track.stop(); } catch (_) {}
    }
    clones.clear();
  };
  const start = (sourceTracks) => {
    const current = ++generation;
    for (const channel of CHANNELS) {
      const track = sourceTracks?.[channel];
      if (!VadCtor?.new || !MediaStreamCtor || typeof track?.clone !== 'function') {
        setState(channel, 'unavailable');
        emit('vad_unavailable', channel);
        continue;
      }
      let clone;
      let stream;
      try {
        clone = track.clone();
        stream = new MediaStreamCtor([clone]);
        clones.add(clone);
      } catch (_) {
        try { clone?.stop(); } catch (_) {}
        setState(channel, 'unavailable');
        emit('vad_unavailable', channel);
        continue;
      }
      emit('vad_loading', channel);
      // Startup is detached so model downloads never hold up recording.
      void (async () => {
        let instance;
        try {
          instance = await VadCtor.new({
            model: 'v5',
            startOnLoad: false,
            positiveSpeechThreshold: SPEECH_THRESHOLD,
            baseAssetPath: '/static/vendor/vad-web/0.0.29/',
            onnxWASMBasePath: '/static/vendor/onnxruntime-web/1.22.0/',
            getStream: async () => stream,
            resumeStream: async () => stream,
            // Recorder owns the ORIGINAL tracks; detector owns only the clone.
            pauseStream: async () => {},
            onFrameProcessed: (probabilities, frame) => {
              if (generation !== current || !frame?.length) return;
              const probability = Number(probabilities?.isSpeech);
              const speechMs = Number.isFinite(probability) && probability >= SPEECH_THRESHOLD
                ? frame.length * 1000 / SAMPLE_RATE : 0;
              try { onSpeechFrame({ channel, speechMs }); } catch (_) {}
            },
          });
          if (generation !== current) { dispose(instance); return; }
          running.set(channel, instance);
          await instance.start();
          if (generation !== current) return;
          setState(channel, 'ready');
          emit('vad_ready', channel);
        } catch (_) {
          if (generation !== current) return;
          running.delete(channel);
          dispose(instance);
          setState(channel, 'unavailable');
          emit('vad_unavailable', channel);
        }
      })();
    }
  };
  return { start, stop };
}
