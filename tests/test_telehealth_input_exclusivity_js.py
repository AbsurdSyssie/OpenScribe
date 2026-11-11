"""Actual recording-mode and upload action handlers honor Telehealth ownership."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js required for the action harness")
def test_recording_mode_and_audio_upload_actions_lock_and_recover(tmp_path):
    actions = (ROOT / "app/static/js/transcribe/actions.js").read_text(encoding="utf-8")
    actions = actions.replace("import { csrfFetch } from '../csrf.js';", "import { csrfFetch } from './csrf.mjs';")
    actions = actions.replace(
        "import { createGeneratedNoteRegenerationController } from './regeneration.js?v=20260911-note-regeneration';",
        "import { createGeneratedNoteRegenerationController } from './regeneration.mjs';",
    )
    (tmp_path / "actions.mjs").write_text(actions, encoding="utf-8")
    (tmp_path / "csrf.mjs").write_text(
        "export const csrfFetch = (...args) => globalThis.__actionsFetch(...args);\n", encoding="utf-8"
    )
    (tmp_path / "regeneration.mjs").write_text(
        "export function createGeneratedNoteRegenerationController(){return {}};\n",
        encoding="utf-8",
    )
    (tmp_path / "test.mjs").write_text(r"""
import assert from 'node:assert/strict';
import { attachTranscribeActions } from './actions.mjs';

class Field {
  constructor(value='') { this.value=value; this.dataset={}; this.listeners={}; this.disabled=false; this.files=[]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async emit(type, event={}) {
    event.preventDefault ||= () => {};
    for (const fn of this.listeners[type] || []) await fn(event);
  }
}
globalThis.Element = class {};
globalThis.HTMLTextAreaElement = class {};
globalThis.HTMLInputElement = class {};
globalThis.FormData = class { append() {} };
globalThis.document = { addEventListener() {} };
globalThis.window = {
  document: globalThis.document,
  addEventListener() {},
  matchMedia: () => ({ matches: false }),
  setTimeout: (fn) => setTimeout(fn, 0),
  confirm: () => true,
};

let telehealthSelected = true;
let uploadCount = 0;
let modePatchCount = 0;
let releaseTitleSync;
let titleSyncGate = null;
const mode = new Field('live_chunked');
const upload = new Field();
const file = new Field();
file.files = [{ name: 'synthetic-audio.wav' }];
const warnings = [];
const flashes = [];
globalThis.__actionsFetch = async (url, options={}) => {
  if (url.endsWith('/audio-file')) uploadCount += 1;
  if (options.method === 'PATCH') modePatchCount += 1;
  return { ok: true, status: 200, json: async () => ({}) };
};
const dom = new Proxy({ recordingModeSelect: mode, uploadForm: upload, fileInput: file }, {
  get(target, key) { return Object.hasOwn(target, key) ? target[key] : null; },
});
attachTranscribeActions({
  dom,
  routeBase: '/workspace',
  getTranscriptId: () => 'synthetic-transcript-id',
  getActiveIngestionMode: () => 'whole_file',
  getIsLiveCaptureUiActive: () => false,
  getIsTelehealthSelected: () => telehealthSelected,
  syncTranscriptTitleIfNeeded: () => titleSyncGate || Promise.resolve(),
  parseErrorMessage: async () => 'synthetic error',
  showFlash: (message, kind) => { flashes.push([message, kind]); if (kind === 'warning') warnings.push(message); },
  setVisibleStatus() {}, setSessionProgress() {}, setRetryAvailability() {},
  reflectBackendStatus() {}, setMicButtons() {}, fetchWorkspace: async () => {},
  scheduleWorkspaceRefreshBurst() {},
});
await mode.emit('change');
assert.equal(mode.value, 'whole_file', 'Telehealth selection restores the existing mode');
assert.equal(modePatchCount, 0, 'a direct change event cannot mutate the consultation mode');
await upload.emit('submit');
assert.equal(uploadCount, 0, 'upload submit is rejected while Telehealth owns audio');
assert.ok(warnings.some((message) => message.includes('Turn off Telehealth')));

telehealthSelected = false;
mode.value = 'live_chunked';
let releaseSync;
titleSyncGate = new Promise((resolve) => { releaseSync = resolve; });
const modeChange = mode.emit('change');
assert.equal(mode.dataset.modeChangeInFlight, 'true');
await mode.emit('change');
assert.equal(modePatchCount, 0, 'a duplicate mode action waits behind the first operation');
releaseSync();
await modeChange;
assert.equal(modePatchCount, 1, 'ordinary mode changes work after Telehealth is switched off');

let releaseUpload;
titleSyncGate = new Promise((resolve) => { releaseUpload = resolve; });
const firstUpload = upload.emit('submit');
assert.equal(upload.dataset.uploadInFlight, 'true');
await upload.emit('submit');
assert.equal(uploadCount, 0, 'the second submission cannot pass the in-flight guard');
releaseUpload();
await firstUpload;
assert.equal(uploadCount, 1, 'ordinary upload works again after Telehealth is switched off');
assert.equal(upload.dataset.uploadInFlight, undefined);
console.log('Telehealth input exclusivity and ordinary-action recovery: passed');
""", encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(tmp_path / "test.mjs")], cwd=tmp_path,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
