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
  let mediaRecorder = null;
  let mediaStream = null;
  let mediaChunks = [];
  let captureMode = 'batch';
  let liveVadInstance = null;
  let liveChunkTimeoutId = null;
  let liveStopRequested = false;
  let liveForceContinueRequested = false;
  let liveSpeechActive = false;
  let liveRestartPending = false;
  let livePendingOverlapAudio = null;
  let liveChunkProcessing = Promise.resolve();
  let startedAt = null;
  let timerId = null;

  const renderTimer = () => {
    if (!dom.micTimer) return;
    if (!startedAt) {
      dom.micTimer.textContent = '00:00';
      return;
    }
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    const minutes = String(Math.floor(elapsedSeconds / 60)).padStart(2, '0');
    const seconds = String(elapsedSeconds % 60).padStart(2, '0');
    dom.micTimer.textContent = `${minutes}:${seconds}`;
  };

  const stopStreamTracks = () => {
    if (!mediaStream) return;
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
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

  const resetRecordingState = () => {
    if (timerId) {
      window.clearInterval(timerId);
      timerId = null;
    }
    clearLiveChunkTimeout();
    startedAt = null;
    renderTimer();
    mediaRecorder = null;
    mediaChunks = [];
    captureMode = 'batch';
    liveStopRequested = false;
    liveForceContinueRequested = false;
    liveSpeechActive = false;
    liveRestartPending = false;
    livePendingOverlapAudio = null;
    liveChunkProcessing = Promise.resolve();
    stopStreamTracks();
    cleanupLiveVad();
    setMicButtons(false);
  };

  const chooseMimeType = () => {
    if (!window.MediaRecorder) return '';
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    return candidates.find((candidate) => window.MediaRecorder.isTypeSupported(candidate)) || '';
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
    });
  };

  const startLiveListeningLoop = () => {
    if (!liveVadInstance) {
      throw new Error('Live capture is not ready to start.');
    }
    liveVadInstance.start();
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
      formData.append('audio', blob, blob.type.includes('mp4') ? 'microphone-batch.mp4' : 'microphone-batch.webm');
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
      startedAt = Date.now();
      renderTimer();
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
    if (!window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) {
      setMicStatus('This browser cannot record audio from the microphone.', 'error');
      return;
    }
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = chooseMimeType();
      mediaChunks = [];
      mediaRecorder = mimeType ? new MediaRecorder(mediaStream, { mimeType }) : new MediaRecorder(mediaStream);
      mediaRecorder.addEventListener('dataavailable', (event) => {
        if (event.data && event.data.size > 0) {
          mediaChunks.push(event.data);
        }
      });
      mediaRecorder.addEventListener('stop', async () => {
        const blob = new Blob(mediaChunks, { type: mediaRecorder?.mimeType || mimeType || 'audio/webm' });
        resetRecordingState();
        if (blob.size === 0) {
          setMicStatus('No microphone audio was captured.', 'error');
          return;
        }
        setMicStatus('Recording finished.', 'success');
        await uploadMicrophoneBatch(blob);
      });
      mediaRecorder.start();
      startedAt = Date.now();
      renderTimer();
      timerId = window.setInterval(renderTimer, 1000);
      setMicButtons(true);
      setMicStatus('Recording from the microphone...');
      setVisibleStatus('recording');
      setSessionProgress('Recording on this device. Nothing has been sent yet.');
    } catch (_) {
      resetRecordingState();
      setMicStatus('Microphone access was denied or unavailable.', 'error');
    }
  };

  const endMicrophoneBatch = () => {
    if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
    setMicStatus('Stopping microphone recording...');
    setVisibleStatus('uploading');
    setSessionProgress('Preparing your recording...');
    mediaRecorder.stop();
  };

  const handleRecordToggle = () => {
    const liveCaptureActive = captureMode === 'live' && (Boolean(liveVadInstance) || liveRestartPending);
    const batchCaptureActive = Boolean(mediaRecorder && mediaRecorder.state !== 'inactive');
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
  };

  return {
    attachDomListeners,
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
