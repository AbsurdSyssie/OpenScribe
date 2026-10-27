// Shared microphone/VAD boundary used by consultation capture and dictation.
// Each capture supplies its own destination behavior while this module keeps
// startup races and callback lifetime checks consistent.
export function createVadCaptureCallbacks({
  isCurrent = () => true,
  isCurrentSpeechEnd = isCurrent,
  onFrameProcessed,
  onSpeechStart,
  onSpeechEnd,
  onVADMisfire,
}) {
  return {
    onFrameProcessed: (probabilities, frame) => {
      if (isCurrent()) onFrameProcessed?.(probabilities, frame);
    },
    onSpeechStart: (...args) => {
      if (isCurrent()) onSpeechStart?.(...args);
    },
    onSpeechEnd: (audio) => {
      if (isCurrentSpeechEnd()) onSpeechEnd?.(audio);
    },
    onVADMisfire: (...args) => {
      if (isCurrent()) onVADMisfire?.(...args);
    },
  };
}

export function createVadCaptureOptions({
  model,
  startOnLoad = true,
  positiveSpeechThreshold = 0.3,
  preSpeechPadMs = 800,
  redemptionMs = 2000,
  minSpeechMs = 400,
  baseAssetPath,
  onnxWASMBasePath,
  getStream,
  pauseStream,
  resumeStream = getStream,
  ...callbacks
}) {
  return {
    model,
    positiveSpeechThreshold,
    startOnLoad,
    preSpeechPadMs,
    redemptionMs,
    minSpeechMs,
    submitUserSpeechOnPause: true,
    baseAssetPath,
    onnxWASMBasePath,
    getStream: async (...args) => {
      if (callbacks.isCurrent && !callbacks.isCurrent()) throw new Error('Microphone capture startup was canceled.');
      return getStream(...args);
    },
    resumeStream: async (...args) => {
      if (callbacks.isCurrent && !callbacks.isCurrent()) throw new Error('Microphone capture startup was canceled.');
      return resumeStream(...args);
    },
    pauseStream: async (...args) => pauseStream?.(...args),
    ...createVadCaptureCallbacks(callbacks),
  };
}

export function createOwnedMicStream({ acquire, canAcquire, onAcquire = () => {}, onRelease = () => {} }) {
  let stream = null;
  const getStream = async () => {
    if (!canAcquire()) throw new Error('Microphone capture startup was canceled.');
    const acquired = await acquire();
    if (!canAcquire()) {
      acquired?.getTracks?.().forEach((track) => track.stop());
      throw new Error('Microphone capture startup was canceled.');
    }
    stream = acquired;
    onAcquire(acquired);
    return acquired;
  };
  const pauseStream = async (target = stream) => {
    if (!target) return;
    try { target.getTracks?.().forEach((track) => track.stop()); } catch (_) {}
    if (stream === target) stream = null;
    onRelease(target);
  };
  return { getStream, resumeStream: getStream, pauseStream, get current() { return stream; } };
}
