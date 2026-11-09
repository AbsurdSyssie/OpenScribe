"""Content-free Node regression tests for window capture, VAD and diagnostics."""
import shutil
import subprocess
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/js/transcribe"
NODE = shutil.which("node")


def run_js(tmp_path, script):
    (tmp_path / "test.mjs").write_text(script, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(tmp_path / "test.mjs")], cwd=tmp_path,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js required")
def test_metadata_only_bounded_capture_log(tmp_path):
    (tmp_path / "diagnostics.mjs").write_text(
        (JS / "telehealthDiagnostics.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    run_js(tmp_path, r"""
import assert from 'node:assert/strict';
import { createTelehealthDiagnostics } from './diagnostics.mjs';
const log = createTelehealthDiagnostics({ now: () => '2026-11-06T00:00:00Z' });
log.emit('picker_selected', {
  surface: 'window', channel: 'patient', part: 2,
  url: 'https://confidential.example/123', title: 'PATIENT NAME',
  error: new Error('transcript content'), code: 'arbitrary sensitive message',
});
log.emit('untrusted event containing patient data', { surface: 'window' });
assert.deepEqual(log.snapshot().events, [{
  at: '2026-11-06T00:00:00Z', event: 'picker_selected',
  channel: 'patient', surface: 'window', part: 2,
}]);
for (let i = 0; i < 260; i++) log.emit('upload_accepted', { channel: 'patient', part: i });
assert.equal(log.length, 200);
assert.equal(log.snapshot().events[0].part, 60);
assert.ok(!JSON.stringify(log.snapshot()).includes('PATIENT NAME'));
log.clear();
assert.equal(log.length, 0);
""")


@pytest.mark.skipif(NODE is None, reason="Node.js required")
def test_silero_uses_cloned_tracks_and_only_reports_speech_frames(tmp_path):
    (tmp_path / "vad.mjs").write_text(
        (JS / "telehealthVad.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    run_js(tmp_path, r"""
import assert from 'node:assert/strict';
import { createTelehealthVadMonitor } from './vad.mjs';
const seen = [], statuses = [], events = [], clones = [];
class Track {
  constructor() { this.stopped = false; }
  stop() { this.stopped = true; }
  clone() { const cloned = new Track(); clones.push(cloned); return cloned; }
}
class Stream {
  constructor(tracks) { this.tracks = tracks; }
}
class Vad {
  static instances = [];
  static async new(options) {
    const inst = {
      options,
      started: false,
      async start() { this.started = true; },
      pause() {},
      destroy() {},
    };
    Vad.instances.push(inst);
    return inst;
  }
}
const patient = new Track(), clinician = new Track();
const monitor = createTelehealthVadMonitor({
  VadCtor: Vad, MediaStreamCtor: Stream,
  onSpeechFrame: event => seen.push(event),
  onState: event => statuses.push(event),
  onDiagnostic: event => events.push(event),
});
monitor.start({ patient, clinician });
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(Vad.instances.length, 2);
assert.ok(Vad.instances.every(v => v.started));
Vad.instances[0].options.onFrameProcessed({ isSpeech: 0.1 }, new Float32Array(512));
Vad.instances[0].options.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512));
assert.equal(seen.filter(v => v.speechMs > 0).length, 1);
assert.equal(seen.filter(v => v.speechMs === 0).length, 1);
assert.equal(statuses.filter(v => v.status === 'ready').length, 2);
monitor.stop();
assert.equal(clones.filter(v => v.stopped).length, 2);
assert.equal(patient.stopped, false);
assert.equal(clinician.stopped, false);
const before = seen.length;
Vad.instances[0].options.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512));
assert.equal(seen.length, before);
const unavailable = [];
createTelehealthVadMonitor({
  VadCtor: null, MediaStreamCtor: Stream,
  onState: e => unavailable.push(e),
}).start({ patient, clinician });
assert.equal(unavailable.filter(e => e.status === 'unavailable').length, 2);
""")


@pytest.mark.skipif(NODE is None, reason="Node.js required")
def test_window_audio_requires_explicit_approval_and_audio_track(tmp_path):
    (tmp_path / "telehealthTimeline.mjs").write_text(
        (JS / "telehealthTimeline.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "telehealthVad.mjs").write_text(
        (JS / "telehealthVad.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    src = (JS / "telehealthCapture.js").read_text(encoding="utf-8")
    src = re.sub(r"['\"]\./telehealthTimeline\.js(?:\?[^'\"]*)?['\"]", "'./telehealthTimeline.mjs'", src)
    src = re.sub(r"['\"]\./telehealthVad\.js(?:\?[^'\"]*)?['\"]", "'./telehealthVad.mjs'", src)
    (tmp_path / "capture.mjs").write_text(src, encoding="utf-8")
    run_js(tmp_path, r"""
