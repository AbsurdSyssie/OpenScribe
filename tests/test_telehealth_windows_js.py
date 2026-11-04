"""Pure Node behavioural tests for bounded two-source PCM window handoff."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_AVAILABLE = shutil.which("node") is not None


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js required")
def test_telehealth_contiguous_windows_gaps_and_retry(tmp_path):
    js = ROOT / "app/static/js/transcribe/telehealthWindows.js"
    (tmp_path / "telehealthWindows.mjs").write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test.mjs").write_text(
        r"""
import assert from 'node:assert/strict';
import { createTelehealthWindows } from './telehealthWindows.mjs';

const accepted = [], gaps = [];
const windows = createTelehealthWindows({
  sampleRate: 8,
  windowSeconds: 1,
  maxPendingWindows: 4,
  onWindow: async (window) => { accepted.push(window); },
  onGap: (gap) => { gaps.push(gap); },
});
assert.equal(windows.ingest({ channel: 'clinician', startFrame: 0, endFrame: 5,
  samples: new Float32Array(5).fill(1) }), true);
assert.equal(windows.ingest({ channel: 'clinician', startFrame: 5, endFrame: 10,
  samples: new Float32Array(5).fill(2) }), true);
assert.equal(windows.ingest({ channel: 'patient', startFrame: 4, endFrame: 12,
  samples: new Float32Array(8).fill(3) }), true);
assert.equal(windows.ingest({ channel: 'clinician', startFrame: 9, endFrame: 11,
  samples: new Float32Array(2) }), false); // Never accept overlapping frames.
await windows.finish();
assert.deepEqual(accepted.map((win) => [win.channel, win.startFrame, win.endFrame]), [
  ['clinician', 0, 8],
  ['patient', 4, 12],
  ['clinician', 8, 10],
]);
assert.equal(accepted[0].samples.length, 8);
assert.deepEqual(Array.from(accepted[0].samples), [1,1,1,1,1,2,2,2]);
assert.deepEqual(gaps, [{ channel: 'patient', startFrame: 0, endFrame: 4 }]);
assert.equal(windows.status().pendingWindows, 0);

let failOnce = true;
const delivered = [];
const recoverable = createTelehealthWindows({
  sampleRate: 4, windowSeconds: 1, maxPendingWindows: 3,
  onWindow: async (window) => {
    if (failOnce) { failOnce = false; throw new Error('synthetic Vault upload interruption'); }
    delivered.push(window);
  },
});
assert.equal(recoverable.ingest({
  channel: 'patient', startFrame: 0, endFrame: 4, samples: new Float32Array(4).fill(.7),
}), true);
await assert.rejects(recoverable.finish(), /synthetic Vault upload interruption/);
assert.equal(recoverable.status().pendingWindows, 1);
assert.equal(await recoverable.retryPending(), true);
assert.equal(delivered.length, 1);
assert.equal(delivered[0].samples.length, 4);

let release;
const blocked = createTelehealthWindows({
  sampleRate: 4, windowSeconds: 1, maxPendingWindows: 1,
  onWindow: () => new Promise((resolve) => { release = resolve; }),
});
assert.equal(blocked.ingest({
  channel: 'clinician', startFrame: 0, endFrame: 4, samples: new Float32Array(4),
}), true);
assert.equal(blocked.ingest({
  channel: 'clinician', startFrame: 4, endFrame: 8, samples: new Float32Array(4),
}), false); // Explicitly fails instead of silently dropping two-source PCM.
release();
await assert.rejects(blocked.finish(), /backpressured/);
assert.equal(blocked.status().pendingWindows, 0);
assert.equal(blocked.status().bufferedFrames.clinician, 4);
assert.equal(await blocked.retryPending(), true);
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
