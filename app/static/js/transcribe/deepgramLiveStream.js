const FRAME_VERSION = 1;
const FRAME_HEADER_BYTES = 13;
const MAX_BATCH_SAMPLES = 16000;
const BATCH_TARGET_SAMPLES = 8000;
const RING_LIMIT_SAMPLES = 5 * 16000;
const OUTBOUND_LIMIT_SAMPLES = 10 * 16000;
const RECOVERY_LIMIT_SAMPLES = 30 * 16000;
const SOCKET_BUFFER_LIMIT_BYTES = 128 * 1024;

const protocolError = (message) => new Error(message);

// The pinned vad-web 0.0.29 FrameProcessor emits FrameProcessed before adding
// the frame to audioBuffer. Trim old frames at that callback boundary so the
// processor's speech segment stays bounded without resetting its VAD state.
export function trimPinnedVadSpeechBuffer(frameProcessor, frame, sampleRate = 16000, retainedSeconds = 5) {
  const buffer = frameProcessor?.audioBuffer;
  if (!Array.isArray(buffer) || !Number.isSafeInteger(frame?.length) || frame.length <= 0
      || !Number.isFinite(sampleRate) || sampleRate <= 0
      || !Number.isFinite(retainedSeconds) || retainedSeconds <= 0) {
    return false;
  }
  const maxFramesBeforePush = Math.max(0, Math.ceil((retainedSeconds * sampleRate) / frame.length) - 1);
  while (buffer.length > maxFramesBeforePush) buffer.shift();
  return true;
}

