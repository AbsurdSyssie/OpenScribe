// Separate clock-aligned PCM taps for local microphone and remote call audio.
// AudioWorklet currentFrame is common to every tap in the SAME AudioContext.
// This produces samples only; silence/VAD decisions belong to the consumer.
const FRAMES_PER_MESSAGE = 4096;

class TelehealthSourceProcessor extends AudioWorkletProcessor {
  constructor(options = {}) {
    super();
    const channel = options.processorOptions?.channel;
    if (channel !== 'clinician' && channel !== 'patient') {
      throw new Error('A known telehealth source is required.');
    }
    this.channel = channel;
    this.pending = new Float32Array(FRAMES_PER_MESSAGE);
    this.length = 0;
    this.startFrame = 0;
    this.expectedFrame = null;
    this.closed = false;
    this.port.onmessage = (event) => {
      if (event.data?.type !== 'flush') return;
      this.emit();
      this.closed = true;
      this.port.postMessage({ type: 'flushed', channel: this.channel, requestId: event.data.requestId });
    };
  }

  emit() {
    if (!this.length) return;
    const samples = this.pending.slice(0, this.length);
    this.port.postMessage({
      type: 'frames',
      channel: this.channel,
      startFrame: this.startFrame,
      samples,
    }, [samples.buffer]);
    this.length = 0;
  }

  process(inputs) {
    if (this.closed) return true;
    const channels = inputs[0];
    const frameCount = channels?.[0]?.length || 0;
    if (!frameCount) {
      // No input is NOT evidence of silence. Preserve any discontinuity in
      // original sample-frame coordinates and let the owner detect track loss.
      this.emit();
      this.expectedFrame = null;
      return true;
    }
    if (this.expectedFrame !== null && currentFrame !== this.expectedFrame) {
      this.emit(); // Never concatenate samples across a missing input interval.
    }
    for (let index = 0; index < frameCount; index += 1) {
      if (this.length === 0) this.startFrame = currentFrame + index;
      let mixed = 0;
      for (const samples of channels) mixed += samples[index] || 0;
      // Downmix within ONE source only (e.g. stereo tab), not between sources.
      this.pending[this.length++] = Math.max(-1, Math.min(1, mixed / channels.length));
      if (this.length === FRAMES_PER_MESSAGE) this.emit();
    }
    this.expectedFrame = currentFrame + frameCount;
    return true;
  }
}

registerProcessor('openscribe-telehealth-source', TelehealthSourceProcessor);
