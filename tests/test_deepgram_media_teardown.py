import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the synthetic media-controller test")
def test_deepgram_media_teardown_ignores_late_vad_callbacks_and_cancels_connecting_capture(tmp_path):
    root = tmp_path / "module-root"
    transcribe = root / "app/static/js/transcribe"
    transcribe.mkdir(parents=True)
    (root / "package.json").write_text('{"type":"module"}')
    (root / "app/static/js/csrf.js").write_text((Path(__file__).parents[1] / "app/static/js/csrf.js").read_text())
    (transcribe / "media.js").write_text((Path(__file__).parents[1] / "app/static/js/transcribe/media.js").read_text())
    (transcribe / "micVisualizer.js").write_text((Path(__file__).parents[1] / "app/static/js/transcribe/micVisualizer.js").read_text())
    (transcribe / "deepgramLiveStream.js").write_text((Path(__file__).parents[1] / "app/static/js/transcribe/deepgramLiveStream.js").read_text())
    (transcribe / "vadCapture.js").write_text((Path(__file__).parents[1] / "app/static/js/transcribe/vadCapture.js").read_text())

    script = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const fetchCalls = [];
globalThis.fetch = async (...args) => { fetchCalls.push(args); return { ok: true, status: 200, json: async () => ({}) }; };
globalThis.window = {
  location: { protocol: 'https:', host: 'openscribe.test', href: 'https://openscribe.test/transcribe' },
  localStorage: { getItem: () => null, setItem: () => {} },
  crypto: { randomUUID: () => 'synthetic-random-id' },
  OpenScribeCSRF: { getToken: () => 'synthetic-csrf' },
  setTimeout, clearTimeout, setInterval, clearInterval,
  confirm: () => true,
};
globalThis.document = {
  hidden: false,
  cookie: '',
  addEventListener: () => {},
  dispatchEvent: () => {},
  querySelector: () => null,
};
globalThis.CustomEvent = class CustomEvent { constructor(type) { this.type = type; } };
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
});

