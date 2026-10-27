import { csrfFetch, csrfToken } from '../csrf.js';
import { DeepgramLiveStream, trimPinnedVadSpeechBuffer } from './deepgramLiveStream.js?v=20261017-deepgram-live-stream';
import { createMicVisualizer } from './micVisualizer.js?v=20261026-shared-mic-visualizer';
import { createOwnedMicStream, createVadCaptureCallbacks, createVadCaptureOptions } from './vadCapture.js?v=20261027-shared-vad-capture';

const createWavPcmBuffer = (sampleCount, sampleRate) => {
  const pcmData = new ArrayBuffer(44 + (sampleCount * 2));
  const view = new DataView(pcmData);
  const writeString = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + (sampleCount * 2), true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, sampleCount * 2, true);
  return { pcmData, view };
};

export function createLivePcm16WavBlob(samples, sampleRate = 16000) {
  if (!(samples instanceof Int16Array) || samples.length === 0) return null;
  const { pcmData, view } = createWavPcmBuffer(samples.length, sampleRate);
  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(44 + (index * 2), samples[index], true);
  }
  return new Blob([pcmData], { type: 'audio/wav' });
}

export function createAudioCaptureController({
  dom,
  config,
  uploadBatchAudio,
  canUseLiveInput,
  canUseWholeFileInput,
  getState,
  beforeUploadFileSelected,
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
  onCaptureStopped,
}) {
  let workspaceRecordingEventState = 'stopped';
  const dispatchWorkspaceRecordingEvent = (state, detail = undefined) => {
    if (state === workspaceRecordingEventState && state !== 'failed') return;
    workspaceRecordingEventState = state === 'started' ? 'started' : 'stopped';
    document.dispatchEvent(new CustomEvent(`openscribe:recording-${state}`, detail ? { detail } : undefined));
  };
  const RECORDING_DURATION_STORAGE_KEY = 'openscribe-glm2-recording-durations';
  const micVisualizer = createMicVisualizer(dom.micVisualizer);
  const VAD_SILENCE_PROMPT_MS = 30000;
  let mediaStream = null;
  let captureMode = 'batch';
  let liveVadInstance = null;
  let liveListeningStarted = false;
  const pendingLiveMicStreams = new Map();
  let deepgramStream = null;
  let deepgramStreamTranscriptId = null;
  let deepgramConnectPending = false;
  let liveCaptureGeneration = 0;
  let deepgramRecovery = null;
  let deepgramFinishPromise = null;
  let batchVadInstance = null;
  const batchVadStreamReleasers = new WeakMap();
  let batchCaptureSettings = null;
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
  let silencePromptTimeoutId = null;
  let silencePromptDismissedForCurrentSilentInterval = false;
  let vadSpeechCurrentlyActive = false;
  let pendingAudioRetry = null;
  const pendingAudioRetryOwner = 'capture';
  const sharedPendingAudioRetry = () => dom.pendingAudioRetryButton?._openscribePendingAudioRetry || null;

  const newUploadIdempotencyKey = () => {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    if (!window.crypto?.getRandomValues) return null;
    const bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };

  const clearPendingAudioRetry = () => {
    pendingAudioRetry = null;
    if (sharedPendingAudioRetry()?.owner === pendingAudioRetryOwner) {
      dom.pendingAudioRetryButton._openscribePendingAudioRetry = null;
    }
    if (dom.pendingAudioRetryRegion) dom.pendingAudioRetryRegion.hidden = true;
    if (dom.pendingAudioRetryButton) dom.pendingAudioRetryButton.disabled = false;
    if (dom.pendingAudioRetryMessage) dom.pendingAudioRetryMessage.textContent = '';
  };

  const showPendingAudioRetry = (message, retry, transcriptId = null, discard = null) => {
    pendingAudioRetry = { retry, transcriptId, discard };
    if (dom.pendingAudioRetryButton) {
      dom.pendingAudioRetryButton._openscribePendingAudioRetry = {
        owner: pendingAudioRetryOwner,
        retry,
        transcriptId,
        discard: async () => {
          if (typeof discard === 'function') await discard();
          clearPendingAudioRetry();
          setMicStatus('Pending audio discarded.');
          setSessionProgress('Pending audio discarded. You can choose or record replacement audio.');
        },
      };
    }
    if (dom.pendingAudioRetryMessage) {
      dom.pendingAudioRetryMessage.textContent = `${message} Audio is still available in this tab.`;
    }
    if (dom.pendingAudioRetryRegion) dom.pendingAudioRetryRegion.hidden = false;
    if (dom.pendingAudioRetryButton) dom.pendingAudioRetryButton.disabled = false;
  };

  const isRecoverableUploadResponse = (response) => response.status === 429 || response.status >= 500;

  const uploadRetryDelayMs = (attempt, response = null) => {
    const retryAfterSeconds = Number(response?.headers?.get?.('Retry-After'));
    if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
      return Math.min(retryAfterSeconds * 1000, 10000);
    }
    return Math.min(2000 * (2 ** Math.max(0, attempt - 1)), 10000);
  };

  const sleepForUploadRetry = (delayMs) => new Promise((resolve) => window.setTimeout(resolve, delayMs));

  const submitAudioWithRecovery = async ({ send, fallbackMessage, progressMessage, canReplay = true, retryConflict = false, conflictRetryDelayMs = 0 }) => {
    const maxAttempts = canReplay ? 3 : 1;
    let lastError = new Error(fallbackMessage);
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      let response = null;
      try {
        response = await send();
        if (response.ok) return true;
        const errorResponse = typeof parseErrorResponse === 'function'
          ? await parseErrorResponse(response, fallbackMessage)
          : { message: await parseErrorMessage(response, fallbackMessage) };
        lastError = new Error(errorResponse.message || fallbackMessage);
        if (!isRecoverableUploadResponse(response) && !(retryConflict && response.status === 409)) throw lastError;
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(fallbackMessage);
        if (response && !isRecoverableUploadResponse(response) && !(retryConflict && response.status === 409)) throw lastError;
      }
      if (attempt === maxAttempts) break;
      const waitingForPriorPart = retryConflict && response?.status === 409;
      setSessionProgress(waitingForPriorPart
        ? `Previous recording part is still transcribing. Retrying this audio part automatically (${attempt} of 2)...`
        : `${progressMessage} Retrying automatically (${attempt} of 2)...`);
      setMicStatus(waitingForPriorPart ? 'Waiting for the previous recording part to finish...' : 'Upload connection interrupted. Retrying...');
      await sleepForUploadRetry(waitingForPriorPart
        ? Math.max(0, Number(conflictRetryDelayMs || 0))
        : uploadRetryDelayMs(attempt, response));
    }
    throw lastError;
  };

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

  const setMicVisualizerVadActive = micVisualizer.setVadActive;
  const resetMicVisualizer = micVisualizer.reset;

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


  const clearLiveChunkTimeout = () => {
    if (liveChunkTimeoutId) {
      window.clearTimeout(liveChunkTimeoutId);
      liveChunkTimeoutId = null;
    }
  };

  const cleanupLiveVad = () => {
    const instance = liveVadInstance;
    liveVadInstance = null;
    liveListeningStarted = false;
    if (!instance) return;
    try {
      if (typeof instance.destroy === 'function') {
        instance.destroy();
      } else {
        instance.pause();
      }
    } catch (_) {}
  };

  const stopPendingLiveMicStreams = (generation = null) => {
    for (const [stream, streamGeneration] of pendingLiveMicStreams) {
      if (generation !== null && streamGeneration !== generation) continue;
      try { stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
      pendingLiveMicStreams.delete(stream);
    }
  };

  const disposePreparedLiveVad = (instance) => {
    // startOnLoad:false means this instance has not acquired a microphone.
    // vad-web's destroy can throw before audio nodes exist, so release it on
    // a best-effort basis and let the unreferenced model state be collected.
    try { instance?.destroy?.(); } catch (_) {}
  };

  const cleanupBatchVad = () => {
    const instance = batchVadInstance;
    batchVadInstance = null;
    disposeBatchVad(instance);
  };

  const disposeBatchVad = (instance) => {
    if (!instance) return;
    try {
      if (typeof instance.destroy === 'function') {
        instance.destroy();
      } else {
        instance.pause();
      }
    } catch (_) {}
    const releaseStream = batchVadStreamReleasers.get(instance);
    batchVadStreamReleasers.delete(instance);
    if (releaseStream) void releaseStream();
  };

  const clearBatchRolloverTimeout = () => {
    if (batchRolloverTimeoutId) {
      window.clearTimeout(batchRolloverTimeoutId);
      batchRolloverTimeoutId = null;
    }
  };

  const resetRecordingState = () => {
    const stoppingLiveGeneration = liveCaptureGeneration;
    liveCaptureGeneration += 1;
    deepgramConnectPending = false;
    liveListeningStarted = false;
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
    batchCaptureSettings = null;
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
    deepgramStream = null;
    deepgramStreamTranscriptId = null;
    deepgramFinishPromise = null;
    stopStreamTracks();
    stopPendingLiveMicStreams(stoppingLiveGeneration);
    cleanupLiveVad();
    cleanupBatchVad();
    resetMicVisualizer();
    setMicButtons(false);
    dispatchWorkspaceRecordingEvent('stopped');
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
    const { pcmData, view } = createWavPcmBuffer(audio.length, config.liveVadSampleRate);

    for (let index = 0; index < audio.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, audio[index]));
      view.setInt16(44 + (index * 2), sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
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

  const commonVadCallbacks = ({
    onFrameProcessed, onSpeechStart, onSpeechEnd, onVADMisfire,
    isCurrent = () => true,
    isCurrentSpeechEnd = isCurrent,
  }) => ({
    getStream: createVadMicStream,
    pauseStream: pauseVadMicStream,
    resumeStream: createVadMicStream,
    ...createVadCaptureCallbacks({
      isCurrent,
      isCurrentSpeechEnd,
      onFrameProcessed: (probabilities, frame) => {
      micVisualizer.updateFrame(frame);
      setMicVisualizerVadActive(Boolean(probabilities && probabilities.isSpeech > probabilities.notSpeech));
      onFrameProcessed?.(probabilities, frame);
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
    }),
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
    const idempotencyKey = newUploadIdempotencyKey();
    const submit = async () => {
      const formData = new FormData();
      formData.append('audio', blob, `live-chunk-${chunkSequenceNo}.wav`);
      formData.append('chunk_sequence_no', String(chunkSequenceNo));
      formData.append('declared_duration_seconds', durationSeconds.toFixed(3));
      await waitForLiveChunkUploadSlot();
      liveLastChunkUploadStartedAt = Date.now();
      return csrfFetch(`/api/v1/transcripts/${transcriptId}/audio-chunks`, {
        method: 'POST',
        body: formData,
        credentials: 'include',
        headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
      });
    };
    const retry = async () => {
      if (dom.pendingAudioRetryButton) dom.pendingAudioRetryButton.disabled = true;
      try {
        await submitAudioWithRecovery({
          send: submit,
          fallbackMessage: 'Could not send this live audio part.',
          progressMessage: 'Live audio upload was interrupted.',
          canReplay: Boolean(idempotencyKey),
        });
        setNextLiveChunkSequenceNo(chunkSequenceNo + 1);
        clearPendingAudioRetry();
        reflectBackendStatus('transcribing');
        scheduleWorkspaceRefreshBurst({ attempts: 90, minimumAttempts: 8 });
      } catch (error) {
        setNextLiveChunkSequenceNo(chunkSequenceNo);
        const message = error instanceof Error ? error.message : 'Could not send this live audio part.';
        showPendingAudioRetry(message, retry, transcriptId);
        throw error;
      }
    };
    try {
      await retry();
    } catch (error) {
      setNextLiveChunkSequenceNo(chunkSequenceNo);
      throw error;
    }
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

  const buildLiveVadInstance = async ({ streaming = false, stream = null, generation = liveCaptureGeneration, startOnLoad = !getState().deepgramLiveStreamingAvailable } = {}) => {
    const isCurrentCapture = () => generation === liveCaptureGeneration;
    const transcriptId = getState().transcriptId;
    const canAcquireLiveMic = () => isCurrentCapture()
      && !liveStopRequested
      && transcriptId === getState().transcriptId;
    let streamingFrameProcessor = null;
    const vadOptions = createVadCaptureOptions({
      model: config.liveVadModel,
      positiveSpeechThreshold: 0.3,
      startOnLoad,
      preSpeechPadMs: config.livePreRollMs,
      redemptionMs: config.liveSilenceThresholdMs,
      minSpeechMs: config.liveMinChunkMs,
      submitUserSpeechOnPause: true,
      baseAssetPath: config.liveVadAssetBasePath,
      onnxWASMBasePath: config.liveVadOnnxBasePath,
      getStream: createVadMicStream,
      pauseStream: pauseVadMicStream,
      resumeStream: createVadMicStream,
      isCurrent: isCurrentCapture,
      isCurrentSpeechEnd: isCurrentCapture,
      ...commonVadCallbacks({
        onFrameProcessed: (probabilities, frame) => {
          if (!isCurrentCapture()) return;
          if (streaming) {
            if (!trimPinnedVadSpeechBuffer(streamingFrameProcessor, frame, config.liveVadSampleRate, 5)) {
              stream?.interrupt();
              return;
            }
            stream?.pushFrame(frame, Number(probabilities?.isSpeech) >= 0.3);
          }
        },
        onSpeechStart: () => {
          if (!isCurrentCapture()) return;
          liveSpeechActive = true;
          if (streaming) {
            stream?.speechStarted();
            setVisibleStatus('speech detected');
            setSessionProgress('Speech detected. Streaming live audio to the selected speech service...');
            setMicStatus('Speech detected. Live audio is streaming as it is captured.');
            return;
          }
          setVisibleStatus('speech detected');
          setSessionProgress('Speech detected. Preparing the next live audio part...');
          setMicStatus('Speech detected. Waiting for a pause or 30 second split...');
          armLiveChunkTimeout();
        },
        onSpeechEnd: (audio) => {
          if (!isCurrentCapture()) return;
          clearLiveChunkTimeout();
          liveSpeechActive = false;
          if (streaming) {
            if (stream && liveStopRequested) void finishDeepgramCapture();
            return;
          }
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
          if (!isCurrentCapture()) return;
          clearLiveChunkTimeout();
          liveSpeechActive = false;
          if (streaming) {
            if (stream && liveStopRequested) void finishDeepgramCapture();
            return;
          }
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
    if (streaming) {
      const ownedMic = createOwnedMicStream({
        acquire: () => navigator.mediaDevices.getUserMedia({ audio: true }),
        canAcquire: canAcquireLiveMic,
        onAcquire: (microphoneStream) => {
          mediaStream = microphoneStream;
          pendingLiveMicStreams.set(microphoneStream, generation);
        },
        onRelease: (microphoneStream) => {
          pendingLiveMicStreams.delete(microphoneStream);
          if (mediaStream === microphoneStream) mediaStream = null;
        },
      });
      vadOptions.getStream = ownedMic.getStream;
      vadOptions.resumeStream = ownedMic.resumeStream;
      vadOptions.pauseStream = ownedMic.pauseStream;
    }
    const vadInstance = await window.vad.MicVAD.new(vadOptions);
    if (streaming) {
      // This integration relies on vad-web 0.0.29's pinned FrameProcessor
      // callback order: onFrameProcessed runs immediately before audioBuffer.push.
      streamingFrameProcessor = vadInstance?.frameProcessor;
      if (!Array.isArray(streamingFrameProcessor?.audioBuffer)) {
        throw new Error('Live audio detector buffer is unavailable.');
      }
    }
    return vadInstance;
  };

  const deepgramStreamErrorMessage = (error) => {
    switch (error?.code) {
      case 'quota_exceeded':
      case 'quota_disabled':
        return 'Live transcription stopped because your team has no transcription quota available. Contact your team administrator.';
      case 'unauthorized':
      case 'forbidden':
      case 'not_found':
      case 'stream_csrf_invalid':
      case 'stream_session_expired':
        return 'Your session expired or access changed. Sign in again, then reopen this consultation.';
      case 'stt_config_secret_missing':
      case 'stream_provider_ineligible':
      case 'invalid_config':
        return 'Live transcription is unavailable with the selected speech-service settings. Ask your team administrator to review them.';
      default:
        return 'Live transcription stopped. Check the connection and try again.';
    }
  };

  const failDeepgramCapture = (error, { streamId = null, samples = null, unresolved = false, recoveryAvailable = false } = {}) => {
    const transcriptId = deepgramStreamTranscriptId;
    const currentStream = deepgramStream;
    deepgramStream = null;
    deepgramFinishPromise = null;
    liveStopRequested = true;
    const safeMessage = deepgramStreamErrorMessage(error);
    setMicStatus(safeMessage, 'error');
    setVisibleStatus('failed');
    setSessionProgress(streamId
      ? 'Live transcription stopped. Checking the server for an unfinished interval; no audio was replayed.'
      : 'Live transcription stopped. No automatic replay was started.');
    resetRecordingState();
    dispatchWorkspaceRecordingEvent('failed');
    deepgramRecovery = null;
    const exactSamples = samples instanceof Int16Array ? samples : null;
    if (streamId && transcriptId && unresolved && !recoveryAvailable && exactSamples?.length) {
      deepgramRecovery = { streamId, transcriptId, samples: exactSamples };
      const recover = async () => {
        const pending = deepgramRecovery;
        if (!pending || pending.transcriptId !== getState().transcriptId) {
          throw new Error('Open the original consultation to retry this audio.');
        }
        let body;
        if (pending.samples) {
          body = new FormData();
          body.append('audio', createLivePcm16WavBlob(pending.samples, config.liveVadSampleRate), 'live-recovery.wav');
          body.append('declared_duration_seconds', (pending.samples.length / config.liveVadSampleRate).toFixed(6));
        }
        const response = await csrfFetch(`/api/v1/transcripts/${pending.transcriptId}/deepgram-stream/${pending.streamId}/retry`, {
          method: 'POST', credentials: 'include', body,
        });
        if (!response.ok) throw new Error(await parseErrorMessage(response, 'Could not retry the unresolved live interval.'));
        deepgramRecovery = null;
        clearPendingAudioRetry();
        reflectBackendStatus('transcribing');
        scheduleWorkspaceRefreshBurst({ attempts: 90, minimumAttempts: 8 });
      };
      const discard = async () => {
        const pending = deepgramRecovery;
        if (!pending || pending.transcriptId !== getState().transcriptId) {
          throw new Error('Open the original consultation to discard this audio.');
        }
        const response = await csrfFetch(`/api/v1/transcripts/${pending.transcriptId}/deepgram-stream/${pending.streamId}/discard`, {
          method: 'POST', credentials: 'include',
        });
        if (!response.ok) throw new Error(await parseErrorMessage(response, 'Could not discard the unresolved live interval.'));
        deepgramRecovery = null;
      };
      showPendingAudioRetry('The unfinished interval is available only in this tab for explicit retry.', recover, transcriptId, discard);
    } else if (streamId && transcriptId && unresolved) {
      setSessionProgress('The server is checking the unfinished interval. Refresh the consultation to retry retained audio or discard it.');
      void pollWorkspace();
    } else if (streamId && transcriptId) {
      void pollWorkspace();
    }
    if (streamId || unresolved) scheduleWorkspaceRefreshBurst({ attempts: 90, minimumAttempts: 8 });
    void currentStream;
  };

  const finishDeepgramCapture = () => {
    if (deepgramFinishPromise) return deepgramFinishPromise;
    if (!deepgramStream) return Promise.resolve(null);
    liveStopRequested = true;
    clearLiveChunkTimeout();
    clearSilencePromptTimer();
    hideSilencePrompt();
    setVisibleStatus('finalizing');
    setMicStatus('Finalizing live transcription...');
    setSessionProgress('Finalizing the current live transcription window...');
    deepgramFinishPromise = (async () => {
      try {
        if (liveVadInstance) await liveVadInstance.pause();
        const result = await deepgramStream?.finish();
        deepgramStream?.onInterim('');
        deepgramStream = null;
        deepgramStreamTranscriptId = null;
        resetRecordingState();
        setMicStatus('Live transcription finalized.', 'success');
        setSessionProgress('Live transcription is saved. Review the transcript before generating notes.');
        reflectBackendStatus('transcribing');
        scheduleWorkspaceRefreshBurst({ attempts: 90, minimumAttempts: 8 });
        return result;
      } catch (error) {
        if (deepgramStream) deepgramStream.interrupt();
        return null;
      }
    })();
    return deepgramFinishPromise;
  };

  const startLiveListeningLoop = async ({ generation = liveCaptureGeneration, transcriptId = getState().transcriptId } = {}) => {
    if (!liveVadInstance) {
      throw new Error('Live capture is not ready to start.');
    }
    const instance = liveVadInstance;
    try {
      await instance.start();
    } catch (error) {
      stopPendingLiveMicStreams(generation);
      throw error;
    }
    if (generation !== liveCaptureGeneration || instance !== liveVadInstance || liveStopRequested || transcriptId !== getState().transcriptId) {
      stopPendingLiveMicStreams(generation);
      try { await instance.pause(); } catch (_) {}
      try { instance.destroy?.(); } catch (_) {}
      return false;
    }
    // MicVAD now owns the acquired stream. A reset that raced with startup
    // has already stopped it through stopPendingLiveMicStreams().
    for (const [pendingStream, streamGeneration] of pendingLiveMicStreams) {
      if (streamGeneration === generation) pendingLiveMicStreams.delete(pendingStream);
    }
    liveListeningStarted = true;
    setMicVisualizerVadActive(false);
    setVisibleStatus('listening');
    setSessionProgress('Listening for speech. The Silero browser VAD will queue live chunks after 2 seconds of silence.');
    setMicStatus('Listening for speech...');
    markVadSpeechEndedOrIdle();
    return true;
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
      await startLiveListeningLoop();
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

  const uploadMicrophoneBatch = async (blob, { rollover = false, transcriptId = null, captureSettingsId = null } = {}) => {
    clearSilencePromptTimer();
    hideSilencePrompt();
    const uploadLabel = rollover ? 'recording part' : 'microphone recording';
    setMicStatus(`Uploading your ${uploadLabel}...`);
    setVisibleStatus('uploading');
    setSessionProgress(`Uploading your ${uploadLabel}...`);
    setRetryAvailability(false);
    const uploadTranscriptId = transcriptId || getState().transcriptId;
    if (!uploadTranscriptId) {
      const message = 'Open consultation before sending microphone audio.';
      setMicStatus(message, 'error');
      showPendingAudioRetry(message, () => uploadMicrophoneBatch(blob, { rollover, transcriptId, captureSettingsId }), transcriptId);
      return false;
    }
    const idempotencyKey = newUploadIdempotencyKey();
    const retry = async () => {
      if (dom.pendingAudioRetryButton) dom.pendingAudioRetryButton.disabled = true;
      try {
        if (uploadTranscriptId === getState().transcriptId) {
          await syncTranscriptTitleIfNeeded();
        }
        if (typeof uploadBatchAudio === 'function') {
          await uploadBatchAudio(blob, { transcriptId: uploadTranscriptId, idempotencyKey, captureSettingsId });
        } else {
          await submitAudioWithRecovery({
            send: () => {
              const formData = new FormData();
              formData.append('audio', blob, blob.type === 'audio/wav' ? 'microphone-batch.wav' : 'microphone-batch.webm');
              if (captureSettingsId) formData.append('capture_settings_id', captureSettingsId);
              return csrfFetch(`/api/v1/transcripts/${uploadTranscriptId}/audio-file`, {
                method: 'POST',
                body: formData,
                credentials: 'include',
                headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
              });
            },
            fallbackMessage: 'Could not send the microphone recording.',
            progressMessage: 'Microphone recording upload was interrupted.',
            canReplay: Boolean(idempotencyKey),
            retryConflict: rollover,
            conflictRetryDelayMs: Number(config.batchRolloverConflictRetryMs || 5000),
          });
        }
        clearPendingAudioRetry();
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
        showPendingAudioRetry(message, retry, uploadTranscriptId);
      return false;
      }
    };
    try {
      return await retry();
    } catch (_) {
      return false;
    }
  };

  const queueMicrophoneBatchUpload = (blob, options = {}) => {
    batchUploadQueue = batchUploadQueue
      .catch(() => {})
      .then(() => uploadMicrophoneBatch(blob, options));
    return batchUploadQueue;
  };

  const buildBatchVadInstance = async ({ generation = batchCaptureGeneration, transcriptId = getState().transcriptId } = {}) => {
    const isCurrentGeneration = () => generation === batchCaptureGeneration
      && transcriptId === getState().transcriptId;
    const canAcquireMic = () => isCurrentGeneration() && !batchStopRequested;
    let ownedMicStream = null;
    const getBatchMicStream = async () => {
      if (!canAcquireMic()) throw new Error('Batch microphone startup was canceled.');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!canAcquireMic()) {
        try { stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
        throw new Error('Batch microphone startup was canceled.');
      }
      ownedMicStream = stream;
      mediaStream = stream;
      return stream;
    };
    const pauseBatchMicStream = async (stream = ownedMicStream) => {
      const target = stream || ownedMicStream;
      if (!target) return;
      try { target.getTracks().forEach((track) => track.stop()); } catch (_) {}
      if (ownedMicStream === target) ownedMicStream = null;
      if (mediaStream === target) mediaStream = null;
    };
    try {
      const instance = await window.vad.MicVAD.new({
        model: config.liveVadModel,
        preSpeechPadMs: config.batchVadPreRollMs,
        redemptionMs: config.batchVadSilenceThresholdMs,
        minSpeechMs: config.liveMinChunkMs,
        submitUserSpeechOnPause: true,
        baseAssetPath: config.liveVadAssetBasePath,
        onnxWASMBasePath: config.liveVadOnnxBasePath,
        ...commonVadCallbacks({
          isCurrent: canAcquireMic,
          isCurrentSpeechEnd: isCurrentGeneration,
          onSpeechStart: () => {
            if (!isCurrentGeneration()) return;
            setVisibleStatus('recording');
            setSessionProgress('Speech detected. Keeping voiced audio locally until you stop.');
            setMicStatus('Speech detected. Voice-only batch capture is running...');
            armBatchRolloverTimeout();
          },
          onSpeechEnd: (audio) => {
            if (!isCurrentGeneration()) return;
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
            if (!isCurrentGeneration()) return;
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
        getStream: getBatchMicStream,
        resumeStream: getBatchMicStream,
        pauseStream: pauseBatchMicStream,
      });
      if (instance && (typeof instance === 'object' || typeof instance === 'function')) {
        batchVadStreamReleasers.set(instance, () => pauseBatchMicStream());
      }
      return instance;
    } catch (error) {
      await pauseBatchMicStream();
      throw error;
    }
  };

  const restartMicrophoneBatchAfterRollover = async () => {
    if (batchStopRequested) return;
    const restartGeneration = batchCaptureGeneration;
    const transcriptId = activeBatchTranscriptId();
    let preparedVad = null;
    try {
      batchRestartPending = true;
      cleanupBatchVad();
      preparedVad = await buildBatchVadInstance({ generation: restartGeneration, transcriptId });
      if (batchStopRequested || batchCaptureGeneration !== restartGeneration || transcriptId !== getState().transcriptId) {
        disposeBatchVad(preparedVad);
        return;
      }
      batchVadInstance = preparedVad;
      preparedVad = null;
      batchRestartPending = false;
      batchVadInstance.start();
      setMicVisualizerVadActive(false);
      setVisibleStatus('recording');
      setSessionProgress('New recording part started. Previous part is being uploaded for transcription.');
      setMicStatus('Recording restarted. Listening for speech...');
      markVadSpeechEndedOrIdle();
    } catch (_) {
      if (batchStopRequested || batchCaptureGeneration !== restartGeneration || transcriptId !== getState().transcriptId) {
        disposeBatchVad(preparedVad);
        return;
      }
      batchRestartPending = false;
      disposeBatchVad(preparedVad);
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
    const uploaded = await queueMicrophoneBatchUpload(blob, {
      rollover: true,
      transcriptId,
      captureSettingsId: batchCaptureSettings?.captureSettingsId || null,
    });
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
      dispatchWorkspaceRecordingEvent('failed');
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
    const captureSettingsId = batchCaptureSettings?.captureSettingsId || null;
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
    await queueMicrophoneBatchUpload(blob, { transcriptId, captureSettingsId });
  };

  const beginLiveTranscription = async () => {
    if (deepgramConnectPending || deepgramStream) return;
    if (sharedPendingAudioRetry()) {
      setMicStatus('Retry the pending audio upload before starting another recording.', 'error');
      return;
    }
    if (!canUseLiveInput()) {
      setMicStatus('Open a consultation in live capture mode before starting.', 'error');
      return;
    }
    if (!window.vad?.MicVAD || !navigator.mediaDevices?.getUserMedia) {
      reportMicIssue?.({ name: 'NotFoundError' });
      setMicStatus('Live speech detection could not start in this browser.', 'error');
      return;
    }
    const streamingRequested = Boolean(getState().deepgramLiveStreamingAvailable);
    const captureGeneration = ++liveCaptureGeneration;
    let streamingInstance = null;
    try {
      captureMode = 'live';
      setMicButtons(true);
      deepgramConnectPending = streamingRequested;
      liveStopRequested = false;
      liveForceContinueRequested = false;
      liveRestartPending = false;
      liveChunkProcessing = Promise.resolve();
      const streamTranscriptId = getState().transcriptId;
      if (streamingRequested) {
        deepgramStreamTranscriptId = streamTranscriptId;
        streamingInstance = new DeepgramLiveStream({
          onInterim: (text) => {
            if (deepgramStreamTranscriptId !== getState().transcriptId) return;
            if (dom.deepgramInterim) {
              dom.deepgramInterim.textContent = text;
              dom.deepgramInterim.hidden = !text;
            }
          },
          onWindowCommitted: () => {
            if (dom.deepgramInterim) {
              dom.deepgramInterim.textContent = '';
              dom.deepgramInterim.hidden = true;
            }
            void pollWorkspace();
          },
          onState: (state) => {
            if (state === 'streaming') {
              setVisibleStatus('transcribing');
              setMicStatus('Speech detected. Streaming live audio...');
              setSessionProgress('Speech audio is streaming. Live words are provisional until saved.');
            } else if (state === 'listening' && liveListeningStarted) {
              setVisibleStatus('listening');
              setMicStatus('Listening for speech...');
              setSessionProgress('Listening locally. Audio pauses after 30 seconds without speech; a short pre-roll stays in this tab.');
            }
          },
          onError: (error, recovery) => failDeepgramCapture(error, recovery),
        });
        deepgramStream = streamingInstance;
        setVisibleStatus('connecting');
        setMicStatus('Connecting to live transcription...');
        setSessionProgress('Connecting securely to the selected Deepgram speech service...');
        const vadPreparation = buildLiveVadInstance({
          streaming: true,
          stream: streamingInstance,
          generation: captureGeneration,
          startOnLoad: false,
        });
        let vadInstance;
        let preparationAbandoned = false;
        try {
          const [, preparedVad] = await Promise.all([
            streamingInstance.connect({ transcriptId: streamTranscriptId, csrfToken: csrfToken() }),
            vadPreparation,
          ]);
          vadInstance = preparedVad;
        } catch (error) {
          // Promise.all rejects as soon as either side fails. Observe and
          // dispose any model instance that completes after the attempt fails.
          preparationAbandoned = true;
          void vadPreparation.then((lateVad) => {
            if (preparationAbandoned || captureGeneration !== liveCaptureGeneration) {
              disposePreparedLiveVad(lateVad);
            }
          }, () => {});
          throw error;
        }
        if (captureGeneration !== liveCaptureGeneration || streamTranscriptId !== getState().transcriptId || !getState().deepgramLiveStreamingAvailable) {
          disposePreparedLiveVad(vadInstance);
          streamingInstance.cancelPending();
          return;
        }
        liveVadInstance = vadInstance;
      } else {
        liveVadInstance = await buildLiveVadInstance({
          streaming: false,
          generation: captureGeneration,
        });
      }
      if (captureGeneration !== liveCaptureGeneration) {
        cleanupLiveVad();
        return;
      }
      if (!streamingRequested) deepgramConnectPending = false;
      reportMicIssue?.(null);
      if (!await startLiveListeningLoop({ generation: captureGeneration, transcriptId: streamTranscriptId })) return;
      deepgramConnectPending = false;
      beginAccumulatedTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      dispatchWorkspaceRecordingEvent('started');
    } catch (error) {
      if (captureGeneration !== liveCaptureGeneration) return;
      if (deepgramStream) {
        deepgramStream.interrupt();
      } else if (streamingRequested && captureMode === 'batch') {
        reportMicIssue?.(error);
        return;
      } else {
        resetRecordingState();
        dispatchWorkspaceRecordingEvent('failed');
      }
      reportMicIssue?.(error);
      if (!deepgramRecovery) setMicStatus('Microphone access was denied, or live speech detection could not start.', 'error');
    } finally {
      if (captureGeneration === liveCaptureGeneration) deepgramConnectPending = false;
    }
  };

  const endLiveTranscription = () => {
    if (deepgramConnectPending && !liveVadInstance) {
      const pending = deepgramStream;
      resetRecordingState();
      pending?.cancelPending();
      setMicStatus('Live transcription connection canceled.');
      setSessionProgress('The live connection was canceled before microphone capture began.');
      return;
    }
    if (!liveVadInstance && !liveRestartPending && !deepgramStream) return;
    if (deepgramStream) {
      void finishDeepgramCapture();
      return;
    }
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

  const stopDeepgramCaptureForContextChange = () => {
    if (deepgramStream) deepgramStream.interrupt();
  };

  const beginMicrophoneBatch = async () => {
    if (sharedPendingAudioRetry()) {
      setMicStatus('Retry the pending audio upload before starting another recording.', 'error');
      return;
    }
    if (!canUseWholeFileInput()) {
      setMicStatus('Open a consultation in uploaded recording mode before using the microphone here.', 'error');
      return;
    }
    if (!window.vad?.MicVAD || !navigator.mediaDevices?.getUserMedia) {
      reportMicIssue?.({ name: 'NotFoundError' });
      setMicStatus('Voice-only microphone capture could not start in this browser.', 'error');
      return;
    }
    const captureGeneration = ++batchCaptureGeneration;
    const transcriptId = getState().transcriptId;
    let preparedVad = null;
    try {
      captureMode = 'batch';
      batchStopRequested = false;
      batchForceRolloverRequested = false;
      batchRolloverUploadPending = false;
      batchSpeechSegments = [];
      batchCaptureSettings = null;
      batchRestartPending = true;
      if (!transcriptId) throw new Error('Open a consultation before starting microphone recording.');
      const captureSettingsResponse = await csrfFetch(`/api/v1/transcripts/${encodeURIComponent(transcriptId)}/capture-settings`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!captureSettingsResponse.ok) {
        throw new Error(await parseErrorMessage(captureSettingsResponse, 'Could not prepare the recording settings.'));
      }
      const captureSettings = await captureSettingsResponse.json();
      if (!captureSettings?.capture_settings_id || captureSettings.connection_type !== 'batch'
        || typeof captureSettings.diarisation_enabled !== 'boolean') {
        throw new Error('The server did not confirm the recording settings.');
      }
      if (captureGeneration !== batchCaptureGeneration || batchStopRequested) return;
      if (transcriptId !== getState().transcriptId) {
        resetRecordingState();
        return;
      }
      batchCaptureSettings = {
        captureSettingsId: captureSettings.capture_settings_id,
        diarizationEnabled: captureSettings.diarisation_enabled,
      };
      recordingTranscriptId = transcriptId;
      preparedVad = await buildBatchVadInstance({ generation: captureGeneration, transcriptId });
      if (captureGeneration !== batchCaptureGeneration || batchStopRequested) {
        disposeBatchVad(preparedVad);
        return;
      }
      if (transcriptId !== getState().transcriptId) {
        disposeBatchVad(preparedVad);
        resetRecordingState();
        return;
      }
      batchVadInstance = preparedVad;
      preparedVad = null;
      batchRestartPending = false;
      reportMicIssue?.(null);
      beginAccumulatedTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      dispatchWorkspaceRecordingEvent('started', { diarisation_enabled: batchCaptureSettings.diarizationEnabled });
      batchVadInstance.start();
      setMicVisualizerVadActive(false);
      setMicStatus('Listening for speech. Voice-only capture keeps buffered speech until you stop.');
      setVisibleStatus('recording');
      setSessionProgress('Listening on this device. Silence is skipped; voiced audio with buffer stays local until upload.');
      markVadSpeechEndedOrIdle();
    } catch (error) {
      disposeBatchVad(preparedVad);
      if (captureGeneration !== batchCaptureGeneration || batchStopRequested) return;
      if (transcriptId !== getState().transcriptId) {
        resetRecordingState();
        return;
      }
      resetRecordingState();
      dispatchWorkspaceRecordingEvent('failed');
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
      dispatchWorkspaceRecordingEvent('failed');
      setMicStatus('Could not finish voice-only microphone capture.', 'error');
    }
  };

  const handleRecordToggle = async () => {
    const liveCaptureActive = captureMode === 'live'
      && (Boolean(liveVadInstance) || liveRestartPending || deepgramConnectPending || Boolean(deepgramStream));
    const batchCaptureActive = Boolean(batchVadInstance) || batchRestartPending;
    if (liveCaptureActive || batchCaptureActive) {
      onCaptureStopped?.();
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
      dom.fileInput.addEventListener('change', async () => {
        if (!dom.fileInput.files || dom.fileInput.files.length === 0) {
          return;
        }
        if (typeof beforeUploadFileSelected === 'function') {
          const canContinue = await beforeUploadFileSelected();
          if (!canContinue) return;
        }
        setVisibleStatus('uploading');
        setSessionProgress('Uploading the recording so it can be turned into text...');
        dom.uploadForm.requestSubmit();
      });
    }
    dom.pendingAudioRetryButton?.addEventListener('click', () => {
      const pending = sharedPendingAudioRetry();
      if (pending?.owner !== pendingAudioRetryOwner) return;
      const retry = pending.retry;
      if (typeof retry !== 'function' || (pending.transcriptId && pending.transcriptId !== getState().transcriptId)) return;
      dom.pendingAudioRetryButton.disabled = true;
      void Promise.resolve(retry()).catch((error) => {
        if (dom.pendingAudioRetryMessage && error instanceof Error) dom.pendingAudioRetryMessage.textContent = error.message;
        if (dom.pendingAudioRetryButton) dom.pendingAudioRetryButton.disabled = false;
      });
    });
    dom.pendingAudioDiscardButton?.addEventListener('click', () => {
      const pending = sharedPendingAudioRetry();
      if (pending?.owner !== pendingAudioRetryOwner || typeof pending.discard !== 'function') return;
      if (!window.confirm('Discard this pending audio? It cannot be recovered after you discard it.')) return;
      dom.pendingAudioDiscardButton.disabled = true;
      void Promise.resolve(pending.discard()).catch((error) => {
        if (dom.pendingAudioRetryMessage && error instanceof Error) dom.pendingAudioRetryMessage.textContent = error.message;
        if (dom.pendingAudioDiscardButton) dom.pendingAudioDiscardButton.disabled = false;
      });
    });
    document.addEventListener('transcribe:active-transcript-changed', () => {
      if (deepgramStream && deepgramStreamTranscriptId !== getState().transcriptId) {
        deepgramStream.interrupt();
      }
      const pending = sharedPendingAudioRetry();
      if (pending?.owner !== pendingAudioRetryOwner) return;
      const visible = !pending.transcriptId || pending.transcriptId === getState().transcriptId;
      if (dom.pendingAudioRetryRegion) dom.pendingAudioRetryRegion.hidden = !visible;
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden || captureMode !== 'live' || !liveVadInstance || liveStopRequested) {
        return;
      }
      if (deepgramStream) {
        setSessionProgress('Live transcription continues in the background. Keep the tab open until the stream is finalized.');
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
      && (deepgramConnectPending || Boolean(deepgramStream)
        || (!liveStopRequested && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)))
    );
    if (!liveCaptureActive) {
      return false;
    }

    if (deepgramConnectPending && !liveVadInstance) {
      const pending = deepgramStream;
      resetRecordingState();
      pending?.cancelPending();
      return true;
    }

    if (deepgramStream) {
      liveStopRequested = true;
      clearLiveChunkTimeout();
      resetSilencePromptState();
      finalizeAccumulatedTimer();
      if (timerId) {
        window.clearInterval(timerId);
        timerId = null;
      }
      setMicButtons(false);
      setVisibleStatus('finalizing');
      setSessionProgress('Finalizing live transcription as this tab closes...');
      try {
        liveVadInstance?.pause();
        void deepgramStream.finish().catch(() => {});
      } catch (_) {}
      stopStreamTracks();
      cleanupLiveVad();
      return true;
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
        && (deepgramConnectPending || Boolean(deepgramStream)
          || (!liveStopRequested && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)))
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
      && (deepgramConnectPending || Boolean(deepgramStream)
        || (!liveStopRequested && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)))
    ),
    shouldPollWhileLiveCaptureActive: () => (
      getState().activeIngestionMode === 'live_chunked'
      && captureMode === 'live'
      && (deepgramConnectPending || Boolean(deepgramStream) || Boolean(liveVadInstance) || liveRestartPending)
    ),
    shouldPreserveLiveMicStatus: () => (
      (
        getState().activeIngestionMode === 'live_chunked'
        && captureMode === 'live'
        && (deepgramConnectPending || Boolean(deepgramStream)
          || (!liveStopRequested && (Boolean(liveVadInstance) || liveRestartPending || liveSpeechActive)))
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
    stopDeepgramCaptureForContextChange,
  };
}
