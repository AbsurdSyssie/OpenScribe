import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the synthetic media-controller test")
def test_batch_capture_startup_cancellation_owns_late_vad_and_microphone_resources(tmp_path):
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
const waitFor = async (predicate, label) => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  assert.fail(`Timed out waiting for ${label}`);
};

const makeHarness = async ({ deferFirstPermission = false } = {}) => {
  const streams = [];
  const micRequests = [];
  const vadCalls = [];
  const uploads = [];
  let activeTranscriptId = 'synthetic-transcript';
  const getUserMedia = async () => {
    const pending = deferFirstPermission && micRequests.length === 0 ? deferred() : null;
    micRequests.push(pending);
    if (pending) return pending.promise;
    const track = { stopped: false, stop() { this.stopped = true; } };
    const stream = { track, getTracks: () => [track] };
    streams.push(stream);
    return stream;
  };
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { mediaDevices: { getUserMedia } },
  });
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ capture_settings_id: `settings-${vadCalls.length + 1}`, connection_type: 'batch', diarisation_enabled: false }),
  });
  globalThis.window = {
    location: { protocol: 'https:', host: 'openscribe.test', href: 'https://openscribe.test/workspace' },
    localStorage: { getItem: () => null, setItem: () => {} },
    crypto: { randomUUID: () => 'synthetic-id' },
    OpenScribeCSRF: { getToken: () => 'synthetic-csrf' },
    setTimeout, clearTimeout, setInterval, clearInterval, confirm: () => false,
  };
  globalThis.document = {
    hidden: false, cookie: '', addEventListener: () => {}, dispatchEvent: () => {},
    createDocumentFragment: () => ({ appendChild() {} }),
    createElement: () => ({ style: { setProperty() {} } }),
    querySelector: () => null,
  };
  globalThis.CustomEvent = class CustomEvent { constructor(type) { this.type = type; } };
  globalThis.window.vad = {
    MicVAD: {
      async new(options) {
        const stream = await options.getStream();
        const gate = deferred();
        const instance = {
          options, stream, active: false, destroyed: false,
          start() { this.active = true; },
          pause() {
            if (this.active) options.onSpeechEnd(new Float32Array(32000).fill(0.25));
            this.active = false;
            return options.pauseStream(stream);
          },
          destroy() { this.destroyed = true; this.active = false; return options.pauseStream(stream); },
        };
        vadCalls.push({ options, stream, gate, instance });
        return gate.promise.then(() => instance);
      },
    },
  };
  const { createAudioCaptureController } = await import(pathToFileURL(process.argv[1]));
  const listeners = {};
  const recordButton = {
    addEventListener(name, callback) { (listeners[name] ||= []).push(callback); },
    click() { for (const callback of listeners.click || []) callback(); },
  };
  const controller = createAudioCaptureController({
    dom: { recordToggleButton: recordButton, micVisualizer: { dataset: {}, appendChild() {} } },
    config: {
      batchVadPreRollMs: 300, batchVadSilenceThresholdMs: 500, batchVadTrailingBufferMs: 100,
      liveMinChunkMs: 1000, liveVadModel: 'synthetic', liveVadAssetBasePath: '/', liveVadOnnxBasePath: '/',
      liveVadSampleRate: 16000,
    },
    canUseLiveInput: () => false,
    canUseWholeFileInput: () => true,
    getState: () => ({ transcriptId: activeTranscriptId, activeIngestionMode: 'whole_file', deepgramLiveStreamingAvailable: false }),
    syncTranscriptTitleIfNeeded: async () => {},
    uploadBatchAudio: async (...args) => { uploads.push(args); },
    setMicButtons() {}, setMicStatus() {}, setVisibleStatus() {}, setSessionProgress() {},
    setRetryAvailability() {}, showFlash() {}, reflectBackendStatus() {}, reportMicIssue() {},
    getDefaultMicStatusState: () => ({ message: '', kind: 'default' }),
  });
  controller.attachDomListeners();
  return { controller, recordButton, streams, micRequests, vadCalls, uploads, switchTranscript: (id) => { activeTranscriptId = id; } };
};
const click = async (button) => { button.click(); await new Promise((resolve) => setTimeout(resolve, 5)); };
const startDeferredVad = async (harness, index) => {
  await waitFor(() => harness.vadCalls.length > index, `VAD ${index} microphone acquisition`);
  harness.vadCalls[index].gate.resolve();
  await waitFor(() => harness.vadCalls[index].instance.active && harness.controller.isCaptureUiActive(), `VAD ${index} active capture`);
  return harness.vadCalls[index];
};

