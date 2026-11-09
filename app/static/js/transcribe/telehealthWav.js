// Recorded telehealth v1 keeps ALL source samples, including silence.
// VAD may guide visual feedback but MUST NOT alter these uploaded timelines.
// A later VAD-trimming implementation must emit a truthful piecewise span map.
export function encodeTelehealthWavWindow({
  samples,
  sampleRate,
  startFrame,
  endFrame,
  outputSampleRate = 16000,
} = {}) {
  if (
    !(samples instanceof Float32Array)
    || samples.length === 0
    || !Number.isSafeInteger(sampleRate)
    || sampleRate < 8000 || sampleRate > 192000
    || !Number.isSafeInteger(outputSampleRate)
    || outputSampleRate < 8000 || outputSampleRate > 192000
    || !Number.isSafeInteger(startFrame) || startFrame < 0
    || !Number.isSafeInteger(endFrame) || endFrame - startFrame !== samples.length
    || samples.length > sampleRate * 30
  ) {
    throw new RangeError('Invalid telehealth audio encoding window.');
  }
  // Linear interpolation is stable enough for speech ASR and avoids the huge
  // storage cost of raw 48 kHz sources. Original frame offsets are retained.
  // The ASR pipeline normalizes the result again server-side.
  const count = Math.max(1, Math.round(samples.length * outputSampleRate / sampleRate));
  if (count > outputSampleRate * 30) {
    throw new RangeError('Audio window is too long.');
  }
  const buffer = new ArrayBuffer(44 + count * 2);
  const view = new DataView(buffer);
  view.setUint32(0, 0x46464952, true); // "RIFF"
  view.setUint32(4, 36 + count * 2, true);
  view.setUint32(8, 0x45564157, true); // "WAVE"
  view.setUint32(12, 0x20746d66, true); // "fmt "
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // Uncompressed PCM
  view.setUint16(22, 1, true); // Mono within one source, not mixed sources
  view.setUint32(24, outputSampleRate, true);
  view.setUint32(28, outputSampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  view.setUint32(36, 0x61746164, true); // "data"
  view.setUint32(40, count * 2, true);
  const scale = sampleRate / outputSampleRate;
  for (let index = 0; index < count; index += 1) {
    const position = index * scale;
    const left = Math.min(Math.floor(position), samples.length - 1);
    const right = Math.min(left + 1, samples.length - 1);
    const ratio = Math.min(1, Math.max(0, position - left));
    const sourceSample = samples[left] * (1 - ratio) + samples[right] * ratio;
    const clipped = Math.max(-1, Math.min(1, Number.isFinite(sourceSample) ? sourceSample : 0));
    view.setInt16(44 + index * 2, clipped < 0
      ? Math.round(clipped * 32768)
      : Math.round(clipped * 32767), true);
  }
  return {
    bytes: new Uint8Array(buffer),
    fileSampleRate: outputSampleRate,
    fileSamples: count,
    originalStartFrame: startFrame,
    originalEndFrame: endFrame,
    spans: [{
      file_start_sample: 0,
      file_end_sample: count,
      timeline_start_frame: startFrame,
      timeline_end_frame: endFrame,
    }],
  };
}
