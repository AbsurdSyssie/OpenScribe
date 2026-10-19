import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the pinned VAD runtime test")
def test_pinned_vad_streaming_buffer_is_bounded_without_resetting_speech_state(tmp_path):
    source = Path(__file__).parents[1]
    root = tmp_path / "module-root"
    transcribe = root / "app/static/js/transcribe"
    vendor = root / "app/static/vendor/vad-web/0.0.29"
    transcribe.mkdir(parents=True)
    vendor.mkdir(parents=True)
    (root / "package.json").write_text('{"type":"module"}')
    (transcribe / "deepgramLiveStream.js").write_text((source / "app/static/js/transcribe/deepgramLiveStream.js").read_text())
    (vendor / "bundle.min.js").write_text((source / "app/static/vendor/vad-web/0.0.29/bundle.min.js").read_text())

    script = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { pathToFileURL } from 'node:url';

const self = { ort: {} };
const context = {
  self, console, setTimeout, clearTimeout, TextEncoder, TextDecoder, Uint8Array,
  ArrayBuffer, DataView, Promise, Map, Set, WeakMap, Proxy, Reflect, Object,
  Error, TypeError, Math, Number, String, Array, Symbol, BigInt, JSON,
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const { FrameProcessor, Message } = self.vad;
const { trimPinnedVadSpeechBuffer } = await import(pathToFileURL(process.argv[1]));
const options = {
  positiveSpeechThreshold: 0.3,
  negativeSpeechThreshold: 0.1,
  redemptionMs: 2000,
  preSpeechPadMs: 100,
  minSpeechMs: 100,
};
let processor;
let speechStarts = 0;
processor = new FrameProcessor(
  async () => ({ isSpeech: 0.9 }),
  () => {},
  options,
  32,
);
processor.resume();
const frame = new Float32Array(512).fill(0.2);
for (let index = 0; index < 2000; index += 1) {
  await processor.process(frame, (event) => {
    if (event.msg === Message.FrameProcessed) {
      assert.equal(trimPinnedVadSpeechBuffer(processor, event.frame, 16000, 5), true);
    }
    if (event.msg === Message.SpeechStart) speechStarts += 1;
  });
}
const capAfterCurrentPush = Math.ceil((5 * 16000) / frame.length);
assert.ok(processor.audioBuffer.length <= capAfterCurrentPush);
assert.equal(processor.speaking, true, 'trimming audio must not reset VAD speech state');
assert.equal(speechStarts, 1, 'VAD speech detection continues after the buffer reaches its cap');
assert.equal(trimPinnedVadSpeechBuffer({ audioBuffer: null }, frame), false, 'an unsupported processor shape fails closed');
"""
    result = subprocess.run(
        [
            shutil.which("node"),
            "--input-type=module",
            "-e",
            script,
            str(transcribe / "deepgramLiveStream.js"),
            str(vendor / "bundle.min.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