const runLateResolveAndReject = async (lateResult) => {
  const h = await makeHarness();
  await click(h.recordButton);
  await waitFor(() => h.vadCalls.length === 1, 'first batch startup');
  await click(h.recordButton); // Stop while the first MicVAD.new is unresolved.
  await waitFor(() => !h.controller.isCaptureUiActive(), 'first batch stop');
  await new Promise((resolve) => setTimeout(resolve, 5)); // Let its scheduled finalizer reset the generation.
  await click(h.recordButton);
  await waitFor(() => h.vadCalls.length === 2, 'second batch startup');
  const current = await startDeferredVad(h, 1);
  assert.equal(current.stream.track.stopped, false);

  if (lateResult === 'resolve') {
    h.vadCalls[0].gate.resolve();
  } else {
    h.vadCalls[0].gate.reject(new Error('synthetic delayed model initialization failure'));
  }
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(h.controller.isCaptureUiActive(), true, `late ${lateResult} must preserve the current capture`);
  assert.equal(current.instance.active, true, `late ${lateResult} must not stop the current VAD`);
  assert.equal(current.stream.track.stopped, false, `late ${lateResult} must not stop the current microphone`);
  assert.equal(h.vadCalls[0].stream.track.stopped, true, `late ${lateResult} must release the stale microphone`);
  const uploadsBeforeStaleCallback = h.uploads.length;
  h.vadCalls[0].options.onSpeechEnd(new Float32Array(32000).fill(0.25));
  assert.equal(h.uploads.length, uploadsBeforeStaleCallback, 'stale speech callbacks must not upload audio');
  h.recordButton.click();
  await waitFor(() => !h.controller.isCaptureUiActive(), 'current batch stop');
  assert.equal(current.stream.track.stopped, true, 'Stop releases the current microphone');
  await waitFor(() => h.uploads.length > uploadsBeforeStaleCallback, 'normal Stop final speech upload');
};

await runLateResolveAndReject('resolve');
await runLateResolveAndReject('reject');

const h = await makeHarness({ deferFirstPermission: true });
await click(h.recordButton);
await waitFor(() => h.micRequests.length === 1, 'first permission request');
await click(h.recordButton);
await waitFor(() => !h.controller.isCaptureUiActive(), 'permission-pending capture cancellation');
await new Promise((resolve) => setTimeout(resolve, 5));
await click(h.recordButton);
const current = await startDeferredVad(h, 0);
const lateTrack = { stopped: false, stop() { this.stopped = true; } };
const lateStream = { track: lateTrack, getTracks: () => [lateTrack] };
h.micRequests[0].resolve(lateStream);
await waitFor(() => lateTrack.stopped, 'late permission stream release');
assert.equal(h.controller.isCaptureUiActive(), true, 'late permission must preserve the later recording');
assert.equal(current.stream.track.stopped, false, 'late permission cleanup must preserve the current microphone');
h.recordButton.click();
await waitFor(() => !h.controller.isCaptureUiActive(), 'permission race current batch stop');

const switched = await makeHarness({ deferFirstPermission: true });
await click(switched.recordButton);
await waitFor(() => switched.micRequests.length === 1, 'session-switch permission request');
switched.switchTranscript('different-synthetic-transcript');
const switchedTrack = { stopped: false, stop() { this.stopped = true; } };
switched.micRequests[0].resolve({ track: switchedTrack, getTracks: () => [switchedTrack] });
await waitFor(() => switchedTrack.stopped, 'late microphone release after transcript switch');
assert.equal(switched.controller.isCaptureUiActive(), false, 'a late startup cannot remain active after its transcript changes');
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, str(transcribe / "media.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