class FakeWebSocket {
  static OPEN = 1;
  static CLOSING = 2;
  constructor(url) { this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = []; this.listeners = {}; FakeWebSocket.last = this; (FakeWebSocket.all ||= []).push(this); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  emit(name, event = {}) { for (const callback of this.listeners[name] || []) callback(event); }
  send(value) {
    this.sent.push(value);
    if (typeof value === 'string' && JSON.parse(value).type === 'stop') {
      queueMicrotask(() => this.json({ type: 'finalized' }));
    }
  }
  close() { this.readyState = 3; this.emit('close'); }
  open() { this.readyState = FakeWebSocket.OPEN; this.emit('open'); }
  json(value) { this.emit('message', { data: JSON.stringify(value) }); }
}
globalThis.WebSocket = FakeWebSocket;

const vadInstances = [];
class FakeMicVAD {
  static async new(options) { const instance = new FakeMicVAD(options); vadInstances.push(instance); return instance; }
  constructor(options) { this.options = options; this.started = false; this.destroyed = false; this.frameProcessor = { audioBuffer: [] }; }
  start() { this.started = true; }
  async pause() {}
  destroy() {
    this.destroyed = true;
    // Simulate a VAD implementation delivering its final speech callback after teardown.
    setTimeout(() => this.options.onSpeechEnd(new Float32Array(16000).fill(0.25)), 35);
  }
}
window.vad = { MicVAD: FakeMicVAD };

const recordListeners = {};
const recordButton = {
  disabled: false,
  addEventListener(name, callback) { (recordListeners[name] ||= []).push(callback); },
  click() { for (const callback of recordListeners.click || []) callback(); },
};
const { createAudioCaptureController } = await import(pathToFileURL(process.argv[1]));
const controller = createAudioCaptureController({
  dom: { recordToggleButton: recordButton },
  config: {
    batchVadPreRollMs: 0, batchVadSilenceThresholdMs: 0, batchVadTrailingBufferMs: 0,
    liveChunkOverlapMs: 0, liveChunkUploadMinIntervalMs: 0, liveMaxChunkMs: 30000,
    liveMinChunkMs: 100, livePostRollTrimMs: 0, livePreRollMs: 0,
    liveRestartDelayMs: 0, liveSilenceThresholdMs: 2000,
    liveVadAssetBasePath: '/', liveVadModel: 'synthetic', liveVadOnnxBasePath: '/',
    liveVadSampleRate: 16000,
  },
  canUseLiveInput: () => true,
  canUseWholeFileInput: () => false,
  getState: () => ({ transcriptId: '11111111-1111-4111-8111-111111111111', activeIngestionMode: 'live_chunked', deepgramLiveStreamingAvailable: true, nextLiveChunkSequenceNo: 1 }),
  setNextLiveChunkSequenceNo: () => {},
  getDefaultMicStatusState: () => ({ message: 'ready', kind: '' }),
  syncTranscriptTitleIfNeeded: async () => {},
  finalizeLiveCapture: async () => null,
  fetchWorkspace: async () => null,
  pollWorkspace: () => {},
  scheduleWorkspaceRefreshBurst: () => {},
  parseErrorResponse: async () => 'error',
  parseErrorMessage: async (_response, fallback) => fallback,
  setMicButtons: () => {},
  setMicStatus: () => {},
  setVisibleStatus: () => {},
  setSessionProgress: () => {},
  setRetryAvailability: () => {},
  showFlash: () => {},
  reflectBackendStatus: () => {},
  reportMicIssue: () => {},
  confirmBeforeStartRecording: async () => true,
});
controller.attachDomListeners();
const waitFor = async (predicate, label) => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  assert.fail(`Timed out waiting for ${label}`);
};
const connect = async () => {
  const previousSocketCount = FakeWebSocket.all?.length || 0;
  recordButton.click();
  await waitFor(() => (FakeWebSocket.all?.length || 0) > previousSocketCount, 'WebSocket creation');
  const socket = FakeWebSocket.last;
  socket.open();
  await waitFor(() => socket.sent.some((value) => typeof value === 'string' && JSON.parse(value).type === 'start'), 'stream start');
  socket.json({ type: 'ready', stream_id: '22222222-2222-4222-8222-222222222222', sample_rate: 16000, encoding: 'pcm_s16le' });
  await waitFor(() => vadInstances.length > 0 && vadInstances.at(-1).started, 'VAD start');
  return socket;
};
const stop = async () => {
  recordButton.click();
  await waitFor(() => !controller.isCaptureUiActive(), 'capture teardown');
};

await connect();
await stop();
assert.equal(vadInstances[0].destroyed, true);
assert.equal(fetchCalls.length, 0, 'stream teardown must not POST prerecorded audio');

await connect();
await new Promise((resolve) => setTimeout(resolve, 60));
assert.equal(controller.isCaptureUiActive(), true, 'a late stopped-VAD callback must not reset the new capture');
assert.equal(fetchCalls.length, 0, 'a late speech-end callback must not replay to audio-chunks');
await stop();

const previousSocketCount = FakeWebSocket.all.length;
recordButton.click();
await waitFor(() => FakeWebSocket.all.length > previousSocketCount, 'pending WebSocket creation');
const pendingSocket = FakeWebSocket.last;
pendingSocket.open();
await waitFor(() => pendingSocket.sent.some((value) => typeof value === 'string' && JSON.parse(value).type === 'start'), 'pending stream start');
assert.equal(controller.isCaptureUiActive(), true, 'connecting capture remains active for Stop/navigation guards');
recordButton.click();
await waitFor(() => !controller.isCaptureUiActive(), 'pending connection cancellation');
const vadCountBeforeLateReady = vadInstances.length;
pendingSocket.json({ type: 'ready', stream_id: '33333333-3333-4333-8333-333333333333', sample_rate: 16000, encoding: 'pcm_s16le' });
await new Promise((resolve) => setTimeout(resolve, 10));
assert.equal(vadInstances.length, vadCountBeforeLateReady, 'late ready must not resume microphone capture');
assert.equal(controller.isCaptureUiActive(), false);
assert.equal(fetchCalls.length, 0);
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, str(transcribe / "media.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
