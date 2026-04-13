export function createAudioCaptureController({
  dom,
  config,
  canUseLiveInput,
  canUseWholeFileInput,
  getState,
  setNextLiveChunkSequenceNo,
  getDefaultMicStatusState,
  syncTranscriptTitleIfNeeded,
  fetchWorkspace,
  pollWorkspace,
  scheduleWorkspaceRefreshBurst,
  parseErrorMessage,
  setMicButtons,
  setMicStatus,
  setVisibleStatus,
  setSessionProgress,
  setRetryAvailability,
  showFlash,
  reflectBackendStatus,
}) {
  const RECORDING_DURATION_STORAGE_KEY = 'openscribe-glm2-recording-durations';
  const MIC_VISUALIZER_BAR_COUNT = 12;
  let mediaStream = null;
  let captureMode = 'batch';
  let liveVadInstance = null;
  let batchVadInstance = null;
  let batchSpeechSegments = [];
  let liveChunkTimeoutId = null;
  let liveStopRequested = false;
  let liveForceContinueRequested = false;
  let liveSpeechActive = false;
  let liveRestartPending = false;
  let livePendingOverlapAudio = null;
  let liveChunkProcessing = Promise.resolve();
  let startedAt = null;
  let timerId = null;
  let recordingTranscriptId = null;
  let accumulatedBeforeCurrentSegmentMs = 0;
  let micVisualizerBars = [];
  let micVisualizerLevels = Array(MIC_VISUALIZER_BAR_COUNT).fill(0.14);

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

  const resetRecordingState = () => {
    finalizeAccumulatedTimer();
    if (timerId) {
      window.clearInterval(timerId);
      timerId = null;
    }
    clearLiveChunkTimeout();
    startedAt = null;
    recordingTranscriptId = null;
    accumulatedBeforeCurrentSegmentMs = 0;
    renderTimer();
    captureMode = 'batch';
    batchSpeechSegments = [];
    liveStopRequested = false;
    liveForceContinueRequested = false;
    liveSpeechActive = false;
    liveRestartPending = false;
    livePendingOverlapAudio = null;
    liveChunkProcessing = Promise.resolve();
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
      onSpeechStart?.();
    },
    onSpeechEnd: (audio) => {
      setMicVisualizerVadActive(false);
      onSpeechEnd?.(audio);
    },
    onVADMisfire: () => {
      setMicVisualizerVadActive(false);
      onVADMisfire?.();
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

  const uploadLiveChunk = async (blob, durationSeconds) => {
    const { transcriptId, nextLiveChunkSequenceNo } = getState();
    if (!transcriptId) {
      throw new Error('Select a live session before sending audio.');
    }
    const chunkSequenceNo = nextLiveChunkSequenceNo;
    setNextLiveChunkSequenceNo(chunkSequenceNo + 1);
    setMicStatus(`Sending live audio part ${chunkSequenceNo}...`);
    setVisibleStatus('uploading');
    setSessionProgress(`Sending live audio part ${chunkSequenceNo}...`);
    const formData = new FormData();
    formData.append('audio', blob, `live-chunk-${chunkSequenceNo}.wav`);
    formData.append('chunk_sequence_no', String(chunkSequenceNo));
    formData.append('declared_duration_seconds', durationSeconds.toFixed(3));
    const response = await fetch(`/api/v1/transcripts/${transcriptId}/audio-chunks`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    });
    if (!response.ok) {
      setNextLiveChunkSequenceNo(chunkSequenceNo);
      throw new Error(await parseErrorMessage(response, 'Could not send this live audio part.'));
    }
    reflectBackendStatus('transcribing');
    scheduleWorkspaceRefreshBurst({ attempts: 90, minimumAttempts: 8 });
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
          setVisibleStatus('recording');
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
            return;
          }
          if (shouldResume) {
            liveChunkProcessing = liveChunkProcessing.then(() => resumeLiveListeningAfterForcedFlush());
            return;
          }
          setMicStatus('Listening for speech...');
          setVisibleStatus('idle');
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
    setVisibleStatus('idle');
    setSessionProgress('Listening for speech. The Silero browser VAD will queue live chunks after 2 seconds of silence.');
    setMicStatus('Listening for speech...');
  };

  const resumeLiveListeningAfterForcedFlush = async () => {
    if (liveStopRequested) return;
    try {
      liveRestartPending = true;
      cleanupLiveVad();
      setVisibleStatus('transcribing');
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
        if (getState().transcriptId) {
          scheduleWorkspaceRefreshBurst({ attempts: 45, minimumAttempts: 4 });
        }
        return;
      }
      if (resumeAfterSegment) {
        await resumeLiveListeningAfterForcedFlush();
        return;
      }
      setMicStatus('Listening for speech...');
      setVisibleStatus('idle');
      setSessionProgress('Live chunk queued. Listening for the next utterance...');
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

  const uploadMicrophoneBatch = async (blob) => {
    setMicStatus('Uploading your microphone recording...');
    setVisibleStatus('uploading');
    setSessionProgress('Uploading your microphone recording...');
    setRetryAvailability(false);
    try {
      await syncTranscriptTitleIfNeeded();
      const { transcriptId } = getState();
      const formData = new FormData();
      formData.append('audio', blob, blob.type === 'audio/wav' ? 'microphone-batch.wav' : 'microphone-batch.webm');
      const response = await fetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
        method: 'POST',
        body: formData,
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(await parseErrorMessage(response, 'Could not send the microphone recording.'));
      }
      showFlash('Recording sent to be turned into text.', 'success');
      await fetchWorkspace();
      scheduleWorkspaceRefreshBurst();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not send the microphone recording.';
      setMicStatus(message, 'error');
      showFlash(message, 'error');
      reflectBackendStatus('failed', message);
      setRetryAvailability(false);
    }
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
        },
        onSpeechEnd: (audio) => {
          const preparedAudio = trimLiveVadSamples(audio, Math.max(0, config.batchVadSilenceThresholdMs - config.batchVadTrailingBufferMs));
          if (preparedAudio.length >= sampleCountForDurationMs(config.liveMinChunkMs)) {
            batchSpeechSegments.push(preparedAudio);
          }
          if (!batchVadInstance) {
            return;
          }
          setVisibleStatus('recording');
          setSessionProgress('Listening for next speech segment. Only voiced audio with buffer is kept.');
          setMicStatus('Listening for speech...');
        },
        onVADMisfire: () => {
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

  const finalizeMicrophoneBatchCapture = async () => {
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
    await uploadMicrophoneBatch(blob);
  };

  const beginLiveTranscription = async () => {
    if (!canUseLiveInput()) {
      setMicStatus('Open a consultation in live capture mode before starting.', 'error');
      return;
    }
    if (!window.vad?.MicVAD || !navigator.mediaDevices?.getUserMedia) {
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
      beginAccumulatedTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      startLiveListeningLoop();
    } catch (_) {
      resetRecordingState();
      setMicStatus('Microphone access was denied, or live speech detection could not start.', 'error');
    }
  };

  const endLiveTranscription = () => {
    if (!liveVadInstance && !liveRestartPending) return;
    liveStopRequested = true;
    liveForceContinueRequested = false;
    clearLiveChunkTimeout();
    setMicStatus('Stopping live recording...');
    setSessionProgress('Finishing the current speech segment...');
    if (liveRestartPending && !liveSpeechActive) {
      resetRecordingState();
      const micStatusState = getDefaultMicStatusState();
      setMicStatus(micStatusState.message, micStatusState.kind);
      return;
    }
    try {
      liveVadInstance?.pause();
      if (!liveSpeechActive) {
        resetRecordingState();
        const micStatusState = getDefaultMicStatusState();
        setMicStatus(micStatusState.message, micStatusState.kind);
      }
    } catch (_) {
      resetRecordingState();
      const micStatusState = getDefaultMicStatusState();
      setMicStatus(micStatusState.message, micStatusState.kind);
    }
  };

  const beginMicrophoneBatch = async () => {
    if (!canUseWholeFileInput()) {
      setMicStatus('Open a consultation in uploaded recording mode before using the microphone here.', 'error');
      return;
    }
    if (!window.vad?.MicVAD || !navigator.mediaDevices?.getUserMedia) {
      setMicStatus('Voice-only microphone capture could not start in this browser.', 'error');
      return;
    }
    try {
      captureMode = 'batch';
      batchSpeechSegments = [];
      batchVadInstance = await buildBatchVadInstance();
      beginAccumulatedTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      batchVadInstance.start();
      setMicVisualizerVadActive(false);
      setMicStatus('Listening for speech. Voice-only capture keeps buffered speech until you stop.');
      setVisibleStatus('recording');
      setSessionProgress('Listening on this device. Silence is skipped; voiced audio with buffer stays local until upload.');
    } catch (_) {
      resetRecordingState();
      setMicStatus('Microphone access was denied or unavailable.', 'error');
    }
  };

  const endMicrophoneBatch = () => {
    if (!batchVadInstance) return;
    setMicStatus('Stopping voice-only microphone recording...');
    setVisibleStatus('uploading');
    setSessionProgress('Finishing your last speech segment...');
    const instance = batchVadInstance;
    batchVadInstance = null;
    try {
      instance.pause();
      window.setTimeout(() => {
        void finalizeMicrophoneBatchCapture();
      }, 0);
    } catch (_) {
      resetRecordingState();
      setMicStatus('Could not finish voice-only microphone capture.', 'error');
    }
  };

  const handleRecordToggle = () => {
    const liveCaptureActive = captureMode === 'live' && (Boolean(liveVadInstance) || liveRestartPending);
    const batchCaptureActive = Boolean(batchVadInstance);
    if (liveCaptureActive || batchCaptureActive) {
      if (captureMode === 'live') {
        endLiveTranscription();
      } else {
        endMicrophoneBatch();
      }
      return;
    }
    if (getState().activeIngestionMode === 'live_chunked') {
      beginLiveTranscription();
    } else {
      beginMicrophoneBatch();
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
      dom.recordToggleButton.addEventListener('click', handleRecordToggle);
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
    renderTimer();
  };

  return {
    attachDomListeners,
    syncDisplayedDuration: renderTimer,
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
