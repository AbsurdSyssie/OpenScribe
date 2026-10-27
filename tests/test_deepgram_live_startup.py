import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the synthetic media-controller test")
def test_deepgram_startup_prepares_vad_in_parallel_and_waits_for_capture(tmp_path):
    root = tmp_path / "module-root"
    transcribe = root / "app/static/js/transcribe"
    transcribe.mkdir(parents=True)
    (root / "package.json").write_text('{"type":"module"}')
    source_root = Path(__file__).parents[1]
    (root / "app/static/js/csrf.js").write_text((source_root / "app/static/js/csrf.js").read_text())
    (transcribe / "media.js").write_text((source_root / "app/static/js/transcribe/media.js").read_text())
    (transcribe / "micVisualizer.js").write_text((source_root / "app/static/js/transcribe/micVisualizer.js").read_text())
    (transcribe / "deepgramLiveStream.js").write_text((source_root / "app/static/js/transcribe/deepgramLiveStream.js").read_text())
    (transcribe / "vadCapture.js").write_text((source_root / "app/static/js/transcribe/vadCapture.js").read_text())

    script = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
let recordingTimerStarts = 0;
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
globalThis.window = {
  location: { protocol: 'https:', host: 'openscribe.test', href: 'https://openscribe.test/transcribe' },
  localStorage: { getItem: () => null, setItem: () => {} },
  crypto: { randomUUID: () => 'synthetic-random-id' },
  OpenScribeCSRF: { getToken: () => 'synthetic-csrf' },
  setTimeout, clearTimeout, setInterval: () => ++recordingTimerStarts, clearInterval: () => {}, confirm: () => true,
};
const dispatched = [];
globalThis.document = {
  hidden: false, cookie: '', addEventListener: () => {},
  dispatchEvent: (event) => { dispatched.push(event.type); }, querySelector: () => null,
};
globalThis.CustomEvent = class CustomEvent { constructor(type) { this.type = type; } };
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: { mediaDevices: { getUserMedia: async () => {
    if (pendingMicRequest) return pendingMicRequest.promise;
    const track = { stopped: false, stop() { this.stopped = true; } };
    const stream = { track, getTracks: () => [track] };
    micStreams.push(stream);
    return stream;
  } } },
});

class FakeWebSocket {
  static OPEN = 1;
  static CLOSING = 2;
  constructor(url) { this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = []; this.listeners = {}; FakeWebSocket.last = this; }
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

const vadDeferreds = [];
const startDeferreds = [];
const vadInstances = [];
const micStreams = [];
let pendingMicRequest = null;
class FakeMicVAD {
  static new(options) {
    const pending = deferred();
    const defaults = {
      getStream: async () => navigator.mediaDevices.getUserMedia({ audio: true }),
      pauseStream: async (stream) => stream.getTracks().forEach((track) => track.stop()),
    };
    vadDeferreds.push({ options: { ...defaults, ...options }, pending });
    return pending.promise;
  }
  constructor(options) { this.options = options; this.started = false; this.destroyed = false; this.frameProcessor = { audioBuffer: [] }; }
  async start() {
    this.startCalled = true;
    this.stream = await this.options.getStream();
    const pending = deferred();
    startDeferreds.push(pending);
    await pending.promise;
    this.started = true;
  }
  async pause() {
    if (this.started) await this.options.pauseStream(this.stream);
    this.started = false;
  }
  destroy() { this.destroyed = true; if (this.started) void this.pause(); }
}
window.vad = { MicVAD: FakeMicVAD };

const recordListeners = {};
const recordButton = {
  disabled: false,
  addEventListener(name, callback) { (recordListeners[name] ||= []).push(callback); },
  click() { for (const callback of recordListeners.click || []) callback(); },
};
const statuses = [];
const { createAudioCaptureController } = await import(pathToFileURL(process.argv[1]));
const controller = createAudioCaptureController({
  dom: { recordToggleButton: recordButton },
  config: {
    batchVadPreRollMs: 0, batchVadSilenceThresholdMs: 0, batchVadTrailingBufferMs: 0,
    liveChunkOverlapMs: 0, liveChunkUploadMinIntervalMs: 0, liveMaxChunkMs: 30000,
    liveMinChunkMs: 100, livePostRollTrimMs: 0, livePreRollMs: 0,
    liveRestartDelayMs: 0, liveSilenceThresholdMs: 2000,
    liveVadAssetBasePath: '/', liveVadModel: 'synthetic', liveVadOnnxBasePath: '/', liveVadSampleRate: 16000,
  },
  canUseLiveInput: () => true, canUseWholeFileInput: () => false,
  getState: () => ({ transcriptId: '11111111-1111-4111-8111-111111111111', activeIngestionMode: 'live_chunked', deepgramLiveStreamingAvailable: true, nextLiveChunkSequenceNo: 1 }),
  setNextLiveChunkSequenceNo: () => {}, getDefaultMicStatusState: () => ({ message: 'ready', kind: '' }),
  syncTranscriptTitleIfNeeded: async () => {}, finalizeLiveCapture: async () => null,
  fetchWorkspace: async () => null, pollWorkspace: () => {}, scheduleWorkspaceRefreshBurst: () => {},
  parseErrorResponse: async () => 'error', parseErrorMessage: async (_response, fallback) => fallback,
  setMicButtons: () => {}, setMicStatus: (message) => statuses.push(message),
  setVisibleStatus: (state) => statuses.push(state), setSessionProgress: () => {},
  setRetryAvailability: () => {}, showFlash: () => {}, reflectBackendStatus: () => {},
  reportMicIssue: () => {}, confirmBeforeStartRecording: async () => true,
});
controller.attachDomListeners();
const waitFor = async (predicate, label) => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  assert.fail(`Timed out waiting for ${label}`);
};

