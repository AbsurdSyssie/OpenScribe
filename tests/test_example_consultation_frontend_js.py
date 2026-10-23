"""Focused browser-harness coverage for first-use examples and capture boundaries."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for the frontend behavior harness")


HARNESS = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const helperSource = fs.readFileSync(process.argv[1], 'utf8').replaceAll('export function ', 'function ');
const helperSandbox = { Boolean };
helperSandbox.globalThis = helperSandbox;
vm.createContext(helperSandbox);
vm.runInContext(`${helperSource}\nglobalThis.createExampleConsultationInitializer = createExampleConsultationInitializer; globalThis.createExampleConsultationBoundary = createExampleConsultationBoundary;`, helperSandbox);
const { createExampleConsultationInitializer, createExampleConsultationBoundary } = helperSandbox;

let selectedTranscript = false;
const calls = [];
const initialize = createExampleConsultationInitializer({
  pending: true,
  hasSelectedTranscript: () => selectedTranscript,
  createExample: async () => { calls.push('create-example'); return 'example-id'; },
  canOpenTranscript: () => !selectedTranscript,
  openTranscript: async (id) => { calls.push(`open:${id}`); },
  refreshWorkspace: async () => { calls.push('refresh'); },
});
assert.equal(await initialize(), true);
assert.equal(await initialize(), false);
assert.deepEqual(calls, ['create-example', 'open:example-id']);

selectedTranscript = false;
const raceCalls = [];
const initializeDuringSelection = createExampleConsultationInitializer({
  pending: true,
  hasSelectedTranscript: () => false,
  createExample: async () => { selectedTranscript = true; return 'example-id'; },
  canOpenTranscript: () => !selectedTranscript,
  openTranscript: async (id) => raceCalls.push(`open:${id}`),
  refreshWorkspace: async () => raceCalls.push('refresh-selected'),
});
assert.equal(await initializeDuringSelection(), true);
assert.deepEqual(raceCalls, ['refresh-selected']);

let createFreshCount = 0;
let isExample = true;
let finishFreshConsultation;
const boundary = createExampleConsultationBoundary({
  isExample: () => isExample,
  createConsultation: () => {
    createFreshCount += 1;
    return new Promise((resolve) => {
      finishFreshConsultation = () => { isExample = false; resolve(true); };
    });
  },
});
const firstBoundaryCall = boundary();
const secondBoundaryCall = boundary();
assert.equal(createFreshCount, 1, 'concurrent capture actions share one fresh consultation');
finishFreshConsultation();
assert.equal(await firstBoundaryCall, true);
assert.equal(await secondBoundaryCall, true);
assert.equal(await boundary(), true);
assert.equal(createFreshCount, 1);

const mediaSource = fs.readFileSync(process.argv[2], 'utf8');
const start = mediaSource.indexOf('    if (dom.audioActionTrigger) {');
const end = mediaSource.indexOf('    if (dom.recordToggleButton) {', start);
assert.ok(start >= 0 && end > start, 'upload-picker listener boundaries must exist');
const events = [];
const trigger = { addEventListener(type, handler) { this.click = handler; } };
const fileInput = { click() { events.push('picker'); } };
const canUseWholeFileInput = () => true;
new Function('dom', 'canUseWholeFileInput', mediaSource.slice(start, end))(
  { audioActionTrigger: trigger, fileInput }, canUseWholeFileInput,
);
trigger.click();
assert.deepEqual(events, ['picker'], 'open the picker directly from the user click');

const uploadStart = mediaSource.indexOf('    if (dom.fileInput && dom.uploadForm) {');
const uploadEnd = mediaSource.indexOf('    dom.pendingAudioRetryButton?.addEventListener', uploadStart);
assert.ok(uploadStart >= 0 && uploadEnd > uploadStart, 'upload change listener boundaries must exist');
const selectedFile = { name: 'recording.wav' };
const uploadInput = {
  files: [selectedFile],
  addEventListener(type, handler) { this.change = handler; },
};
const uploadEvents = [];
const uploadForm = {
  addEventListener(type, handler) { this.submit = handler; },
  requestSubmit() { uploadEvents.push('submit'); },
};
const beforeUploadFileSelected = async () => {
  assert.equal(uploadInput.files[0], selectedFile, 'keep the chosen file through the consultation switch');
  uploadEvents.push('fresh-consultation');
  return true;
};
const setVisibleStatus = () => uploadEvents.push('upload-status');
const setSessionProgress = () => {};
new Function('dom', 'beforeUploadFileSelected', 'setVisibleStatus', 'setSessionProgress', mediaSource.slice(uploadStart, uploadEnd))(
  { fileInput: uploadInput, uploadForm }, beforeUploadFileSelected, setVisibleStatus, setSessionProgress,
);
await uploadInput.change();
assert.deepEqual(uploadEvents, ['fresh-consultation', 'upload-status', 'submit']);
'''


def test_example_initialization_and_upload_boundary_are_once_and_ordered() -> None:
    helper_path = ROOT / "app/static/js/transcribe/exampleConsultation.js"
    media_path = ROOT / "app/static/js/transcribe/media.js"
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", HARNESS, str(helper_path), str(media_path)],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
