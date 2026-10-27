import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the synthetic PCM recovery test")
def test_recovery_wav_preserves_every_signed_pcm16_sample(tmp_path):
    source = Path(__file__).parents[1]
    root = tmp_path / "module-root"
    transcribe = root / "app/static/js/transcribe"
    transcribe.mkdir(parents=True)
    (root / "package.json").write_text('{"type":"module"}')
    (root / "app/static/js/csrf.js").write_text((source / "app/static/js/csrf.js").read_text())
    (transcribe / "media.js").write_text((source / "app/static/js/transcribe/media.js").read_text())
    (transcribe / "vadCapture.js").write_text((source / "app/static/js/transcribe/vadCapture.js").read_text())
    (transcribe / "micVisualizer.js").write_text((source / "app/static/js/transcribe/micVisualizer.js").read_text())
    (transcribe / "deepgramLiveStream.js").write_text((source / "app/static/js/transcribe/deepgramLiveStream.js").read_text())

    script = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { createLivePcm16WavBlob } = await import(pathToFileURL(process.argv[1]));

const samples = new Int16Array(65536);
for (let index = 0; index < samples.length; index += 1) samples[index] = index - 32768;
const blob = createLivePcm16WavBlob(samples, 16000);
assert.equal(blob.type, 'audio/wav');
const wav = new DataView(await blob.arrayBuffer());
assert.equal(wav.getUint32(24, true), 16000);
assert.equal(wav.getUint32(40, true), samples.length * 2);
for (let index = 0; index < samples.length; index += 1) {
  assert.equal(wav.getInt16(44 + (index * 2), true), samples[index], `sample ${samples[index]} changed`);
}
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, str(transcribe / "media.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