import assert from 'node:assert/strict';
import { createTelehealthCapture, prepareTelehealthDisplaySource } from './capture.mjs';
class Track {
  constructor(kind, surface) {
    this.kind = kind; this.surface = surface; this.readyState = 'live';
    this.muted = false; this.listeners = {}; this.stopped = false;
  }
  getSettings() { return this.kind === 'video' ? { displaySurface: this.surface } : {}; }
  addEventListener(name, cb) { (this.listeners[name] ||= []).push(cb); }
  removeEventListener(name, cb) { this.listeners[name] = (this.listeners[name] || []).filter(fn => fn !== cb); }
  stop() { this.stopped = true; }
}
class Node {
  connect(target) { return target; }
  disconnect() {}
}
class Context {
  constructor() {
    this.sampleRate = 48000; this.currentTime = 0; this.state = 'running';
    this.destination = {}; this.audioWorklet = { addModule: async () => {} };
  }
  createGain() { const n = new Node(); n.gain = { value: 1 }; return n; }
  createMediaStreamSource() { return new Node(); }
  async suspend() { this.state = 'suspended'; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
}
class Tap extends Node {
  constructor() {
    super();
    this.port = {
      onmessage: null,
      postMessage: msg => {
        if (msg.type === 'flush') queueMicrotask(() => this.port.onmessage({
          data: { type: 'flushed', requestId: msg.requestId },
        }));
      },
    };
  }
}
globalThis.window = { setTimeout, clearTimeout, AudioContext: Context, AudioWorkletNode: Tap };
function devices(surface, hasAudio = true) {
  const video = new Track('video', surface);
  const audio = new Track('audio');
  const mic = new Track('audio');
  const display = {
    getTracks: () => hasAudio ? [video, audio] : [video],
    getVideoTracks: () => [video],
    getAudioTracks: () => hasAudio ? [audio] : [],
  };
  const calls = { microphone: 0, options: null };
  return {
    calls, video, audio, mic,
    mediaDevices: {
      async getDisplayMedia(options) { calls.options = options; return display; },
      async getUserMedia() {
        calls.microphone++;
        return { getTracks: () => [mic], getAudioTracks: () => [mic] };
      },
    },
  };
}
async function make(env, approved, onState = () => {}) {
  const events = [];
  const source = await prepareTelehealthDisplaySource({
    mediaDevices: env.mediaDevices, confirmWindowAudio: () => approved,
    onDiagnostic: event => events.push(event),
    onState,
  });
  const capture = createTelehealthCapture({
    mediaDevices: env.mediaDevices, preparedSource: source, AudioContextCtor: Context, AudioWorkletNodeCtor: Tap,
    onFrames: () => true,
    onDiagnostic: event => events.push(event),
  });
  return { capture, events, source };
}
const window = devices('window');
const synchronousSelection = devices('browser');
const pendingSelection = prepareTelehealthDisplaySource({ mediaDevices: synchronousSelection.mediaDevices });
assert.equal(synchronousSelection.calls.options.video, true); // Picker invocation is synchronous with consent.
assert.equal(synchronousSelection.calls.microphone, 0);
(await pendingSelection).release();
const allowed = await make(window, true);
assert.equal(window.calls.microphone, 0); // Selection does not request the microphone.
assert.equal(allowed.source.isLive(), true); // A live source may be silent until Start.
assert.equal(window.calls.options.windowAudio, 'window');
assert.equal(window.calls.options.systemAudio, 'exclude');
await allowed.capture.start();
assert.equal(window.calls.microphone, 1);
assert.equal(window.calls.options.surfaceSwitching, 'exclude');
assert.equal(allowed.events.some(e => e.event === 'window_acknowledged'), true);
await allowed.capture.stop();
assert.equal(window.video.stopped, true);
const noConsent = devices('window');
await assert.rejects(make(noConsent, false), /cancelled/);
assert.equal(noConsent.calls.microphone, 0);
assert.equal(noConsent.video.stopped, true);
const failedAcknowledgement = devices('window');
await assert.rejects(make(failedAcknowledgement, Promise.reject(new Error('Synthetic acknowledgement failure'))), /Synthetic acknowledgement failure/);
assert.equal(failedAcknowledgement.video.stopped, true);
assert.equal(failedAcknowledgement.audio.stopped, true);
const withoutAudio = devices('window', false);
await assert.rejects(make(withoutAudio, true), /No audio/);
assert.equal(withoutAudio.calls.microphone, 0);
const screen = devices('monitor');
await assert.rejects(make(screen, true), /Full-screen/);
assert.equal(screen.calls.microphone, 0);
const browser = devices('browser');
const browserCapture = await make(browser, false);
assert.equal(browser.calls.microphone, 0);
await browserCapture.capture.start();
assert.equal(browser.calls.microphone, 1);
await browserCapture.capture.stop();
const noTabAudio = devices('browser', false);
await assert.rejects(make(noTabAudio, false), /No tab audio/);
assert.equal(noTabAudio.calls.microphone, 0);

// A prepared source owns its tracks before Start, rejects reuse, and releases
// the entire selection if either display track ends while waiting.
const transferredDevices = devices('browser');
const preparedStatuses = [];
const transferInterruptions = [];
const oneShot = await make(transferredDevices, false, state => preparedStatuses.push(state));
oneShot.source.transfer(event => transferInterruptions.push(event));
assert.throws(() => oneShot.source.transfer(() => {}), /no longer available/);
transferredDevices.audio.listeners.mute[0]();
assert.deepEqual(transferInterruptions, [{ channel: 'patient', reason: 'source_muted' }]);
assert.deepEqual(preparedStatuses, ['connected']);
oneShot.source.release();
const waiting = devices('browser');
const waitingStates = [];
const waitingSource = await prepareTelehealthDisplaySource({
  mediaDevices: waiting.mediaDevices, onState: state => waitingStates.push(state),
});
assert.equal(waiting.calls.microphone, 0);
waiting.audio.listeners.ended[0]();
assert.equal(waitingSource.isLive(), false);
assert.equal(waiting.video.stopped, true);
assert.equal(waiting.audio.stopped, true);
assert.deepEqual(waitingStates, ['connected', 'ended']);

// The selected surface is checked again at Start, before microphone access.
const changedSurface = devices('browser');
const changedSource = await make(changedSurface, false);
changedSurface.video.surface = 'window';
await assert.rejects(changedSource.capture.start(), /source changed/);
assert.equal(changedSurface.calls.microphone, 0);
assert.equal(changedSurface.video.stopped, true);

// Transfer arms source monitoring before a pending microphone permission
// resolves, so ending the call source cancels startup and releases the late mic.
const pendingSourceDevices = devices('browser');
const pendingSource = await make(pendingSourceDevices, false);
let grantMicrophone;
pendingSourceDevices.mediaDevices.getUserMedia = () => new Promise(resolve => { grantMicrophone = resolve; });
const pendingCapture = createTelehealthCapture({
  mediaDevices: pendingSourceDevices.mediaDevices, preparedSource: pendingSource.source,
  AudioContextCtor: Context, AudioWorkletNodeCtor: Tap, onFrames: () => true,
});
const pendingStart = pendingCapture.start();
await Promise.resolve();
assert.equal(typeof grantMicrophone, 'function');
pendingSourceDevices.audio.listeners.ended[0]();
const lateMic = new Track('audio');
grantMicrophone({ getTracks: () => [lateMic], getAudioTracks: () => [lateMic] });
await assert.rejects(pendingStart, /selected source ended|cancelled/);
assert.equal(pendingSourceDevices.video.stopped, true);
assert.equal(pendingSourceDevices.audio.stopped, true);
assert.equal(lateMic.stopped, true);
""")
