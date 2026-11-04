// Bounded two-channel handoff. Windows contain contiguous original-clock PCM;
// a later VAD stage may remove silence only if it preserves the source spans.
// onWindow MUST resolve only after its audio has been durably accepted or handed
// to another explicitly bounded, recoverable owner-controlled stage.
const CHANNELS = ['clinician', 'patient'];
const isFrame = (n) => Number.isSafeInteger(n) && n >= 0;

export function createTelehealthWindows({
  sampleRate,
  windowSeconds = 10,
  maxPendingWindows = 4,
  onWindow,
  onGap = () => {},
} = {}) {
  if (!isFrame(sampleRate) || sampleRate === 0
    || !Number.isInteger(windowSeconds) || windowSeconds < 1 || windowSeconds > 30
    || !Number.isInteger(maxPendingWindows) || maxPendingWindows < 1 || maxPendingWindows > 16
    || typeof onWindow !== 'function') {
    throw new RangeError('Invalid bounded telehealth window configuration.');
  }
  const maxFrames = sampleRate * windowSeconds;
  if (!isFrame(maxFrames)) throw new RangeError('Invalid audio window capacity.');

  const sources = {
    clinician: { start: null, next: null, chunks: [], count: 0 },
    patient: { start: null, next: null, chunks: [], count: 0 },
  };
  const queue = [];
  let processing = null;
  let finished = false;
  let failure = null;

  const drain = () => {
    if (processing || failure || !queue.length) return processing;
    processing = (async () => {
      while (queue.length && !failure) {
        try {
          // Do not dequeue before success: retry must retain all unaccepted
          // audio even after a provider or upload failure.
          await onWindow(queue[0]);
          queue.shift();
        } catch (error) {
          failure = error instanceof Error ? error : new Error('Audio window was not accepted.');
        }
      }
    })().finally(() => {
      processing = null;
      if (!failure && queue.length) drain();
    });
    return processing;
  };

  const enqueue = (window) => {
    if (queue.length >= maxPendingWindows) {
      failure = new Error('Telehealth audio handoff is backpressured.');
      return false;
    }
    queue.push(window);
    void drain();
    return true;
  };

  const flush = (channel) => {
    const source = sources[channel];
    if (!source.count) return true;
    const samples = new Float32Array(source.count);
    let offset = 0;
    for (const chunk of source.chunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }
    const entry = {
      channel,
      startFrame: source.start,
      endFrame: source.start + source.count,
      sampleRate,
      samples,
    };
    if (!enqueue(entry)) return false;
    source.chunks = [];
    source.count = 0;
    source.start = null;
    return true;
  };

  const ingest = ({ channel, startFrame, endFrame, samples }) => {
    if (finished || failure || !CHANNELS.includes(channel) || !isFrame(startFrame)
      || !isFrame(endFrame) || !(samples instanceof Float32Array)
      || samples.length === 0 || endFrame - startFrame !== samples.length) return false;

    const source = sources[channel];
    if (source.next !== null && startFrame < source.next) return false; // Duplicate/rewound audio.
    if ((source.next === null && startFrame > 0)
      || (source.next !== null && startFrame > source.next)) {
      if (!flush(channel)) return false;
      const gapStart = source.next === null ? 0 : source.next;
      try { onGap({ channel, startFrame: gapStart, endFrame: startFrame }); } catch (_) { return false; }
    }

    let offset = 0;
    while (offset < samples.length) {
      if (source.start === null) source.start = startFrame + offset;
      const amount = Math.min(maxFrames - source.count, samples.length - offset);
      // Copy: worklet messages and consumers can transfer/modify their buffers.
      source.chunks.push(samples.slice(offset, offset + amount));
      source.count += amount;
      offset += amount;
      source.next = startFrame + offset;
      if (source.count === maxFrames && !flush(channel)) return false;
    }
    return true;
  };

  const finish = async () => {
    finished = true;
    if (!failure) {
      for (const channel of CHANNELS) {
        if (!flush(channel)) break;
      }
    }
    if (processing) await processing;
    if (failure) throw failure;
    if (queue.length) {
      await drain();
      if (failure) throw failure;
    }
    return true;
  };

  const retryPending = async () => {
    // Caller must have stopped capture. A failed handoff remains available
    // for explicit retry, never silently discarded.
    if (!finished) throw new Error('Stop capture before retrying pending audio.');
    failure = null;
    if (queue.length) await drain();
    if (failure) throw failure;
    for (const channel of CHANNELS) {
      if (!flush(channel)) throw failure;
    }
    if (processing) await processing;
    if (failure) throw failure;
    return queue.length === 0;
  };

  const status = () => ({
    finished, failed: Boolean(failure),
    pendingWindows: queue.length,
    bufferedFrames: {
      clinician: sources.clinician.count,
      patient: sources.patient.count,
    },
  });

  return { ingest, finish, retryPending, status };
}
