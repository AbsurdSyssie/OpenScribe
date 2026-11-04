"""Synthetic Node tests for browser telehealth timing and source isolation.

No microphone, clinical content, network, STT provider or database is required.
"""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_AVAILABLE = shutil.which("node") is not None


def _prepare_modules(tmp_path: Path) -> None:
    js = ROOT / "app/static/js/transcribe"
    (tmp_path / "telehealthTimeline.mjs").write_text(
        (js / "telehealthTimeline.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    capture = (js / "telehealthCapture.js").read_text(encoding="utf-8")
    capture = capture.replace(
        "./telehealthTimeline.js?v=20261104-telehealth-timing",
        "./telehealthTimeline.mjs",
    )
    (tmp_path / "telehealthCapture.mjs").write_text(capture, encoding="utf-8")
    (tmp_path / "telehealthSourceWorklet.js").write_text(
        (js / "telehealthSourceWorklet.js").read_text(encoding="utf-8"), encoding="utf-8"
    )


def _run_node(tmp_path: Path, script: str) -> None:
    (tmp_path / "test.mjs").write_text(script, encoding="utf-8")
    subprocess.run(
        ["node", str(tmp_path / "test.mjs")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js required")
def test_telehealth_shared_clock_speech_check_and_asr_timing(tmp_path):
    _prepare_modules(tmp_path)
    _run_node(tmp_path, r"""
import assert from 'node:assert/strict';
import {
  createTelehealthTimeline, createTelehealthSpeechCheck,
  projectTelehealthProviderTurn,
} from './telehealthTimeline.mjs';

const timeline = createTelehealthTimeline({ sampleRate: 48000, originFrame: 24000 });
const mic1 = timeline.ingest({ channel: 'clinician', startFrame: 24000, samples: new Float32Array(4096) });
assert.deepEqual(
  [mic1.startFrame, mic1.endFrame, mic1.skippedFrames],
  [0, 4096, 0]
);
const remote1 = timeline.ingest({ channel: 'patient', startFrame: 48000, samples: new Float32Array(4096) });
assert.deepEqual([remote1.startFrame, remote1.skippedFrames], [24000, 24000]);
const mic2 = timeline.ingest({ channel: 'clinician', startFrame: 28100, samples: new Float32Array(200) });
assert.equal(mic2.skippedFrames, 4);
assert.throws(() => timeline.ingest({ channel: 'clinician', startFrame: 28120, samples: new Float32Array(100) }), /overlap|rewind/);
assert.throws(() => timeline.ingest({ channel: 'unknown', startFrame: 30000, samples: new Float32Array(10) }), /Invalid telehealth/);
assert.equal(timeline.snapshot().receivedFrames.clinician, 4296);
timeline.close();
assert.throws(() => timeline.ingest({ channel: 'patient', startFrame: 52096, samples: new Float32Array(1) }), /closed/);

const speech = createTelehealthSpeechCheck({ sampleRate: 48000, requiredSpeechMs: 5000 });
assert.equal(speech.observeSpeech({ channel: 'clinician', startFrame: 0, endFrame: 192000 }).clinician, false);
assert.equal(speech.observeSpeech({ channel: 'patient', startFrame: 0, endFrame: 240000 }).both, false);
const verified = speech.observeSpeech({ channel: 'clinician', startFrame: 100000, endFrame: 240000 });
assert.equal(verified.both, true);
assert.equal(verified.clinicianSpeechMs, 5000); // Overlap not double-counted.
assert.equal(verified.patientSpeechMs, 5000);

const spans = [
  { fileStartSample: 0, fileEndSample: 16000, timelineStartFrame: 96000, timelineEndFrame: 144000 },
  { fileStartSample: 16000, fileEndSample: 32000, timelineStartFrame: 480000, timelineEndFrame: 528000 },
];
const first = projectTelehealthProviderTurn({ text: 'synthetic', start_ms: 250, end_ms: 750 }, spans, {
  fileSampleRate: 16000, timelineSampleRate: 48000,
});
assert.equal(first.start_ms, 2250);
assert.equal(first.end_ms, 2750);
const second = projectTelehealthProviderTurn({ text: 'synthetic', start_ms: 1200, end_ms: 1400 }, spans, {
  fileSampleRate: 16000, timelineSampleRate: 48000,
});
assert.deepEqual([second.start_ms, second.end_ms], [10200, 10400]);
assert.equal(projectTelehealthProviderTurn({ start_ms: 900, end_ms: 1100 }, spans, {
  fileSampleRate: 16000, timelineSampleRate: 48000,
}), null); // One ASR turn crosses VAD-compressed silence: never fabricate precision.
assert.equal(projectTelehealthProviderTurn({ start_ms: 3100, end_ms: 3200 }, spans, {
  fileSampleRate: 16000, timelineSampleRate: 48000,
}), null);
const invalidOverlappingSpans = [
  { fileStartSample: 0, fileEndSample: 16000, timelineStartFrame: 0, timelineEndFrame: 48000 },
  { fileStartSample: 15000, fileEndSample: 30000, timelineStartFrame: 48000, timelineEndFrame: 93000 },
];
assert.equal(projectTelehealthProviderTurn({ start_ms: 100, end_ms: 200 }, invalidOverlappingSpans, {
  fileSampleRate: 16000, timelineSampleRate: 48000,
}), null);
const invalidClockScale = [
  { fileStartSample: 0, fileEndSample: 16000, timelineStartFrame: 0, timelineEndFrame: 120000 },
];
assert.equal(projectTelehealthProviderTurn({ start_ms: 100, end_ms: 200 }, invalidClockScale, {
  fileSampleRate: 16000, timelineSampleRate: 48000,
}), null);
""")


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js required")
def test_telehealth_worklet_offsets_backpressure_and_capture_lifecycle(tmp_path):
    _prepare_modules(tmp_path)
    _run_node(tmp_path, r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createTelehealthCapture } from './telehealthCapture.mjs';

class FakeProcessor {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
}
let Registered;
const sandbox = {
  currentFrame: 0,
  AudioWorkletProcessor: FakeProcessor,
  registerProcessor: (name, ctor) => {
    assert.equal(name, 'openscribe-telehealth-source');
    Registered = ctor;
  },
  Float32Array, Math,
};
vm.runInNewContext(fs.readFileSync('./telehealthSourceWorklet.js', 'utf8'), sandbox);
const proc = new Registered({ processorOptions: { channel: 'patient' } });
const emitted = [];
proc.port.postMessage = (message) => emitted.push(message);
for (let index = 0; index < 32; index += 1) {
  sandbox.currentFrame = index * 128;
  proc.process([[new Float32Array(128).fill(0.25), new Float32Array(128).fill(0.75)]]);
}
const full = emitted.find((m) => m.type === 'frames');
assert.equal(full.channel, 'patient');
assert.equal(full.startFrame, 0);
assert.equal(full.samples.length, 4096);
assert.equal(full.samples[0], 0.5); // Stereo tab downmix, NOT clinician+patient mixing.
proc.port.onmessage({ data: { type: 'ack' } });
sandbox.currentFrame = 4608;
proc.process([[new Float32Array(128).fill(0.1)]]);
proc.port.onmessage({ data: { type: 'flush', requestId: 'stop' } });
assert.equal(emitted.find((m) => m.type === 'frames' && m.startFrame === 4608).samples.length, 128);
assert.equal(emitted.at(-1).type, 'flushed');

const overflow = new Registered({ processorOptions: { channel: 'clinician' } });
const reports = [];
overflow.port.postMessage = (message) => reports.push(message);
for (let index = 0; index < 25 * 32; index += 1) {
  sandbox.currentFrame = index * 128;
  overflow.process([[new Float32Array(128)]]);
}
assert.equal(reports.filter((m) => m.type === 'overflow').length, 1);
assert.equal(reports.filter((m) => m.type === 'frames').length, 24);

class FakeTrack {
  constructor(kind, displaySurface) {
    this.kind = kind;
    this.displaySurface = displaySurface;
    this.readyState = 'live';
    this.muted = false;
    this.listeners = {};
    this.stopped = false;
  }
  getSettings() { return this.displaySurface ? { displaySurface: this.displaySurface } : {}; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((x) => x !== fn); }
  emit(type) { for (const fn of this.listeners[type] || []) fn(); }
  stop() { this.stopped = true; this.readyState = 'ended'; }
}
const video = new FakeTrack('video', 'browser');
const patient = new FakeTrack('audio');
const mic = new FakeTrack('audio');
const display = {
  getTracks: () => [video, patient], getVideoTracks: () => [video], getAudioTracks: () => [patient],
};
const microphone = {
  getTracks: () => [mic], getVideoTracks: () => [], getAudioTracks: () => [mic],
};
class FakeNode {
  connect(dest) { this.connectedTo = dest; return dest; }
  disconnect() { this.disconnected = true; }
}
class FakeContext {
  static last;
  constructor() {
    FakeContext.last = this;
    this.sampleRate = 48000;
    this.currentTime = 0;
    this.state = 'running';
    this.destination = {};
    this.audioWorklet = { addModule: async (url) => { assert.match(url, /telehealthSourceWorklet/); } };
  }
  createMediaStreamSource() { return new FakeNode(); }
  createGain() { const node = new FakeNode(); node.gain = { value: 1 }; return node; }
  async suspend() { this.state = 'suspended'; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
}
class FakePort {
  constructor() { this.onmessage = null; this.sent = []; }
  postMessage(data) {
    this.sent.push(data);
    if (data.type === 'flush') {
      queueMicrotask(() => this.onmessage?.({
        data: { type: 'flushed', requestId: data.requestId },
      }));
    }
  }
  emit(data) { this.onmessage?.({ data }); }
}
class FakeTap extends FakeNode {
  static all = [];
  constructor(context, name, options) {
    super();
    assert.equal(name, 'openscribe-telehealth-source');
    this.port = new FakePort();
    this.channel = options.processorOptions.channel;
    FakeTap.all.push(this);
  }
}
const calls = [];
const devices = {
  async getDisplayMedia(options) { calls.push('display'); assert.equal(options.video, true); return display; },
  async getUserMedia() { calls.push('mic'); return microphone; },
};
globalThis.window = { AudioContext: FakeContext, AudioWorkletNode: FakeTap, setTimeout, clearTimeout };
const frames = [], interruptions = [], statuses = [];
const capture = createTelehealthCapture({
  mediaDevices: devices,
  AudioContextCtor: FakeContext,
  AudioWorkletNodeCtor: FakeTap,
  onFrames: (event) => { frames.push(event); },
  onInterrupted: (event) => interruptions.push(event),
  onSourceState: (event) => statuses.push(event),
});
assert.deepEqual(await capture.start(), { sampleRate: 48000 });
assert.deepEqual(calls, ['display', 'mic']);
assert.equal(capture.state, 'running');
assert.equal(statuses.length, 2);
assert.equal(FakeTap.all.length, 2);
const patientTap = FakeTap.all.find((tap) => tap.channel === 'patient');
const clinicianTap = FakeTap.all.find((tap) => tap.channel === 'clinician');
patientTap.port.emit({ type: 'frames', channel: 'patient', startFrame: 48000, samples: new Float32Array(128) });
clinicianTap.port.emit({ type: 'frames', channel: 'clinician', startFrame: 0, samples: new Float32Array(128) });
assert.deepEqual(frames.map((f) => [f.channel, f.startFrame, f.skippedFrames]), [
  ['patient', 48000, 48000], ['clinician', 0, 0],
]);
assert.equal(patientTap.port.sent.some((m) => m.type === 'ack'), true);
patient.emit('ended');
await new Promise((resolve) => setTimeout(resolve, 10));
assert.deepEqual(interruptions, [{ channel: 'patient', reason: 'ended' }]);
assert.equal(capture.state, 'stopped');
assert.equal(video.stopped, true);
assert.equal(mic.stopped, true);
assert.equal(FakeContext.last.state, 'closed');

// Cancellation while the share picker is open stops any eventual late stream.
const lateVideo = new FakeTrack('video', 'browser');
const lateAudio = new FakeTrack('audio');
const lateStream = {
  getTracks: () => [lateVideo, lateAudio],
  getVideoTracks: () => [lateVideo],
  getAudioTracks: () => [lateAudio],
};
let releasePermission;
const delayed = createTelehealthCapture({
  mediaDevices: {
    getDisplayMedia: () => new Promise((resolve) => { releasePermission = resolve; }),
    getUserMedia: async () => { throw new Error('Must not acquire mic after cancellation'); },
  },
  AudioContextCtor: FakeContext, AudioWorkletNodeCtor: FakeTap,
  onFrames: () => {},
});
const pending = delayed.start();
await delayed.stop();
releasePermission(lateStream);
await assert.rejects(pending, /cancelled/);
assert.equal(lateVideo.stopped, true);
assert.equal(lateAudio.stopped, true);
assert.equal(delayed.state, 'stopped');
""")
