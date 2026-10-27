import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the browser protocol unit test")
def test_deepgram_stream_frames_pause_resume_and_bound_backpressure(tmp_path):
    source = Path(__file__).parents[1] / "app/static/js/transcribe/deepgramLiveStream.js"
    module = tmp_path / "deepgramLiveStream.mjs"
    module.write_text(source.read_text())
    script = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

globalThis.window = { setTimeout, clearTimeout, location: { protocol: 'https:', host: 'openscribe.test' } };
class FakeWebSocket {
  static OPEN = 1;
  static CLOSING = 2;
  static CONNECTING = 0;
  constructor(url) { this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = []; this.listeners = {}; FakeWebSocket.last = this; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  emit(name, event = {}) { for (const callback of this.listeners[name] || []) callback(event); }
  send(value) { this.sent.push(value); }
  close() { this.readyState = 3; }
  open() { this.readyState = FakeWebSocket.OPEN; this.emit('open'); }
  json(value) { this.emit('message', { data: JSON.stringify(value) }); }
}
globalThis.WebSocket = FakeWebSocket;
const { DeepgramLiveStream, pcm16FrameMessage } = await import(pathToFileURL(process.argv[1]));

const wire = pcm16FrameMessage(0x010203040506, new Int16Array([-2, 3]));
const wireView = new DataView(wire);
assert.equal(wireView.getUint8(0), 1);
assert.equal(wireView.getBigUint64(1, true), 0x010203040506n);
assert.equal(wireView.getUint32(9, true), 2);
assert.equal(wireView.getInt16(13, true), -2);
assert.equal(wireView.getInt16(15, true), 3);

const events = [];
let errorInfo = null;
const stream = new DeepgramLiveStream({ onState: (state) => events.push(state), onError: (_error, details) => { errorInfo = details; } });
const ready = stream.connect({ transcriptId: 'owner-transcript', csrfToken: 'synthetic-csrf' });
const socket = FakeWebSocket.last;
assert.match(socket.url, /^wss:\/\/openscribe\.test\/api\/v1\/transcripts\/owner-transcript\/deepgram-stream$/);
socket.open();
assert.deepEqual(JSON.parse(socket.sent[0]), { type: 'start', csrf_token: 'synthetic-csrf' });
socket.json({ type: 'ready', stream_id: '11111111-1111-4111-8111-111111111111', sample_rate: 16000, encoding: 'pcm_s16le' });
await ready;

let dictationPreview = null;
let dictationWindow = null;
const dictationStream = new DeepgramLiveStream({
  onDictationPreview: (message) => { dictationPreview = message; },
  onWindowCommitted: (message) => { dictationWindow = message; },
});
const dictationReady = dictationStream.connect({
  transcriptId: 'owner-transcript',
  csrfToken: 'synthetic-csrf',
  url: '/api/v1/transcripts/owner-transcript/post-consultation-dictation/deepgram-stream',
});
const dictationSocket = FakeWebSocket.last;
assert.match(dictationSocket.url, /post-consultation-dictation\/deepgram-stream$/);
dictationSocket.open();
dictationSocket.json({ type: 'ready', stream_id: '22222222-2222-4222-8222-222222222222', sample_rate: 16000, encoding: 'pcm_s16le' });
await dictationReady;
dictationSocket.json({ type: 'dictation_preview', window_no: 0, confirmed_text: 'Confirmed', interim_text: ' interim' });
assert.deepEqual(dictationPreview, { type: 'dictation_preview', window_no: 0, confirmed_text: 'Confirmed', interim_text: ' interim' });
dictationSocket.json({ type: 'window_committed', window_no: 0, next_sample_offset: 0, text: 'Confirmed' });
assert.equal(dictationWindow.text, 'Confirmed');
dictationStream.cancelPending();

stream.pushFrame(new Float32Array(512).fill(0.5), true, 1000);
stream.speechStarted();
for (let index = 0; index < 15; index += 1) stream.pushFrame(new Float32Array(512).fill(0.25), false, 1001 + index);
const firstBinary = socket.sent.find((value) => value instanceof ArrayBuffer);
assert.ok(firstBinary);
const firstHeader = new DataView(firstBinary);
assert.equal(firstHeader.getBigUint64(1, true), 0n);
assert.equal(firstHeader.getUint32(9, true), 8192);
socket.json({ type: 'received', next_sample_offset: 8192 });
socket.json({ type: 'window_committed', window_no: 1, next_sample_offset: 8192 });
assert.equal(stream.pendingRecoverySamples({ start: 0, end: 8192, count: 8192 }), null);
stream.pendingCommit = [
  { offset: 100, samples: new Int16Array(40).fill(4) },
  { offset: 160, samples: new Int16Array(40).fill(5) },
];
stream.pendingCommitSamples = 80;
stream.outbound = [new Int16Array(20).fill(6)];
stream.ring = [{ offset: 200, samples: new Int16Array(10).fill(7) }];
const exactRecovery = stream.pendingRecoverySamples({ start: 100, end: 200, count: 80 });
assert.equal(exactRecovery.length, 80);
assert.equal(exactRecovery[0], 4);
assert.equal(exactRecovery.at(-1), 5);
assert.equal(stream.pendingRecoverySamples({ start: 100, end: 200, count: 90 }), null);

const gapStream = new DeepgramLiveStream({ onError: (_error, details) => { errorInfo = details; } });
const gapReady = gapStream.connect({ transcriptId: 'owner-transcript', csrfToken: 'synthetic-csrf' });
const gapSocket = FakeWebSocket.last;
gapSocket.open();
gapSocket.json({ type: 'ready', stream_id: '33333333-3333-4333-8333-333333333333', sample_rate: 16000, encoding: 'pcm_s16le' });
await gapReady;
gapStream.pendingCommit = [
  { offset: 1000, samples: new Int16Array(20).fill(8) },
  { offset: 1040, samples: new Int16Array(20).fill(9) },
];
gapStream.pendingCommitSamples = 40;
gapStream.outbound = [new Int16Array(10).fill(1)];
gapStream.ring = [{ offset: 1100, samples: new Int16Array(10).fill(2) }];
gapSocket.json({ type: 'error', code: 'quota_exceeded', unresolved: true, recovery_available: false,
  source_start_sample_offset: 1000, source_end_sample_offset: 1060, forwarded_sample_count: 40 });
assert.equal(errorInfo.unresolved, true);
assert.equal(errorInfo.recoveryAvailable, false);
assert.equal(errorInfo.samples.length, 40);
assert.equal(errorInfo.samples.at(-1), 9);

stream.pushFrame(new Float32Array(512), false, 32001);
assert.deepEqual(JSON.parse(socket.sent.at(-1)), { type: 'paused' });
for (let index = 0; index < 170; index += 1) stream.pushFrame(new Float32Array(512), false, 32002 + index);
stream.pushFrame(new Float32Array(512).fill(0.4), true, 40000);
stream.speechStarted();
for (let index = 0; index < 14; index += 1) stream.pushFrame(new Float32Array(512).fill(0.2), false, 40001 + index);
const binaries = socket.sent.filter((value) => value instanceof ArrayBuffer);
const secondHeader = new DataView(binaries[1]);
assert.ok(secondHeader.getBigUint64(1, true) > 8192n);
assert.ok(secondHeader.getBigUint64(1, true) >= firstHeader.getBigUint64(1, true) + BigInt(firstHeader.getUint32(9, true)));
assert.ok(secondHeader.getUint32(9, true) > 0);
assert.ok(events.includes('streaming'));

const slowStream = new DeepgramLiveStream({ onError: (_error, details) => { errorInfo = details; } });
const slowReady = slowStream.connect({ transcriptId: 'owner-transcript', csrfToken: 'synthetic-csrf' });
const slowSocket = FakeWebSocket.last;
slowSocket.open();
slowSocket.json({ type: 'ready', stream_id: '22222222-2222-4222-8222-222222222222', sample_rate: 16000, encoding: 'pcm_s16le' });
await slowReady;
slowStream.pushFrame(new Float32Array(512).fill(0.5), true, 50000);
slowStream.speechStarted();
slowSocket.bufferedAmount = 200 * 1024;
for (let index = 0; index < 15; index += 1) slowStream.pushFrame(new Float32Array(512).fill(0.5), false, 50001 + index);
assert.equal(errorInfo.streamId, '22222222-2222-4222-8222-222222222222');
assert.equal(errorInfo.samples, null);
assert.equal(slowSocket.sent.filter((value) => value instanceof ArrayBuffer).length, 0);

let overflowError = null;
const overflowStream = new DeepgramLiveStream({ onError: (error) => { overflowError = error; } });
const overflowReady = overflowStream.connect({ transcriptId: 'owner-transcript', csrfToken: 'synthetic-csrf' });
const overflowSocket = FakeWebSocket.last;
overflowSocket.open();
overflowSocket.json({ type: 'ready', stream_id: '44444444-4444-4444-8444-444444444444', sample_rate: 16000, encoding: 'pcm_s16le' });
await overflowReady;
overflowStream.pushFrame(new Float32Array(512).fill(0.5), true, 70000);
overflowStream.speechStarted();
for (let index = 0; index < 327; index += 1) {
  assert.doesNotThrow(() => overflowStream.pushFrame(new Float32Array(512).fill(0.5), true, 70001 + index));
}
assert.equal(overflowStream.stopped, false);
assert.equal(overflowStream.outboundSamples, 159744);
assert.equal(overflowStream.ringSamples, 5 * 16000);
overflowStream.pushFrame(new Float32Array(512).fill(0.5), true, 70312);
assert.equal(overflowStream.stopped, true);
assert.match(overflowError.message, /receiving audio too slowly/);
assert.equal(overflowSocket.sent.filter((value) => value instanceof ArrayBuffer).length, 1);

const cancelStream = new DeepgramLiveStream();
const cancelledReady = cancelStream.connect({ transcriptId: 'owner-transcript', csrfToken: 'synthetic-csrf' });
const cancelSocket = FakeWebSocket.last;
cancelSocket.open();
cancelStream.cancelPending();
await assert.rejects(cancelledReady, /connection was canceled/);
assert.equal(cancelStream.stopped, true);
assert.equal(cancelSocket.readyState, 3);
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, str(module)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
