"""Node synthetic tests for canonical, silence-preserving telehealth WAV."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_AVAILABLE = shutil.which("node") is not None


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js required")
def test_telehealth_pcm16_wav_sample_rate_and_original_clock(tmp_path):
    source = ROOT / "app/static/js/transcribe/telehealthWav.js"
    (tmp_path / "telehealthWav.mjs").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test.mjs").write_text(
        r"""
import assert from 'node:assert/strict';
import { encodeTelehealthWavWindow } from './telehealthWav.mjs';

// Speech and silence are both retained; timeline offsets never shift.
const samples = new Float32Array(48000);
for (let i = 0; i < 24000; i++) samples[i] = 0.25;
const encoded = encodeTelehealthWavWindow({
  samples, sampleRate: 48000, startFrame: 48000, endFrame: 96000,
});
assert.equal(encoded.fileSamples, 16000);
assert.equal(encoded.fileSampleRate, 16000);
assert.equal(encoded.bytes.byteLength, 32044);
assert.deepEqual(encoded.spans, [{
  file_start_sample: 0,
  file_end_sample: 16000,
  timeline_start_frame: 48000,
  timeline_end_frame: 96000,
}]);
const header = new DataView(encoded.bytes.buffer);
assert.equal(header.getUint32(0, true), 0x46464952);
assert.equal(header.getUint32(8, true), 0x45564157);
assert.equal(header.getUint16(20, true), 1);
assert.equal(header.getUint16(22, true), 1);
assert.equal(header.getUint32(24, true), 16000);
assert.equal(header.getUint32(40, true), 32000);
assert.ok(header.getInt16(44, true) > 7000);
assert.equal(header.getInt16(44 + 14000 * 2, true), 0); // Silence NOT trimmed.

assert.throws(() => encodeTelehealthWavWindow({
  samples, sampleRate: 48000, startFrame: 0, endFrame: 47999,
}), /Invalid/);
assert.throws(() => encodeTelehealthWavWindow({
  samples: new Float32Array(0), sampleRate: 48000, startFrame: 0, endFrame: 0,
}), /Invalid/);
""",
        encoding="utf-8",
    )
    subprocess.run(
        ["node", str(tmp_path / "test.mjs")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