export function deepgramStreamWebSocketUrl(transcriptId, location = window.location) {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${location.host}/api/v1/transcripts/${encodeURIComponent(transcriptId)}/deepgram-stream`;
}

export function pcm16FrameMessage(firstSampleOffset, pcmSamples) {
  if (!Number.isSafeInteger(firstSampleOffset) || firstSampleOffset < 0) {
    throw protocolError('Invalid live audio sample offset.');
  }
  if (!(pcmSamples instanceof Int16Array) || pcmSamples.length <= 0 || pcmSamples.length > MAX_BATCH_SAMPLES) {
    throw protocolError('Invalid live audio batch.');
  }
  const message = new ArrayBuffer(FRAME_HEADER_BYTES + pcmSamples.byteLength);
  const view = new DataView(message);
  view.setUint8(0, FRAME_VERSION);
  view.setBigUint64(1, BigInt(firstSampleOffset), true);
  view.setUint32(9, pcmSamples.length, true);
  for (let index = 0; index < pcmSamples.length; index += 1) {
    view.setInt16(FRAME_HEADER_BYTES + index * 2, pcmSamples[index], true);
  }
  return message;
}

const toPcm16 = (frame) => {
  if (!(frame instanceof Float32Array) || frame.length === 0) return null;
  const pcm = new Int16Array(frame.length);
  for (let index = 0; index < frame.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, frame[index] || 0));
    pcm[index] = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
  }
  return pcm;
};

const copyRecordSlice = (record, startOffset, endOffset) => {
  const start = Math.max(0, startOffset - record.offset);
  const end = Math.min(record.samples.length, endOffset - record.offset);
  if (end <= start) return null;
  return { offset: record.offset + start, samples: record.samples.slice(start, end) };
};

export class DeepgramLiveStream {
  constructor({ onInterim = () => {}, onWindowCommitted = () => {}, onState = () => {}, onError = () => {} } = {}) {
    this.onInterim = onInterim;
    this.onWindowCommitted = onWindowCommitted;
    this.onState = onState;
    this.onError = onError;
    this.socket = null;
    this.transcriptId = null;
    this.sourceOffset = 0;
    this.lastQueuedOffset = 0;
    this.lastSentOffset = 0;
    this.lastVoicedAt = null;
    this.audioPaused = true;
    this.started = false;
    this.stopped = false;
    this.ring = [];
    this.ringSamples = 0;
    this.outbound = [];
    this.outboundSamples = 0;
    this.outboundOffset = null;
    this.pendingCommit = [];
    this.pendingCommitSamples = 0;
    this.inFlight = null;
    this.pauseRequested = false;
    this.resumeRequested = false;
    this.stopRequested = false;
    this.forceFlush = false;
    this.awaitingWindowCommit = false;
    this.streamId = null;
    this.readyPromise = null;
    this.resolveReady = null;
    this.rejectReady = null;
    this.finalizePromise = null;
    this.resolveFinalize = null;
    this.rejectFinalize = null;
  }

  connect({ transcriptId, csrfToken }) {
    if (!transcriptId || !csrfToken || this.socket) throw protocolError('Could not start live transcription.');
    this.transcriptId = transcriptId;
    this.readyPromise = new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
    });
    this.readyTimeoutId = window.setTimeout(() => this._fail(protocolError('Live transcription did not become ready.')), 20000);
    try {
      this.socket = new WebSocket(deepgramStreamWebSocketUrl(transcriptId));
      this.socket.binaryType = 'arraybuffer';
      this.socket.addEventListener('open', () => {
        if (this.stopped) return;
        this.socket.send(JSON.stringify({ type: 'start', csrf_token: csrfToken }));
      });
      this.socket.addEventListener('message', (event) => this._handleMessage(event));
      this.socket.addEventListener('error', () => this._fail(protocolError('Live transcription connection failed.')));
      this.socket.addEventListener('close', () => {
        if (!this.stopped) this._fail(protocolError('Live transcription connection closed.'));
      });
    } catch (_) {
      this._fail(protocolError('Live transcription connection failed.'));
    }
    return this.readyPromise;
  }

  _handleMessage(event) {
    if (this.stopped) return;
    if (typeof event.data !== 'string') {
      this._fail(protocolError('Live transcription returned an unsupported message.'));
      return;
    }
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (_) {
      this._fail(protocolError('Live transcription returned an invalid message.'));
      return;
    }
    if (!message || typeof message !== 'object' || typeof message.type !== 'string') {
      this._fail(protocolError('Live transcription returned an invalid message.'));
      return;
    }
    if (message.type === 'ready') {
      if (
        this.started
        || typeof message.stream_id !== 'string'
        || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(message.stream_id)
        || message.sample_rate !== 16000
        || message.encoding !== 'pcm_s16le'
      ) {
        this._fail(protocolError('Live transcription is not ready.'));
        return;
      }
      this.streamId = message.stream_id;
      this.started = true;
      window.clearTimeout(this.readyTimeoutId);
      this.onState('listening');
      this.resolveReady?.(message);
      this.resolveReady = null;
      this.rejectReady = null;
      return;
    }
    if (message.type === 'received') {
      if (!this.inFlight || message.next_sample_offset !== this.inFlight.offset + this.inFlight.samples.length) {
        this._fail(protocolError('Live transcription returned an invalid audio acknowledgement.'));
        return;
      }
      this.lastSentOffset = message.next_sample_offset;
      this.inFlight = null;
      this._advanceTransport();
      return;
    }
    if (message.type === 'interim') {
      this.onInterim(typeof message.text === 'string' ? message.text : '');
      return;
    }
    if (message.type === 'window_committed') {
      if (!Number.isSafeInteger(message.next_sample_offset) || message.next_sample_offset < 0) {
        this._fail(protocolError('Live transcription returned an invalid checkpoint.'));
        return;
      }
      this._dropCommitted(message.next_sample_offset);
      this.awaitingWindowCommit = false;
      this._advanceTransport();
      this.onWindowCommitted(message);
      return;
    }
    if (message.type === 'error') {
      const error = protocolError('Live transcription stopped.');
      const allowedCodes = new Set([
        'quota_exceeded', 'unauthorized', 'forbidden', 'stream_csrf_invalid', 'not_found',
        'stt_config_secret_missing', 'stream_provider_ineligible', 'stream_transcript_busy',
        'stream_frame_invalid', 'stream_message_invalid', 'stream_session_expired',
        'stream_result_invalid', 'stream_finalization_failed', 'stream_time_limit', 'stream_failed',
      ]);
      error.code = allowedCodes.has(message.code) ? message.code : 'stream_failed';
      error.unresolved = message.unresolved === true;
      error.recoveryAvailable = message.recovery_available === true;
      error.sourceStartSampleOffset = Number.isSafeInteger(message.source_start_sample_offset) ? message.source_start_sample_offset : null;
      error.sourceEndSampleOffset = Number.isSafeInteger(message.source_end_sample_offset) ? message.source_end_sample_offset : null;
      error.forwardedSampleCount = Number.isSafeInteger(message.forwarded_sample_count) ? message.forwarded_sample_count : null;
      this._fail(error);
      return;
    }
    if (message.type === 'finalized') {
      this.stopped = true;
      window.clearTimeout(this.finalizeTimeoutId);
      this.onState('stopped');
      this.resolveFinalize?.(message);
      this.resolveFinalize = null;
      this.rejectFinalize = null;
      this._closeSocket();
      return;
    }
    this._fail(protocolError('Live transcription returned an unsupported message.'));
  }

  _sendJson(message) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw protocolError('Live transcription connection is unavailable.');
    }
    this.socket.send(JSON.stringify(message));
  }

  pushFrame(frame, voiced, now = Date.now()) {
    if (!this.started || this.stopped) return;
    const samples = toPcm16(frame);
    if (!samples) return;
    const record = { offset: this.sourceOffset, samples };
    this.sourceOffset += samples.length;
    this._appendRing(record);
    if (voiced) this.lastVoicedAt = now;

    if (this.audioPaused && this.lastVoicedAt !== null && now - this.lastVoicedAt >= 30000) {
      return;
    }
    if (!this.audioPaused && this.lastVoicedAt !== null && now - this.lastVoicedAt >= 30000) {
      this._pauseAudio();
      return;
    }
    if (!this.audioPaused) {
      try {
        this._queueAudio(record);
      } catch (error) {
        this._fail(error);
      }
    }
    else if (this.ringSamples >= RING_LIMIT_SAMPLES && (this.pauseRequested || this.resumeRequested || this.inFlight)) {
      this._fail(protocolError('Live transcription is receiving audio too slowly.'));
    }
  }

  speechStarted() {
    if (!this.started || this.stopped || !this.audioPaused) return;
    try {
      this.resumeRequested = true;
      this._advanceTransport();
    } catch (error) {
      this._fail(error);
    }
  }

  async finish() {
    if (this.stopped) return null;
    if (!this.started) throw protocolError('Live transcription did not become ready.');
    this.stopRequested = true;
    if (this.audioPaused) this._discardUnsentOutbound();
    this.finalizePromise = new Promise((resolve, reject) => {
      this.resolveFinalize = resolve;
      this.rejectFinalize = reject;
    });
    this.finalizeTimeoutId = window.setTimeout(() => this._fail(protocolError('Live transcription did not finish in time.')), 30000);
    this._advanceTransport();
    return this.finalizePromise;
  }

  interrupt() {
    this._fail(protocolError('Live transcription was interrupted.'));
  }

  cancelPending() {
    if (this.stopped) return;
    this.stopped = true;
    if (!this.started) this.rejectReady?.(protocolError('Live transcription connection was canceled.'));
    window.clearTimeout(this.readyTimeoutId);
    this.resolveReady = null;
    this.rejectReady = null;
    this.pendingCommit = [];
    this.pendingCommitSamples = 0;
    this.outbound = [];
    this.outboundSamples = 0;
    this.ring = [];
    this.ringSamples = 0;
    this._closeSocket();
  }

  pendingRecoverySamples({ start, end, count } = {}) {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || !Number.isSafeInteger(count)
      || start < 0 || end <= start || count <= 0 || count > RECOVERY_LIMIT_SAMPLES) return null;
    const records = this.pendingCommit
      .map((record) => copyRecordSlice(record, start, end))
      .filter(Boolean)
      .sort((left, right) => left.offset - right.offset);
    if (!records.length || records[0].offset !== start
      || records[records.length - 1].offset + records[records.length - 1].samples.length !== end
      || records.reduce((total, record) => total + record.samples.length, 0) !== count) return null;
    for (let index = 1; index < records.length; index += 1) {
      const previousEnd = records[index - 1].offset + records[index - 1].samples.length;
      if (records[index].offset < previousEnd) return null;
    }
    return concatInt16(records.map((record) => record.samples));
  }

  _appendRing(record) {
    this.ring.push(record);
    this.ringSamples += record.samples.length;
    while (this.ringSamples > RING_LIMIT_SAMPLES && this.ring.length) {
      const first = this.ring[0];
      const excess = this.ringSamples - RING_LIMIT_SAMPLES;
      if (excess >= first.samples.length) {
        this.ring.shift();
        this.ringSamples -= first.samples.length;
      } else {
        this.ring[0] = { offset: first.offset + excess, samples: first.samples.slice(excess) };
        this.ringSamples -= excess;
      }
    }
  }

  _queueAudio(record) {
    if (this.outboundSamples && record.offset !== this.outboundOffset + this.outboundSamples) {
      this._flushOutbound();
    }
    if (this.outboundOffset === null) this.outboundOffset = record.offset;
    this.outbound.push(record.samples);
    this.outboundSamples += record.samples.length;
    this.lastQueuedOffset = record.offset + record.samples.length;
    if (this.outboundSamples > OUTBOUND_LIMIT_SAMPLES) {
      throw protocolError('Live transcription is receiving audio too slowly.');
    }
    this._flushOutbound();
  }

  _flushOutbound() {
    this._advanceTransport();
  }

  _appendPending(record) {
    this.pendingCommit.push(record);
    this.pendingCommitSamples += record.samples.length;
    if (this.pendingCommitSamples > RECOVERY_LIMIT_SAMPLES) {
      throw protocolError('Live transcription could not confirm a committed audio window.');
    }
  }

  _dropCommitted(nextOffset) {
    const kept = [];
    this.pendingCommitSamples = 0;
    for (const record of this.pendingCommit) {
      const fresh = copyRecordSlice(record, Math.max(record.offset, nextOffset), record.offset + record.samples.length);
      if (fresh) {
        kept.push(fresh);
        this.pendingCommitSamples += fresh.samples.length;
      }
    }
    this.pendingCommit = kept;
  }

  _pauseAudio() {
    if (this.audioPaused || this.stopped) return;
    try {
      this.pauseRequested = true;
      this._advanceTransport();
    } catch (error) {
      this._fail(error);
    }
  }

  _advanceTransport() {
    if (!this.started || this.stopped || !this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    try {
    if (this.stopRequested) {
      if (this.audioPaused) this._discardUnsentOutbound();
      if (!this.inFlight && !this.awaitingWindowCommit && this.outboundSamples > 0) this.forceFlush = true;
      if (!this.inFlight && this.outboundSamples === 0 && !this.awaitingWindowCommit) {
        this._sendJson({ type: 'stop' });
        this.stopRequested = false;
        return;
      }
      if (this.inFlight || this.awaitingWindowCommit) return;
      }
      if (this.awaitingWindowCommit || this.inFlight) return;
      if (this.pauseRequested && this.outboundSamples === 0) {
        this._sendJson({ type: 'paused' });
        this.pauseRequested = false;
        this.audioPaused = true;
        this.onState('listening');
      }
      if (this.resumeRequested && !this.inFlight && this.outboundSamples === 0) {
        this._sendJson({ type: 'resumed' });
        this.resumeRequested = false;
        this.audioPaused = false;
        this.onState('streaming');
        for (const record of this.ring) {
          const fresh = copyRecordSlice(record, Math.max(record.offset, this.lastQueuedOffset), record.offset + record.samples.length);
          if (fresh) this._queueAudio(fresh);
        }
      }
      if (this.audioPaused || this.inFlight || this.awaitingWindowCommit) return;
      if (this.outboundSamples < BATCH_TARGET_SAMPLES && !this.pauseRequested && !this.stopRequested && !this.forceFlush) return;
      if (this.outboundSamples === 0) return;
      if (this.socket.bufferedAmount > SOCKET_BUFFER_LIMIT_BYTES) {
        throw protocolError('Live transcription is receiving audio too slowly.');
      }
      const remainingWindow = RECOVERY_LIMIT_SAMPLES - this.pendingCommitSamples;
      if (remainingWindow <= 0) {
        this.awaitingWindowCommit = true;
        return;
      }
      const count = Math.min(MAX_BATCH_SAMPLES, this.outboundSamples, remainingWindow);
      const { samples, remainder } = takeSamples(this.outbound, count);
      const firstOffset = this.outboundOffset;
      this.outbound = remainder.chunks;
      this.outboundSamples -= count;
      this.outboundOffset = remainder.samples ? firstOffset + count : null;
      const record = { offset: firstOffset, samples };
      this._appendPending(record);
      this.inFlight = record;
      this.socket.send(pcm16FrameMessage(firstOffset, samples));
      this.forceFlush = false;
      if (this.pendingCommitSamples >= RECOVERY_LIMIT_SAMPLES) this.awaitingWindowCommit = true;
    } catch (error) {
      this._fail(error);
    }
  }

  _discardUnsentOutbound() {
    this.outbound = [];
    this.outboundSamples = 0;
    this.outboundOffset = null;
    this.lastQueuedOffset = this.lastSentOffset;
  }

  _fail(error) {
    if (this.stopped) return;
    this.stopped = true;
    this.rejectReady?.(error);
    this.rejectFinalize?.(error);
    window.clearTimeout(this.readyTimeoutId);
    window.clearTimeout(this.finalizeTimeoutId);
    this.resolveReady = null;
    this.rejectReady = null;
    this.resolveFinalize = null;
    this.rejectFinalize = null;
    this.onInterim('');
    this.onState('failed');
    const range = {
      start: error?.sourceStartSampleOffset,
      end: error?.sourceEndSampleOffset,
      count: error?.forwardedSampleCount,
    };
    this.onError(error, {
      streamId: this.streamId,
      unresolved: error?.unresolved === true,
      recoveryAvailable: error?.recoveryAvailable === true,
      samples: this.pendingRecoverySamples(range),
    });
    this._closeSocket();
  }

  _closeSocket() {
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
  }
}

function takeSamples(chunks, sampleCount) {
  let needed = sampleCount;
  const taken = [];
  const rest = [];
  for (const chunk of chunks) {
    if (needed <= 0) {
      rest.push(chunk);
      continue;
    }
    if (chunk.length <= needed) {
      taken.push(chunk);
      needed -= chunk.length;
      continue;
    }
    taken.push(chunk.slice(0, needed));
    rest.push(chunk.slice(needed));
    needed = 0;
  }
  return { samples: concatInt16(taken), remainder: { chunks: rest, samples: rest.length > 0 } };
}

function concatInt16(chunks) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const joined = new Int16Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.length;
  }
  return joined;
}
