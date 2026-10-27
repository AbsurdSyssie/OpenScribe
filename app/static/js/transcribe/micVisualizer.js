const BAR_COUNT = 12;
const IDLE_LEVEL = 0.14;

const buildLevels = (frame) => {
  if (!(frame instanceof Float32Array) || frame.length === 0) return Array(BAR_COUNT).fill(IDLE_LEVEL);
  const levels = [];
  const bucketSize = Math.max(1, Math.floor(frame.length / BAR_COUNT));
  for (let bucketIndex = 0; bucketIndex < BAR_COUNT; bucketIndex += 1) {
    const start = bucketIndex * bucketSize;
    const end = bucketIndex === BAR_COUNT - 1 ? frame.length : Math.min(frame.length, start + bucketSize);
    let total = 0;
    let samples = 0;
    for (let sampleIndex = start; sampleIndex < end; sampleIndex += 1) {
      total += Math.abs(frame[sampleIndex] || 0);
      samples += 1;
    }
    levels.push(Math.min(1, Math.max(0.12, (samples ? total / samples : 0) * 7)));
  }
  return levels;
};

export function createMicVisualizer(element, { AudioContextCtor = null } = {}) {
  let bars = [];
  let levels = Array(BAR_COUNT).fill(IDLE_LEVEL);
  let audioContext = null;
  let analyser = null;
  let animationFrameId = null;
  let generation = 0;
  let paused = false;

  const ensureBars = () => {
    if (!element || bars.length) return;
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < BAR_COUNT; index += 1) {
      const bar = document.createElement('span');
      bar.className = 'mic-visualizer__bar';
      bar.style.setProperty('--level', String(IDLE_LEVEL));
      fragment.appendChild(bar);
      bars.push(bar);
    }
    element.appendChild(fragment);
  };

  const render = () => {
    ensureBars();
    bars.forEach((bar, index) => bar.style.setProperty('--level', String(levels[index] || IDLE_LEVEL)));
  };

  const updateFrame = (frame) => {
    const nextLevels = buildLevels(frame);
    levels = levels.map((previous, index) => (previous * 0.45) + (nextLevels[index] * 0.55));
    render();
  };

  const setVadActive = (active) => {
    if (element) element.dataset.vadActive = active ? 'true' : 'false';
  };

  const cancelFrame = () => {
    if (animationFrameId !== null) window.cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  };

  const reset = () => {
    levels = Array(BAR_COUNT).fill(IDLE_LEVEL);
    setVadActive(false);
    render();
  };

  const closeContext = (context) => {
    if (!context?.close) return;
    try {
      Promise.resolve(context.close()).catch(() => {});
    } catch (_) {}
  };

  const drawFrames = (runGeneration) => {
    if (runGeneration !== generation || paused || !analyser) return;
    try {
      const frame = new Float32Array(analyser.fftSize);
      const draw = () => {
        if (runGeneration !== generation || paused || !analyser) return;
        try {
          analyser.getFloatTimeDomainData(frame);
          updateFrame(frame);
          animationFrameId = window.requestAnimationFrame(draw);
        } catch (_) {
          stop();
        }
      };
      draw();
    } catch (_) {
      stop();
    }
  };

  const stop = () => {
    generation += 1;
    paused = false;
    cancelFrame();
    const closingContext = audioContext;
    audioContext = null;
    analyser = null;
    closeContext(closingContext);
    reset();
  };

  const pause = () => {
    paused = true;
    generation += 1;
    cancelFrame();
    reset();
  };

  const startStream = (stream) => {
    stop();
    const Context = AudioContextCtor || window.AudioContext || window.webkitAudioContext;
    if (!element || !Context || !stream) return false;
    let context;
    try {
      context = new Context();
      const source = context.createMediaStreamSource(stream);
      const nextAnalyser = context.createAnalyser();
      nextAnalyser.fftSize = 512;
      source.connect(nextAnalyser);
      audioContext = context;
      analyser = nextAnalyser;
    } catch (_) {
      closeContext(context);
      return false;
    }
    paused = false;
    const runGeneration = ++generation;
    let resumeResult;
    try {
      resumeResult = context.resume?.();
    } catch (_) {
      drawFrames(runGeneration);
      return true;
    }
    Promise.resolve(resumeResult).catch(() => {}).finally(() => {
      if (runGeneration === generation && !paused && audioContext === context) drawFrames(runGeneration);
    });
    return true;
  };

  const resume = () => {
    if (!audioContext || !analyser) return;
    paused = false;
    const runGeneration = ++generation;
    let resumeResult;
    try {
      resumeResult = audioContext.resume?.();
    } catch (_) {
      return;
    }
    Promise.resolve(resumeResult).catch(() => {}).finally(() => {
      drawFrames(runGeneration);
    });
  };

  ensureBars();
  reset();
  return { updateFrame, setVadActive, reset, startStream, pause, resume, stop };
}
