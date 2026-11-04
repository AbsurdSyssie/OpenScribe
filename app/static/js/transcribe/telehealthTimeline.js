// Telehealth capture timing primitives. No audio content is persisted here.
// All offsets refer to one AudioContext rendering clock BEFORE VAD removes silence.
// This module deliberately does not infer speech or clinical speaker identity.
export const TELEHEALTH_CHANNELS = Object.freeze(['clinician', 'patient']);

const isFrame = (value) => Number.isSafeInteger(value) && value >= 0;

export function createTelehealthTimeline({ sampleRate, originFrame = 0 } = {}) {
  if (!isFrame(sampleRate) || sampleRate === 0 || !isFrame(originFrame)) {
    throw new RangeError('A positive audio sample rate and a valid shared origin are required.');
  }

  const nextFrame = { clinician: originFrame, patient: originFrame };
  const received = { clinician: 0, patient: 0 };
  let closed = false;

  const ingest = ({ channel, startFrame, samples }) => {
    if (closed) throw new Error('The telehealth timeline is closed.');
    if (!Object.hasOwn(nextFrame, channel)) throw new RangeError('Invalid telehealth audio channel.');
    if (!isFrame(startFrame) || startFrame < originFrame || !(samples instanceof Float32Array)
      || samples.length === 0 || !isFrame(startFrame + samples.length)) {
      throw new RangeError('Invalid source audio interval.');
    }
    if (startFrame < nextFrame[channel]) {
      throw new RangeError('Source audio intervals must not overlap or rewind.');
    }
    const skippedFrames = startFrame - nextFrame[channel];
    const endFrame = startFrame + samples.length;
    nextFrame[channel] = endFrame;
    received[channel] += samples.length;
    return Object.freeze({
      channel,
      startFrame: startFrame - originFrame,
      endFrame: endFrame - originFrame,
      skippedFrames,
      sampleRate,
    });
  };

  const snapshot = () => ({
    sampleRate,
    originFrame,
    receivedFrames: { ...received },
    endFrames: {
      clinician: nextFrame.clinician - originFrame,
      patient: nextFrame.patient - originFrame,
    },
    closed,
  });

  return { ingest, snapshot, close: () => { closed = true; } };
}

// Count actual VAD-positive ORIGINAL source intervals without counting overlapped
// pre-roll or repeated speech callbacks twice. Never delay starting capture.
export function createTelehealthSpeechCheck({ sampleRate, requiredSpeechMs = 5000 } = {}) {
  if (!isFrame(sampleRate) || sampleRate === 0
    || !Number.isSafeInteger(requiredSpeechMs) || requiredSpeechMs <= 0) {
    throw new RangeError('Invalid speech-check parameters.');
  }
  const requiredFrames = Math.ceil(requiredSpeechMs * sampleRate / 1000);
  const lastEnd = { clinician: 0, patient: 0 };
  const verifiedFrames = { clinician: 0, patient: 0 };

  const observeSpeech = ({ channel, startFrame, endFrame }) => {
    if (!Object.hasOwn(lastEnd, channel) || !isFrame(startFrame)
      || !isFrame(endFrame) || endFrame <= startFrame) {
      throw new RangeError('Invalid speech interval.');
    }
    const additionalFrames = Math.max(0, endFrame - Math.max(startFrame, lastEnd[channel]));
    lastEnd[channel] = Math.max(lastEnd[channel], endFrame);
    verifiedFrames[channel] += additionalFrames;
    return state();
  };
  const state = () => {
    const clinician = verifiedFrames.clinician >= requiredFrames;
    const patient = verifiedFrames.patient >= requiredFrames;
    return {
      clinician,
      patient,
      both: clinician && patient,
      clinicianSpeechMs: Math.floor(verifiedFrames.clinician * 1000 / sampleRate),
      patientSpeechMs: Math.floor(verifiedFrames.patient * 1000 / sampleRate),
    };
  };
  return { observeSpeech, state };
}

// A provider reports offsets relative to the shorter, VAD-filtered uploaded
// audio. A turn is exactly placeable only when it is wholly inside ONE
// contiguous original-time span; never pretend removed silence is continuous.
// Spans: { fileStartSample, fileEndSample, timelineStartFrame, timelineEndFrame }.
// All timeline frames are RELATIVE to the same capture-session origin.
export function projectTelehealthProviderTurn(turn, spans, {
  fileSampleRate,
  timelineSampleRate,
} = {}) {
  if (!turn || !Array.isArray(spans) || !isFrame(fileSampleRate) || fileSampleRate === 0
    || !isFrame(timelineSampleRate) || timelineSampleRate === 0) {
    throw new RangeError('Invalid telehealth time mapping.');
  }
  if (!Number.isFinite(turn.start_ms) || !Number.isFinite(turn.end_ms)
    || turn.start_ms < 0 || turn.end_ms < turn.start_ms) return null;

  const fileStartSample = Math.round(turn.start_ms * fileSampleRate / 1000);
  const fileEndSample = Math.round(turn.end_ms * fileSampleRate / 1000);
  if (!isFrame(fileStartSample) || !isFrame(fileEndSample)) return null;

  // Reject ambiguous or malformed piecewise time maps instead of selecting
  // whichever overlapping piece happened to be listed first. Upload-side
  // server validation must additionally check measured WAV duration/size.
  let previousFileEnd = 0;
  let previousTimelineEnd = 0;
  for (const piece of spans) {
    if (!piece || !isFrame(piece.fileStartSample) || !isFrame(piece.fileEndSample)
      || !isFrame(piece.timelineStartFrame) || !isFrame(piece.timelineEndFrame)
      || piece.fileEndSample <= piece.fileStartSample
      || piece.timelineEndFrame <= piece.timelineStartFrame
      || piece.fileStartSample !== previousFileEnd
      || piece.timelineStartFrame < previousTimelineEnd) return null;
    const sourceDurationMs = (piece.fileEndSample - piece.fileStartSample) * 1000 / fileSampleRate;
    const timelineDurationMs = (piece.timelineEndFrame - piece.timelineStartFrame) * 1000 / timelineSampleRate;
    // Resampling may introduce a small edge rounding difference, but a
    // substantial mismatch would incorrectly attribute dialogue to time.
    if (Math.abs(sourceDurationMs - timelineDurationMs) > 10) return null;
    previousFileEnd = piece.fileEndSample;
    previousTimelineEnd = piece.timelineEndFrame;
  }
  const span = spans.find((piece) => fileStartSample >= piece.fileStartSample
    && fileEndSample <= piece.fileEndSample);
  if (!span) return null;

  const position = (sample) => span.timelineStartFrame
    + (sample - span.fileStartSample) * (span.timelineEndFrame - span.timelineStartFrame)
    / (span.fileEndSample - span.fileStartSample);
  const start_ms = Math.round(position(fileStartSample) * 1000 / timelineSampleRate);
  const end_ms = Math.round(position(fileEndSample) * 1000 / timelineSampleRate);
  if (!isFrame(start_ms) || !isFrame(end_ms) || end_ms < start_ms) return null;
  return { ...turn, start_ms, end_ms };
}
