"""Shared microphone visualizer regressions using the real module and synthetic audio frames."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the visualizer harness")
def test_shared_visualizer_buckets_frames_independently_and_cleans_up_paused_observers():
    root = Path(__file__).resolve().parents[1]
    visualizer = root / "app/static/js/transcribe/micVisualizer.js"
    script = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { createMicVisualizer } = await import(pathToFileURL(process.argv[1]).href);
let nextFrameId = 1;
const queuedFrames = new Map();
const canceledFrames = [];
globalThis.window = {
  requestAnimationFrame(callback) { const id = nextFrameId++; queuedFrames.set(id, callback); return id; },
  cancelAnimationFrame(id) { canceledFrames.push(id); queuedFrames.delete(id); },
};
globalThis.document = {
  createDocumentFragment: () => ({ children: [], appendChild(node) { this.children.push(node); } }),
  createElement: () => ({ style: { values: {}, setProperty(key, value) { this.values[key] = value; } } }),
};
const makeElement = () => ({ dataset: {}, bars: [], appendChild(fragment) { this.bars.push(...fragment.children); } });
const firstElement = makeElement();
const secondElement = makeElement();
const first = createMicVisualizer(firstElement);
const second = createMicVisualizer(secondElement);
assert.equal(firstElement.bars.length, 12);
assert.equal(secondElement.bars.length, 12);
const separatedFrame = new Float32Array(48);
separatedFrame.fill(1, 0, 4);
first.updateFrame(separatedFrame);
assert.ok(Math.abs(Number(firstElement.bars[0].style.values['--level']) - 0.613) < 1e-12, 'the first audio bucket drives its matching bar');
assert.ok(Math.abs(Number(firstElement.bars[1].style.values['--level']) - 0.129) < 1e-12, 'quiet buckets remain low');
assert.equal(secondElement.bars[0].style.values['--level'], '0.14');
first.setVadActive(true);
assert.equal(firstElement.dataset.vadActive, 'true');
assert.equal(secondElement.dataset.vadActive, 'false');

let closed = 0;
let tracksStopped = 0;
let amplitude = 0.1;
class FakeAudioContext {
  createMediaStreamSource(stream) { this.stream = stream; return { connect() {} }; }
  createAnalyser() {
    return { fftSize: 32, getFloatTimeDomainData(frame) { frame.fill(amplitude); } };
  }
  resume() { return Promise.resolve(); }
  close() { closed += 1; return Promise.resolve(); }
}
const observed = createMicVisualizer(makeElement(), { AudioContextCtor: FakeAudioContext });
const ownedStream = { getTracks: () => [{ stop() { tracksStopped += 1; } }] };
assert.equal(observed.startStream(ownedStream), true);
assert.equal(observed.startStream(ownedStream), true);
assert.equal(closed, 1, 'restarting closes the previous analyser context');
// Retain the target element reference in a separate instance for visible stale-frame assertions.
const pausedElement = makeElement();
const paused = createMicVisualizer(pausedElement, { AudioContextCtor: FakeAudioContext });
paused.startStream(ownedStream);
await Promise.resolve();
await Promise.resolve();
const pausedCallback = [...queuedFrames.values()].at(-1);
paused.pause();
const levelsAtPause = pausedElement.bars.map((bar) => bar.style.values['--level']);
assert.equal(pausedElement.dataset.vadActive, 'false');
pausedCallback();
assert.deepEqual(pausedElement.bars.map((bar) => bar.style.values['--level']), levelsAtPause, 'late callbacks after pause cannot update bars');
paused.resume();
await Promise.resolve();
await Promise.resolve();
amplitude = 0.8;
[...queuedFrames.values()].at(-1)?.();
assert.notDeepEqual(pausedElement.bars.map((bar) => bar.style.values['--level']), levelsAtPause, 'resume observes the current analyser frame');
const stoppedCallback = [...queuedFrames.values()].at(-1);
paused.stop();
assert.equal(tracksStopped, 0, 'visualizer cleanup never stops the caller-owned microphone tracks');
assert.ok(closed >= 2, 'stop closes the active analyser context');
const levelsAtStop = pausedElement.bars.map((bar) => bar.style.values['--level']);
stoppedCallback?.();
assert.deepEqual(pausedElement.bars.map((bar) => bar.style.values['--level']), levelsAtStop, 'late callbacks after stop cannot update bars');

const noAudio = createMicVisualizer(makeElement());
assert.equal(noAudio.startStream(ownedStream), false, 'missing AudioContext only disables the cosmetic visualizer');
"""
    completed = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script, str(visualizer)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