recordButton.click();
await waitFor(() => FakeWebSocket.last && vadDeferreds.length > 0, 'parallel socket and VAD initialization');
const socket = FakeWebSocket.last;
assert.equal(vadDeferreds[0].options.startOnLoad, false, 'VAD preparation must not open the microphone');
assert.equal(vadInstances.length, 0);
socket.open();
await waitFor(() => socket.sent.some((value) => typeof value === 'string' && JSON.parse(value).type === 'start'), 'stream start request');
assert.ok(!statuses.includes('listening'), 'socket callbacks must not claim capture is listening before VAD starts');

const vad = new FakeMicVAD(vadDeferreds[0].options);
vadInstances.push(vad);
vadDeferreds[0].pending.resolve(vad);
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(vad.startCalled, undefined, 'microphone must wait for server readiness');
socket.json({ type: 'ready', stream_id: '22222222-2222-4222-8222-222222222222', sample_rate: 16000, encoding: 'pcm_s16le' });
await waitFor(() => vad.startCalled, 'actual microphone startup');
await waitFor(() => startDeferreds.length === 1, 'microphone acquired');
assert.ok(!dispatched.includes('openscribe:recording-started'), 'recording event waits for microphone startup');
assert.ok(!statuses.includes('listening'), 'Listening waits for microphone startup');
assert.equal(recordingTimerStarts, 0, 'recording timer waits for microphone startup');

startDeferreds[0].resolve();
await waitFor(() => dispatched.includes('openscribe:recording-started'), 'recording started event');
assert.ok(statuses.includes('listening'));
assert.equal(recordingTimerStarts, 1, 'recording timer starts with microphone capture');
assert.equal(controller.isCaptureUiActive(), true);
recordButton.click();
await waitFor(() => !controller.isCaptureUiActive(), 'capture stop');
assert.equal(vad.stream.track.stopped, true, 'normal Stop releases the active microphone track');

const startAnotherAttempt = async ({ holdMic = false } = {}) => {
  const previousSocket = FakeWebSocket.last;
  const previousStartCount = startDeferreds.length;
  if (holdMic) pendingMicRequest = deferred();
  recordButton.click();
  await waitFor(() => FakeWebSocket.last !== previousSocket && vadDeferreds.length > vadInstances.length, 'retry socket and VAD initialization');
  const retrySocket = FakeWebSocket.last;
  const vadIndex = vadDeferreds.length - 1;
  retrySocket.open();
  await waitFor(() => retrySocket.sent.some((value) => typeof value === 'string' && JSON.parse(value).type === 'start'), 'retry stream start');
  const retryVad = new FakeMicVAD(vadDeferreds[vadIndex].options);
  vadInstances.push(retryVad);
  vadDeferreds[vadIndex].pending.resolve(retryVad);
  retrySocket.json({ type: 'ready', stream_id: `22222222-2222-4222-8222-22222222222${vadIndex}`, sample_rate: 16000, encoding: 'pcm_s16le' });
  await waitFor(() => retryVad.startCalled, 'retry microphone startup');
  if (holdMic) {
    await waitFor(() => pendingMicRequest !== null, 'deferred microphone request');
  } else {
    await waitFor(() => startDeferreds.length > previousStartCount, 'retry microphone acquired');
  }
  return { retrySocket, retryVad };
};

const cancelledAttempt = await startAnotherAttempt({ holdMic: true });
recordButton.click();
await waitFor(() => !controller.isCaptureUiActive(), 'pending microphone cancellation');
const cancelledMicRequest = pendingMicRequest;
pendingMicRequest = null;
const overlappingRetry = await startAnotherAttempt();
const retryMicStream = overlappingRetry.retryVad.stream;
const lateTrack = { stopped: false, stop() { this.stopped = true; } };
const lateMicStream = { track: lateTrack, getTracks: () => [lateTrack] };
cancelledMicRequest.resolve(lateMicStream);
await waitFor(() => lateTrack.stopped, 'late microphone track release');
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(cancelledAttempt.retryVad.started, false, 'late permission resolution must not resume capture');
assert.equal(retryMicStream.track.stopped, false, 'a stale permission result must not stop the retry microphone');
startDeferreds.at(-1).resolve();
await waitFor(() => overlappingRetry.retryVad.started, 'retry while stale permission settles');
recordButton.click();
await waitFor(() => !controller.isCaptureUiActive(), 'overlapping retry stop');
assert.equal(retryMicStream.track.stopped, true, 'the retry releases its own microphone on Stop');

const failedAttempt = await startAnotherAttempt();
startDeferreds.at(-1).reject(new Error('synthetic microphone startup failure'));
await waitFor(() => !controller.isCaptureUiActive(), 'failed microphone startup teardown');
assert.equal(failedAttempt.retryVad.stream.track.stopped, true, 'failed startup releases its microphone track');
const startedEventsAfterFailure = dispatched.filter((event) => event === 'openscribe:recording-started').length;
assert.equal(failedAttempt.retryVad.started, false);
const successfulRetry = await startAnotherAttempt();
startDeferreds.at(-1).resolve();
await waitFor(() => successfulRetry.retryVad.started, 'retry after startup failure');
await waitFor(() => dispatched.filter((event) => event === 'openscribe:recording-started').length === startedEventsAfterFailure + 1, 'retry recording event');
recordButton.click();
await waitFor(() => !controller.isCaptureUiActive(), 'retry stop');
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, str(transcribe / "media.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
