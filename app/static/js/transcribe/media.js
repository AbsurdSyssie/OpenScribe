import { csrfFetch } from '../csrf.js';

export function createAudioCaptureController({
  dom,
  config,
  uploadBatchAudio,
  canUseLiveInput,
  canUseWholeFileInput,
  getState,
  setNextLiveChunkSequenceNo,
  getDefaultMicStatusState,
  syncTranscriptTitleIfNeeded,
  finalizeLiveCapture,
  fetchWorkspace,
  pollWorkspace,
  scheduleWorkspaceRefreshBurst,
  parseErrorResponse,
  parseErrorMessage,
  setMicButtons,
  setMicStatus,
  setVisibleStatus,
  setSessionProgress,
  setRetryAvailability,
  showFlash,
  reflectBackendStatus,
  reportMicIssue,
  confirmBeforeStartRecording,
}) {
  const RECORDING_DURATION_STORAGE_KEY = 'openscribe-glm2-recording-durations';
  const MIC_VISUALIZER_BAR_COUNT = 12;
  const VAD_SILENCE_PROMPT_MS = 30000;
  let mediaStream = null;
  let captureMode = 'batch';
  let liveVadInstance = null;
  let batchVadInstance = null;
  let batchSpeechSegments = [];
  let batchRolloverTimeoutId = null;
  let batchForceRolloverRequested = false;
  let batchStopRequested = false;
  let batchRestartPending = false;
  let batchRolloverUploadPending = false;
  let batchUploadQueue = Promise.resolve();
  let recordStartGuardInFlight = false;
  let batchCaptureGeneration = 0;
  let liveChunkTimeoutId = null;
  let liveStopRequested = false;
  let liveForceContinueRequested = false;
  let liveSpeechActive = false;
  let liveRestartPending = false;
  let livePendingOverlapAudio = null;
  let liveChunkProcessing = Promise.resolve();
  let liveLastChunkUploadStartedAt = 0;
  let startedAt = null;
  let timerId = null;
  let recordingTranscriptId = null;
  let accumulatedBeforeCurrentSegmentMs = 0;
  let micVisualizerBars = [];
  let micVisualizerLevels = Array(MIC_VISUALIZER_BAR_COUNT).fill(0.14);
  let silencePromptTimeoutId = null;
  let silencePromptDismissedForCurrentSilentInterval = false;
  let vadSpeechCurrentlyActive = false;

  const readStoredDurations = () => {
    try {
      const raw = window.localStorage.getItem(RECORDING_DURATION_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
      return {};
    }
  };

  const writeStoredDurations = (durations) => {
    try {
      window.localStorage.setItem(RECORDING_DURATION_STORAGE_KEY, JSON.stringify(durations));
    } catch (_) {}
  };

  const storedDurationMsForTranscript = (transcriptId) => {
    if (!transcriptId) return 0;
    const value = readStoredDurations()[transcriptId];
    const numeric = typeof value === 'number' ? value : Number(value || 0);
    return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
  };

  const persistStoredDurationMsForTranscript = (transcriptId, durationMs) => {
    if (!transcriptId) return;
    const durations = readStoredDurations();
    durations[transcriptId] = Math.max(0, Math.floor(durationMs));
    writeStoredDurations(durations);
  };

  const renderDurationMs = (durationMs) => {
    if (!dom.micTimer) return;
    const elapsedSeconds = Math.max(0, Math.floor(durationMs / 1000));
    const minutes = String(Math.floor(elapsedSeconds / 60)).padStart(2, '0');
    const seconds = String(elapsedSeconds % 60).padStart(2, '0');
    dom.micTimer.textContent = `${minutes}:${seconds}`;
  };

  const renderTimer = () => {
    const transcriptId = getState().transcriptId || '';
    if (!transcriptId) {
      renderDurationMs(0);
      return;
    }
    if (startedAt && recordingTranscriptId && transcriptId === recordingTranscriptId) {
      renderDurationMs(accumulatedBeforeCurrentSegmentMs + Math.max(0, Date.now() - startedAt));
      return;
    }
    renderDurationMs(storedDurationMsForTranscript(transcriptId));
  };

  const beginAccumulatedTimer = () => {
    recordingTranscriptId = getState().transcriptId || null;
    accumulatedBeforeCurrentSegmentMs = storedDurationMsForTranscript(recordingTranscriptId);
    startedAt = Date.now();
    renderTimer();
  };

  const finalizeAccumulatedTimer = () => {
    if (!startedAt || !recordingTranscriptId) return;
    const nextDurationMs = accumulatedBeforeCurrentSegmentMs + Math.max(0, Date.now() - startedAt);
    persistStoredDurationMsForTranscript(recordingTranscriptId, nextDurationMs);
    accumulatedBeforeCurrentSegmentMs = nextDurationMs;
  };

  const stopStreamTracks = () => {
    if (!mediaStream) return;
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  };

  const ensureMicVisualizerBars = () => {
    if (!dom.micVisualizer || micVisualizerBars.length > 0) return;
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < MIC_VISUALIZER_BAR_COUNT; index += 1) {
      const bar = document.createElement('span');
      bar.className = 'mic-visualizer__bar';
      bar.style.setProperty('--level', String(micVisualizerLevels[index] || 0.14));
      fragment.appendChild(bar);
      micVisualizerBars.push(bar);
    }
    dom.micVisualizer.appendChild(fragment);
  };

  const renderMicVisualizer = () => {
    ensureMicVisualizerBars();
    micVisualizerBars.forEach((bar, index) => {
      bar.style.setProperty('--level', String(micVisualizerLevels[index] || 0.14));
    });
  };

  const setMicVisualizerVadActive = (active) => {
    if (!dom.micVisualizer) return;
    dom.micVisualizer.dataset.vadActive = active ? 'true' : 'false';
  };

  const setMicVisualizerLevels = (levels) => {
    micVisualizerLevels = micVisualizerLevels.map((previous, index) => {
      const next = Math.max(0.12, Math.min(1, Number(levels[index] || 0)));
      return (previous * 0.45) + (next * 0.55);
    });
    renderMicVisualizer();
  };

  const resetMicVisualizer = () => {
    setMicVisualizerVadActive(false);
    micVisualizerLevels = Array(MIC_VISUALIZER_BAR_COUNT).fill(0.14);
    renderMicVisualizer();
  };

  const isRecordingOngoing = () => (
    (
      captureMode === 'live'
      && !liveStopRequested
      && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)
    )
    || (
      captureMode === 'batch'
      && !batchStopRequested
      && (Boolean(batchVadInstance) || batchRestartPending)
    )
  );

  const showSilencePrompt = () => {
    if (!dom.silencePrompt) return;
    dom.silencePrompt.hidden = false;
  };

  const hideSilencePrompt = () => {
    if (dom.silencePrompt) {
      dom.silencePrompt.hidden = true;
    }
  };

  const clearSilencePromptTimer = () => {
    if (silencePromptTimeoutId) {
      window.clearTimeout(silencePromptTimeoutId);
      silencePromptTimeoutId = null;
    }
  };

  const armSilencePromptTimer = () => {
    clearSilencePromptTimer();
    if (!isRecordingOngoing() || vadSpeechCurrentlyActive || silencePromptDismissedForCurrentSilentInterval) return;
    silencePromptTimeoutId = window.setTimeout(() => {
      silencePromptTimeoutId = null;
      if (!isRecordingOngoing() || vadSpeechCurrentlyActive || silencePromptDismissedForCurrentSilentInterval) return;
      showSilencePrompt();
    }, Number(config.vadSilencePromptMs || VAD_SILENCE_PROMPT_MS));
  };

  const markVadSpeechStarted = () => {
    vadSpeechCurrentlyActive = true;
    silencePromptDismissedForCurrentSilentInterval = false;
    clearSilencePromptTimer();
    hideSilencePrompt();
  };

  const markVadSpeechEndedOrIdle = () => {
    vadSpeechCurrentlyActive = false;
    if (!silencePromptDismissedForCurrentSilentInterval) {
      hideSilencePrompt();
    }
    armSilencePromptTimer();
  };

  const resetSilencePromptState = () => {
    clearSilencePromptTimer();
    hideSilencePrompt();
    silencePromptDismissedForCurrentSilentInterval = false;
    vadSpeechCurrentlyActive = false;
  };

  const buildMicVisualizerLevelsFromVadFrame = (frame) => {
    if (!(frame instanceof Float32Array) || frame.length === 0) {
      return Array(MIC_VISUALIZER_BAR_COUNT).fill(0.14);
    }
    const levels = [];
    const bucketSize = Math.max(1, Math.floor(frame.length / MIC_VISUALIZER_BAR_COUNT));
    for (let bucketIndex = 0; bucketIndex < MIC_VISUALIZER_BAR_COUNT; bucketIndex += 1) {
      const start = bucketIndex * bucketSize;
      const end = bucketIndex === MIC_VISUALIZER_BAR_COUNT - 1 ? frame.length : Math.min(frame.length, start + bucketSize);
      let total = 0;
      let samples = 0;
      for (let sampleIndex = start; sampleIndex < end; sampleIndex += 1) {
        total += Math.abs(frame[sampleIndex] || 0);
        samples += 1;
      }
      const average = samples > 0 ? total / samples : 0;
      levels.push(Math.min(1, Math.max(0.12, average * 7)));
    }
    return levels;
  };

  const clearLiveChunkTimeout = () => {
    if (liveChunkTimeoutId) {
      window.clearTimeout(liveChunkTimeoutId);
      liveChunkTimeoutId = null;
    }
  };

  const cleanupLiveVad = () => {
    const instance = liveVadInstance;
    liveVadInstance = null;
    if (!instance) return;
    try {
      if (typeof instance.destroy === 'function') {
        instance.destroy();
      } else {
        instance.pause();
      }
    } catch (_) {}
  };

  const cleanupBatchVad = () => {
    const instance = batchVadInstance;
    batchVadInstance = null;
    if (!instance) return;
    try {
      if (typeof instance.destroy === 'function') {
        instance.destroy();
      } else {
        instance.pause();
      }
    } catch (_) {}
  };

  const clearBatchRolloverTimeout = () => {
    if (batchRolloverTimeoutId) {
      window.clearTimeout(batchRolloverTimeoutId);
      batchRolloverTimeoutId = null;
    }
  };

  const resetRecordingState = () => {
    finalizeAccumulatedTimer();
    if (timerId) {
      window.clearInterval(timerId);
      timerId = null;
    }
    clearLiveChunkTimeout();
    clearBatchRolloverTimeout();
    resetSilencePromptState();
    startedAt = null;
    recordingTranscriptId = null;
    accumulatedBeforeCurrentSegmentMs = 0;
    renderTimer();
    captureMode = 'batch';
    batchCaptureGeneration += 1;
    batchSpeechSegments = [];
    batchForceRolloverRequested = false;
    batchStopRequested = false;
    batchRestartPending = false;
    batchRolloverUploadPending = false;
    liveStopRequested = false;
    liveForceContinueRequested = false;
    liveSpeechActive = false;
    liveRestartPending = false;
    livePendingOverlapAudio = null;
    liveChunkProcessing = Promise.resolve();
    liveLastChunkUploadStartedAt = 0;
    stopStreamTracks();
    cleanupLiveVad();
    cleanupBatchVad();
    resetMicVisualizer();
    setMicButtons(false);
  };

  const armLiveChunkTimeout = () => {
    clearLiveChunkTimeout();
    if (liveStopRequested) return;
    liveChunkTimeoutId = window.setTimeout(() => {
      if (!liveVadInstance || liveStopRequested) {
        return;
      }
      liveForceContinueRequested = true;
      setMicStatus('Thirty seconds of speech reached. Sending the current live chunk...');
      setSessionProgress('Thirty second speech window reached. Flushing the current live chunk...');
      try {
        liveVadInstance.pause();
      } catch (_) {
        resetRecordingState();
        setMicStatus('Could not flush the live speech segment.', 'error');
      }
    }, config.liveMaxChunkMs);
  };

  const trimLiveVadSamples = (audio, trailingTrimMs = 0) => {
    if (!(audio instanceof Float32Array)) {
      return new Float32Array();
    }
    if (trailingTrimMs <= 0) {
      return audio;
    }
    const trimSampleCount = Math.floor((trailingTrimMs / 1000) * config.liveVadSampleRate);
    if (trimSampleCount <= 0 || trimSampleCount >= audio.length) {
      return audio;
    }
    return audio.slice(0, audio.length - trimSampleCount);
  };

  const sampleCountForDurationMs = (durationMs) => {
    if (durationMs <= 0) {
      return 0;
    }
    return Math.floor((durationMs / 1000) * config.liveVadSampleRate);
  };

  const takeTailLiveVadSamples = (audio, durationMs) => {
    if (!(audio instanceof Float32Array) || audio.length === 0) {
      return new Float32Array();
    }
    const sampleCount = sampleCountForDurationMs(durationMs);
    if (sampleCount <= 0 || sampleCount >= audio.length) {
      return audio;
    }
    return audio.slice(audio.length - sampleCount);
  };

  const capLiveVadSamplesToMaxDuration = (audio) => {
    if (!(audio instanceof Float32Array) || audio.length === 0) {
      return new Float32Array();
    }
    const maxSampleCount = sampleCountForDurationMs(config.liveMaxChunkMs);
    if (maxSampleCount <= 0 || audio.length <= maxSampleCount) {
      return audio;
    }
    return audio.slice(audio.length - maxSampleCount);
  };

  const prependLiveVadOverlap = (audio) => {
    if (!(audio instanceof Float32Array)) {
      return new Float32Array();
    }
    if (!(livePendingOverlapAudio instanceof Float32Array) || livePendingOverlapAudio.length === 0) {
      return audio;
    }
    const merged = new Float32Array(livePendingOverlapAudio.length + audio.length);
    merged.set(livePendingOverlapAudio, 0);
    merged.set(audio, livePendingOverlapAudio.length);
    livePendingOverlapAudio = null;
    return merged;
  };

  const encodeWavPcm = (audio) => {
    const pcmData = new ArrayBuffer(44 + (audio.length * 2));
    const view = new DataView(pcmData);
    const writeString = (offset, value) => {
      for (let index = 0; index < value.length; index += 1) {
        view.setUint8(offset + index, value.charCodeAt(index));
      }
    };
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + (audio.length * 2), true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, config.liveVadSampleRate, true);
    view.setUint32(28, config.liveVadSampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, 'data');
    view.setUint32(40, audio.length * 2, true);

    let offset = 44;
    for (let index = 0; index < audio.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, audio[index]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
    return pcmData;
  };

  const createLiveVadWavBlob = (audio) => {
    if (!(audio instanceof Float32Array) || audio.length === 0) {
      return null;
    }
    return new Blob([encodeWavPcm(audio)], { type: 'audio/wav' });
  };

  const durationSecondsForVadAudio = (audio) => Math.max(0.1, audio.length / config.liveVadSampleRate);

  const sleep = (durationMs) => new Promise((resolve) => window.setTimeout(resolve, Math.max(0, durationMs)));

  const waitForLiveChunkUploadSlot = async () => {
    const minIntervalMs = Number(config.liveChunkUploadMinIntervalMs || 0);
    if (minIntervalMs <= 0 || liveLastChunkUploadStartedAt <= 0) return;
    const waitMs = minIntervalMs - (Date.now() - liveLastChunkUploadStartedAt);
    if (waitMs > 0) {
      await sleep(waitMs);
    }
  };

  const createVadMicStream = async () => {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return mediaStream;
  };

  const pauseVadMicStream = async () => {
    stopStreamTracks();
  };

  const commonVadCallbacks = ({ onSpeechStart, onSpeechEnd, onVADMisfire }) => ({
    getStream: createVadMicStream,
    pauseStream: pauseVadMicStream,
    resumeStream: createVadMicStream,
    onFrameProcessed: (probabilities, frame) => {
      setMicVisualizerLevels(buildMicVisualizerLevelsFromVadFrame(frame));
      setMicVisualizerVadActive(Boolean(probabilities && probabilities.isSpeech > probabilities.notSpeech));
    },
    onSpeechStart: () => {
      setMicVisualizerVadActive(true);
      markVadSpeechStarted();
      onSpeechStart?.();
    },
    onSpeechEnd: (audio) => {
      setMicVisualizerVadActive(false);
      onSpeechEnd?.(audio);
      markVadSpeechEndedOrIdle();
    },
    onVADMisfire: () => {
      setMicVisualizerVadActive(false);
      onVADMisfire?.();
      markVadSpeechEndedOrIdle();
    },
  });

  const concatVadAudioSegments = (segments) => {
    const validSegments = segments.filter((segment) => segment instanceof Float32Array && segment.length > 0);
    if (validSegments.length === 0) {
      return new Float32Array();
    }
    const totalSamples = validSegments.reduce((sum, segment) => sum + segment.length, 0);
    const combined = new Float32Array(totalSamples);
    let offset = 0;
    validSegments.forEach((segment) => {
      combined.set(segment, offset);
      offset += segment.length;
    });
    return combined;
  };

  const durationMsForVadSegments = (segments) => (
    (concatVadAudioSegments(segments).length / config.liveVadSampleRate) * 1000
  );

  const wavByteSizeForSampleCount = (sampleCount) => 44 + (Math.max(0, sampleCount) * 2);

  const byteSizeForVadSegments = (segments) => (
    wavByteSizeForSampleCount(concatVadAudioSegments(segments).length)
  );

  const batchRolloverEnabled = () => (
    Number(config.batchRolloverMaxDurationMs || 0) > 0
    || Number(config.batchRolloverMaxBytes || 0) > 0
  );

  const batchRolloverThresholdReached = (segments) => {
    if (!batchRolloverEnabled() || segments.length === 0) return false;
    const maxDurationMs = Number(config.batchRolloverMaxDurationMs || 0);
    const maxBytes = Number(config.batchRolloverMaxBytes || 0);
    return (
      (maxDurationMs > 0 && durationMsForVadSegments(segments) >= maxDurationMs)
      || (maxBytes > 0 && byteSizeForVadSegments(segments) >= maxBytes)
    );
  };

  const armBatchRolloverTimeout = () => {
    clearBatchRolloverTimeout();
    if (!batchRolloverEnabled() || batchStopRequested || !batchVadInstance) return;
    const maxDurationMs = Number(config.batchRolloverMaxDurationMs || 0);
    if (maxDurationMs <= 0) return;
    const elapsedMs = durationMsForVadSegments(batchSpeechSegments);
    const remainingMs = Math.max(config.liveMinChunkMs, maxDurationMs - elapsedMs);
    batchRolloverTimeoutId = window.setTimeout(() => {
      if (!batchVadInstance || batchStopRequested) return;
      batchForceRolloverRequested = true;
      setMicStatus('Recording limit nearly reached. Sending this part and continuing...');
      setSessionProgress('Recording part reached its limit. Uploading it, then continuing capture.');
      try {
        batchVadInstance.pause();
      } catch (_) {
        resetRecordingState();
        setMicStatus('Could not split the recording before the limit.', 'error');
      }
    }, remainingMs);
  };

  const uploadLiveChunk = async (blob, durationSeconds) => {
    clearSilencePromptTimer();
    hideSilencePrompt();
    const { transcriptId, nextLiveChunkSequenceNo } = getState();
    if (!transcriptId) {
      throw new Error('Select a live session before sending audio.');
    }
    const chunkSequenceNo = nextLiveChunkSequenceNo;
    setNextLiveChunkSequenceNo(chunkSequenceNo + 1);
    setMicStatus(`Sending live audio part ${chunkSequenceNo}...`);
    setVisibleStatus('sending chunk');
    setSessionProgress(`Sending live audio part ${chunkSequenceNo}...`);
    const formData = new FormData();
    formData.append('audio', blob, `live-chunk-${chunkSequenceNo}.wav`);
    formData.append('chunk_sequence_no', String(chunkSequenceNo));
    formData.append('declared_duration_seconds', durationSeconds.toFixed(3));
    const maxAttempts = 3;
    let lastMessage = 'Could not send this live audio part.';
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      await waitForLiveChunkUploadSlot();
      liveLastChunkUploadStartedAt = Date.now();
      const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/audio-chunks`, {
        method: 'POST',
        body: formData,
        credentials: 'include',
      });
      if (response.ok) {
        reflectBackendStatus('transcribing');
        scheduleWorkspaceRefreshBurst({ attempts: 90, minimumAttempts: 8 });
        return;
      }
      const errorResponse = await parseErrorResponse(response, 'Could not send this live audio part.');
      lastMessage = errorResponse.message;
      if (errorResponse.code !== 'rate_limited' || attempt === maxAttempts) {
        setNextLiveChunkSequenceNo(chunkSequenceNo);
        throw new Error(lastMessage);
      }
      setSessionProgress('Live upload rate limit reached. Waiting briefly, then retrying the same audio part...');
      setMicStatus('Live upload is catching up...');
      const retryAfterSeconds = Number(response.headers.get('Retry-After'));
      const retryDelayMs = Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
        ? retryAfterSeconds * 1000
        : Number(config.liveChunkRateLimitRetryMs || 1200);
      await sleep(retryDelayMs);
    }
    setNextLiveChunkSequenceNo(chunkSequenceNo);
    throw new Error(lastMessage);
  };

  const finalizeLiveCaptureIfNeeded = async ({ keepalive = false } = {}) => {
    clearSilencePromptTimer();
    hideSilencePrompt();
    const { transcriptId } = getState();
    if (!transcriptId || typeof finalizeLiveCapture !== 'function') {
      if (transcriptId) {
        scheduleWorkspaceRefreshBurst({ attempts: 45, minimumAttempts: 4 });
      }
      return;
    }
    setVisibleStatus('finalizing');
    setSessionProgress('Finalizing live capture and checking redaction...');
    try {
      await finalizeLiveCapture({ keepalive });
      setSessionProgress('Live capture finalized. Review identified PII before generating notes.');
      scheduleWorkspaceRefreshBurst({ attempts: 45, minimumAttempts: 4 });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not finalize live capture.';
      setMicStatus(message, 'error');
      showFlash(message, 'error');
      scheduleWorkspaceRefreshBurst({ attempts: 45, minimumAttempts: 4 });
    }
  };

  const buildLiveVadInstance = async () => {
    return window.vad.MicVAD.new({
      model: config.liveVadModel,
      preSpeechPadMs: config.livePreRollMs,
      redemptionMs: config.liveSilenceThresholdMs,
      minSpeechMs: config.liveMinChunkMs,
      submitUserSpeechOnPause: true,
      baseAssetPath: config.liveVadAssetBasePath,
      onnxWASMBasePath: config.liveVadOnnxBasePath,
      ...commonVadCallbacks({
        onSpeechStart: () => {
          liveSpeechActive = true;
          setVisibleStatus('speech detected');
          setSessionProgress('Speech detected. Preparing the next live audio part...');
          setMicStatus('Speech detected. Waiting for a pause or 30 second split...');
          armLiveChunkTimeout();
        },
        onSpeechEnd: (audio) => {
          clearLiveChunkTimeout();
          liveSpeechActive = false;
          const resumeAfterSegment = liveForceContinueRequested && !liveStopRequested;
          const stopAfterSegment = liveStopRequested;
          liveForceContinueRequested = false;
          queueLiveVadSegment(audio, {
            resumeAfterSegment,
            stopAfterSegment,
            trimTrailingMs: resumeAfterSegment || stopAfterSegment ? 0 : config.livePostRollTrimMs,
          });
        },
        onVADMisfire: () => {
          clearLiveChunkTimeout();
          liveSpeechActive = false;
          const shouldResume = liveForceContinueRequested && !liveStopRequested;
          const shouldStop = liveStopRequested;
          liveForceContinueRequested = false;
          if (shouldStop) {
            resetRecordingState();
            const micStatusState = getDefaultMicStatusState();
            setMicStatus(micStatusState.message, micStatusState.kind);
            void finalizeLiveCaptureIfNeeded();
            return;
          }
          if (shouldResume) {
            liveChunkProcessing = liveChunkProcessing.then(() => resumeLiveListeningAfterForcedFlush());
            return;
          }
          setMicStatus('Listening for speech...');
          setVisibleStatus('listening');
          setSessionProgress('Listening for speech. Live chunks will queue after a pause.');
        },
      }),
    });
  };

  const startLiveListeningLoop = () => {
    if (!liveVadInstance) {
      throw new Error('Live capture is not ready to start.');
    }
    liveVadInstance.start();
    setMicVisualizerVadActive(false);
    setVisibleStatus('listening');
    setSessionProgress('Listening for speech. The Silero browser VAD will queue live chunks after 2 seconds of silence.');
    setMicStatus('Listening for speech...');
    markVadSpeechEndedOrIdle();
  };

  const resumeLiveListeningAfterForcedFlush = async () => {
    if (liveStopRequested) return;
    try {
      liveRestartPending = true;
      cleanupLiveVad();
      setVisibleStatus('sending chunk');
      setSessionProgress('Live audio part sent. Getting ready for the next one...');
      setMicStatus('Getting live capture ready again...');
      if (config.liveRestartDelayMs > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, config.liveRestartDelayMs));
      }
      if (liveStopRequested) {
        resetRecordingState();
        const micStatusState = getDefaultMicStatusState();
        setMicStatus(micStatusState.message, micStatusState.kind);
        return;
      }
      liveVadInstance = await buildLiveVadInstance();
      liveRestartPending = false;
      startLiveListeningLoop();
    } catch (_) {
      liveRestartPending = false;
      resetRecordingState();
      setMicStatus('Could not restart live capture.', 'error');
    }
  };

  const processLiveVadSegment = async (audio, { trimTrailingMs = 0, resumeAfterSegment = false, stopAfterSegment = false } = {}) => {
    try {
      let preparedAudio = trimLiveVadSamples(audio, trimTrailingMs);
      if (!resumeAfterSegment) {
        preparedAudio = prependLiveVadOverlap(preparedAudio);
      }
      const uncappedDurationMs = (preparedAudio.length / config.liveVadSampleRate) * 1000;
      if (resumeAfterSegment) {
        livePendingOverlapAudio = takeTailLiveVadSamples(preparedAudio, config.liveChunkOverlapMs);
      }
      preparedAudio = capLiveVadSamplesToMaxDuration(preparedAudio);
      const durationMs = (preparedAudio.length / config.liveVadSampleRate) * 1000;
      if (durationMs >= config.liveMinChunkMs) {
        const blob = createLiveVadWavBlob(preparedAudio);
        if (blob && blob.size > 0) {
          if (resumeAfterSegment && uncappedDurationMs > config.liveMaxChunkMs) {
            setSessionProgress('Thirty second speech window reached. Sending the latest 30 seconds and keeping a 0.8 second overlap for the next live chunk...');
          }
          await uploadLiveChunk(blob, durationSecondsForVadAudio(preparedAudio));
        }
      }
      if (stopAfterSegment) {
        resetRecordingState();
        const micStatusState = getDefaultMicStatusState();
        setMicStatus(micStatusState.message, micStatusState.kind);
        await finalizeLiveCaptureIfNeeded();
        return;
      }
      if (resumeAfterSegment) {
        await resumeLiveListeningAfterForcedFlush();
        return;
      }
      setMicStatus('Listening for speech...');
      setVisibleStatus('listening');
      setSessionProgress('Live chunk queued. Listening for the next utterance...');
      markVadSpeechEndedOrIdle();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not send this live audio part.';
      setMicStatus(message, 'error');
      showFlash(message, 'error');
      reflectBackendStatus('failed', message);
      resetRecordingState();
    }
  };

  const queueLiveVadSegment = (audio, options = {}) => {
    liveChunkProcessing = liveChunkProcessing.then(() => processLiveVadSegment(audio, options));
    return liveChunkProcessing;
  };

  const activeBatchTranscriptId = () => recordingTranscriptId || getState().transcriptId || null;

  const uploadMicrophoneBatch = async (blob, { rollover = false, transcriptId = null } = {}) => {
    clearSilencePromptTimer();
    hideSilencePrompt();
    const uploadLabel = rollover ? 'recording part' : 'microphone recording';
    setMicStatus(`Uploading your ${uploadLabel}...`);
    setVisibleStatus('uploading');
    setSessionProgress(`Uploading your ${uploadLabel}...`);
    setRetryAvailability(false);
    try {
      const uploadTranscriptId = transcriptId || getState().transcriptId;
      if (!uploadTranscriptId) {
        throw new Error('Open consultation before sending microphone audio.');
      }
      if (uploadTranscriptId === getState().transcriptId) {
        await syncTranscriptTitleIfNeeded();
      }
      if (typeof uploadBatchAudio === 'function') {
        await uploadBatchAudio(blob, { transcriptId: uploadTranscriptId });
      } else {
        const formData = new FormData();
        formData.append('audio', blob, blob.type === 'audio/wav' ? 'microphone-batch.wav' : 'microphone-batch.webm');
        let response = null;
        let lastMessage = 'Could not send the microphone recording.';
        for (;;) {
          response = await csrfFetch(`/api/v1/transcripts/${uploadTranscriptId}/audio-file`, {
            method: 'POST',
            body: formData,
            credentials: 'include',
          });
          if (response.ok) break;
          lastMessage = await parseErrorMessage(response, 'Could not send the microphone recording.');
          if (!rollover || response.status !== 409) {
            throw new Error(lastMessage);
          }
          setSessionProgress('Previous recording part is still transcribing. Holding the next part locally, then retrying...');
          setMicStatus('Waiting for the previous recording part to finish...');
          await sleep(Number(config.batchRolloverConflictRetryMs || 5000));
        }
      }
      showFlash(rollover ? 'Recording part sent. Capture continues.' : (config.batchUploadSuccessMessage || 'Recording sent to be turned into text.'), 'success');
      await fetchWorkspace();
      scheduleWorkspaceRefreshBurst();
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not send the microphone recording.';
      setMicStatus(message, 'error');
      showFlash(message, 'error');
      reflectBackendStatus('failed', message);
      setRetryAvailability(false);
      return false;
    }
  };

  const queueMicrophoneBatchUpload = (blob, options = {}) => {
    batchUploadQueue = batchUploadQueue
      .catch(() => {})
      .then(() => uploadMicrophoneBatch(blob, options));
    return batchUploadQueue;
  };

  const buildBatchVadInstance = async () => {
    return window.vad.MicVAD.new({
      model: config.liveVadModel,
      preSpeechPadMs: config.batchVadPreRollMs,
      redemptionMs: config.batchVadSilenceThresholdMs,
      minSpeechMs: config.liveMinChunkMs,
      submitUserSpeechOnPause: true,
      baseAssetPath: config.liveVadAssetBasePath,
      onnxWASMBasePath: config.liveVadOnnxBasePath,
      ...commonVadCallbacks({
        onSpeechStart: () => {
          setVisibleStatus('recording');
          setSessionProgress('Speech detected. Keeping voiced audio locally until you stop.');
          setMicStatus('Speech detected. Voice-only batch capture is running...');
          armBatchRolloverTimeout();
        },
        onSpeechEnd: (audio) => {
          clearBatchRolloverTimeout();
          const preparedAudio = trimLiveVadSamples(audio, Math.max(0, config.batchVadSilenceThresholdMs - config.batchVadTrailingBufferMs));
          if (preparedAudio.length >= sampleCountForDurationMs(config.liveMinChunkMs)) {
            batchSpeechSegments.push(preparedAudio);
          }
          const shouldRollover = batchForceRolloverRequested || batchRolloverThresholdReached(batchSpeechSegments);
          batchForceRolloverRequested = false;
          if (shouldRollover && !batchStopRequested) {
            void rolloverMicrophoneBatchCapture();
            return;
          }
          if (!batchVadInstance) {
            return;
          }
          setVisibleStatus('recording');
          setSessionProgress('Listening for next speech segment. Only voiced audio with buffer is kept.');
          setMicStatus('Listening for speech...');
        },
        onVADMisfire: () => {
          clearBatchRolloverTimeout();
          if (batchForceRolloverRequested && !batchStopRequested && batchSpeechSegments.length > 0) {
            batchForceRolloverRequested = false;
            void rolloverMicrophoneBatchCapture();
            return;
          }
          batchForceRolloverRequested = false;
          if (!batchVadInstance) {
            return;
          }
          setVisibleStatus('recording');
          setSessionProgress('Listening for speech. Only voiced audio with buffer is kept.');
          setMicStatus('Listening for speech...');
        },
      }),
    });
  };

  const restartMicrophoneBatchAfterRollover = async () => {
    if (batchStopRequested) return;
    const restartGeneration = batchCaptureGeneration;
    try {
      batchRestartPending = true;
      cleanupBatchVad();
      batchVadInstance = await buildBatchVadInstance();
      if (batchStopRequested || batchCaptureGeneration !== restartGeneration) {
        batchRestartPending = false;
        cleanupBatchVad();
        return;
      }
      batchRestartPending = false;
      batchVadInstance.start();
      setMicVisualizerVadActive(false);
      setVisibleStatus('recording');
      setSessionProgress('New recording part started. Previous part is being uploaded for transcription.');
      setMicStatus('Recording restarted. Listening for speech...');
      markVadSpeechEndedOrIdle();
    } catch (_) {
      batchRestartPending = false;
      if (batchStopRequested || batchCaptureGeneration !== restartGeneration) {
        return;
      }
      resetRecordingState();
      setMicStatus('Could not restart recording after sending the previous part.', 'error');
    }
  };

  const rolloverMicrophoneBatchCapture = async () => {
    if (!batchVadInstance || batchSpeechSegments.length === 0) return;
    const combinedAudio = concatVadAudioSegments(batchSpeechSegments);
    const blob = createLiveVadWavBlob(combinedAudio);
    if (!blob || blob.size === 0) return;
    const transcriptId = activeBatchTranscriptId();
    const rolloverGeneration = batchCaptureGeneration;
    batchRestartPending = true;
    batchRolloverUploadPending = true;
    clearSilencePromptTimer();
    hideSilencePrompt();
    cleanupBatchVad();
    setMicStatus('Recording part ready. Uploading before capture continues...');
    setVisibleStatus('uploading');
    setSessionProgress('Sending recording part for transcription before the limit is reached. Capture will resume after this part is accepted.');
    const uploaded = await queueMicrophoneBatchUpload(blob, { rollover: true, transcriptId });
    batchRolloverUploadPending = false;
    if (batchCaptureGeneration !== rolloverGeneration) {
      batchRestartPending = false;
      return;
    }
    if (!uploaded) {
      batchRestartPending = false;
      batchStopRequested = true;
      clearBatchRolloverTimeout();
      resetSilencePromptState();
      stopStreamTracks();
      resetMicVisualizer();
      finalizeAccumulatedTimer();
      if (timerId) {
        window.clearInterval(timerId);
        timerId = null;
      }
      setMicButtons(false);
      setVisibleStatus('failed');
      setSessionProgress('Recording stopped because this part could not be uploaded. No later audio was recorded after the failed part.');
      return;
    }
    if (batchStopRequested) {
      batchRestartPending = false;
      resetRecordingState();
      const micStatusState = getDefaultMicStatusState();
      setMicStatus(micStatusState.message, micStatusState.kind);
      return;
    }
    batchSpeechSegments = [];
    batchRestartPending = false;
    await restartMicrophoneBatchAfterRollover();
  };

  const finalizeMicrophoneBatchCapture = async () => {
    const transcriptId = activeBatchTranscriptId();
    const combinedAudio = concatVadAudioSegments(batchSpeechSegments);
    resetRecordingState();
    if (combinedAudio.length === 0) {
      setMicStatus('No voice activity was captured.', 'error');
      return;
    }
    const blob = createLiveVadWavBlob(combinedAudio);
    if (!blob || blob.size === 0) {
      setMicStatus('No voice activity was captured.', 'error');
      return;
    }
    setMicStatus('Voice activity captured. Preparing upload...', 'success');
    await queueMicrophoneBatchUpload(blob, { transcriptId });
  };

  const beginLiveTranscription = async () => {
    if (!canUseLiveInput()) {
      setMicStatus('Open a consultation in live capture mode before starting.', 'error');
      return;
    }
    if (!window.vad?.MicVAD || !navigator.mediaDevices?.getUserMedia) {
      reportMicIssue?.({ name: 'NotFoundError' });
      setMicStatus('Live speech detection could not start in this browser.', 'error');
      return;
    }
    try {
      captureMode = 'live';
      liveStopRequested = false;
      liveForceContinueRequested = false;
      liveRestartPending = false;
      liveChunkProcessing = Promise.resolve();
      liveVadInstance = await buildLiveVadInstance();
      reportMicIssue?.(null);
      beginAccumulatedTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      startLiveListeningLoop();
    } catch (error) {
      resetRecordingState();
      reportMicIssue?.(error);
      setMicStatus('Microphone access was denied, or live speech detection could not start.', 'error');
    }
  };

  const endLiveTranscription = () => {
    if (!liveVadInstance && !liveRestartPending) return;
    liveStopRequested = true;
    liveForceContinueRequested = false;
    clearLiveChunkTimeout();
    clearSilencePromptTimer();
    hideSilencePrompt();
    setMicStatus('Stopping live recording...');
    setSessionProgress('Finishing the current speech segment...');
      if (liveRestartPending && !liveSpeechActive) {
        resetRecordingState();
        const micStatusState = getDefaultMicStatusState();
        setMicStatus(micStatusState.message, micStatusState.kind);
        void finalizeLiveCaptureIfNeeded();
        return;
      }
    try {
      liveVadInstance?.pause();
      if (!liveSpeechActive) {
        resetRecordingState();
        const micStatusState = getDefaultMicStatusState();
        setMicStatus(micStatusState.message, micStatusState.kind);
        void finalizeLiveCaptureIfNeeded();
      }
    } catch (_) {
      resetRecordingState();
      const micStatusState = getDefaultMicStatusState();
      setMicStatus(micStatusState.message, micStatusState.kind);
      void finalizeLiveCaptureIfNeeded();
    }
  };

  const beginMicrophoneBatch = async () => {
    if (!canUseWholeFileInput()) {
      setMicStatus('Open a consultation in uploaded recording mode before using the microphone here.', 'error');
      return;
    }
    if (!window.vad?.MicVAD || !navigator.mediaDevices?.getUserMedia) {
      reportMicIssue?.({ name: 'NotFoundError' });
      setMicStatus('Voice-only microphone capture could not start in this browser.', 'error');
      return;
    }
    try {
      captureMode = 'batch';
      batchCaptureGeneration += 1;
      batchStopRequested = false;
      batchForceRolloverRequested = false;
      batchRestartPending = false;
      batchRolloverUploadPending = false;
      batchSpeechSegments = [];
      batchVadInstance = await buildBatchVadInstance();
      reportMicIssue?.(null);
      beginAccumulatedTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      batchVadInstance.start();
      setMicVisualizerVadActive(false);
      setMicStatus('Listening for speech. Voice-only capture keeps buffered speech until you stop.');
      setVisibleStatus('recording');
      setSessionProgress('Listening on this device. Silence is skipped; voiced audio with buffer stays local until upload.');
      markVadSpeechEndedOrIdle();
    } catch (error) {
      resetRecordingState();
      reportMicIssue?.(error);
      setMicStatus('Microphone access was denied or unavailable.', 'error');
    }
  };

  const endMicrophoneBatch = () => {
    if (!batchVadInstance && !batchRestartPending) return;
    batchStopRequested = true;
    clearBatchRolloverTimeout();
    clearSilencePromptTimer();
    hideSilencePrompt();
    setMicStatus('Stopping voice-only microphone recording...');
    setVisibleStatus('uploading');
    setSessionProgress('Finishing your last speech segment...');
    const instance = batchVadInstance;
    batchVadInstance = null;
    try {
      if (!instance) {
        if (batchRolloverUploadPending) {
          setSessionProgress('Stopping after the in-flight recording part upload finishes...');
          return;
        }
        window.setTimeout(() => {
          void finalizeMicrophoneBatchCapture();
        }, 0);
        return;
      }
      instance.pause();
      window.setTimeout(() => {
        void finalizeMicrophoneBatchCapture();
      }, 0);
    } catch (_) {
      resetRecordingState();
      setMicStatus('Could not finish voice-only microphone capture.', 'error');
    }
  };

  const handleRecordToggle = async () => {
    const liveCaptureActive = captureMode === 'live' && (Boolean(liveVadInstance) || liveRestartPending);
    const batchCaptureActive = Boolean(batchVadInstance) || batchRestartPending;
    if (liveCaptureActive || batchCaptureActive) {
      if (captureMode === 'live') {
        endLiveTranscription();
      } else {
        endMicrophoneBatch();
      }
      return;
    }
    if (typeof confirmBeforeStartRecording === 'function') {
      if (recordStartGuardInFlight) return;
      recordStartGuardInFlight = true;
      let canStart = false;
      try {
        canStart = await confirmBeforeStartRecording();
      } finally {
        recordStartGuardInFlight = false;
      }
      if (!canStart) return;
    }
    if (getState().activeIngestionMode === 'live_chunked') {
      await beginLiveTranscription();
    } else {
      await beginMicrophoneBatch();
    }
  };

  const attachDomListeners = () => {
    ensureMicVisualizerBars();
    resetMicVisualizer();
    if (dom.audioActionTrigger) {
      dom.audioActionTrigger.addEventListener('click', () => {
        if (!canUseWholeFileInput()) return;
        dom.fileInput?.click();
      });
    }

    if (dom.recordToggleButton) {
      dom.recordToggleButton.addEventListener('click', () => {
        void handleRecordToggle();
      });
    }

    if (dom.silencePromptDismiss) {
      dom.silencePromptDismiss.addEventListener('click', () => {
        silencePromptDismissedForCurrentSilentInterval = true;
        clearSilencePromptTimer();
        hideSilencePrompt();
      });
    }

    if (dom.fileInput && dom.uploadForm) {
      dom.fileInput.addEventListener('change', () => {
        if (!dom.fileInput.files || dom.fileInput.files.length === 0) {
          return;
        }
        setVisibleStatus('uploading');
        setSessionProgress('Uploading the recording so it can be turned into text...');
        dom.uploadForm.requestSubmit();
      });
    }
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden || captureMode !== 'live' || !liveVadInstance || liveStopRequested) {
        return;
      }
      liveForceContinueRequested = true;
      clearSilencePromptTimer();
      hideSilencePrompt();
      setSessionProgress('Tab moved to background. Flushing live capture before browser throttling can delay it...');
      try {
        liveVadInstance.pause();
      } catch (_) {
        resetRecordingState();
        setMicStatus('Could not keep live capture running after tab backgrounding.', 'error');
      }
    });
    renderTimer();
  };

  const handlePageLifecycleExit = () => {
    const liveCaptureActive = (
      captureMode === 'live'
      && !liveStopRequested
      && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)
    );
    if (!liveCaptureActive) {
      return false;
    }

    liveStopRequested = true;
    liveForceContinueRequested = false;
    clearLiveChunkTimeout();
    resetSilencePromptState();
    finalizeAccumulatedTimer();
    if (timerId) {
      window.clearInterval(timerId);
      timerId = null;
    }
    setMicButtons(false);
    setVisibleStatus('transcribing');
    setSessionProgress('Browser is closing or unloading this tab. Finalizing live capture with uploaded chunks...');
    try {
      liveVadInstance?.pause();
    } catch (_) {}
    stopStreamTracks();
    cleanupLiveVad();
    void finalizeLiveCaptureIfNeeded({ keepalive: true });
    return true;
  };

  return {
    attachDomListeners,
    handlePageLifecycleExit,
    syncDisplayedDuration: renderTimer,
    isCaptureUiActive: () => (
      (
        captureMode === 'live'
        && !liveStopRequested
        && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)
      )
      || (
        captureMode === 'batch'
        && !batchStopRequested
        && (Boolean(batchVadInstance) || batchRestartPending)
      )
    ),
    isLiveCaptureUiActive: () => (
      getState().activeIngestionMode === 'live_chunked'
      && captureMode === 'live'
      && !liveStopRequested
      && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)
    ),
    shouldPollWhileLiveCaptureActive: () => (
      getState().activeIngestionMode === 'live_chunked'
      && captureMode === 'live'
      && (Boolean(liveVadInstance) || liveRestartPending)
    ),
    shouldPreserveLiveMicStatus: () => (
      (
        getState().activeIngestionMode === 'live_chunked'
        && captureMode === 'live'
        && !liveStopRequested
        && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)
      )
      || (
        getState().activeIngestionMode === 'live_chunked'
        && captureMode === 'live'
        && !liveStopRequested
        && (
          getState().latestIngestionJobStatus === 'queued'
          || getState().latestIngestionJobStatus === 'processing'
        )
      )
    ),
  };
}
