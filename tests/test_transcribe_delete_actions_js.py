"""Node behavioral coverage for the real consultation-delete submit handler."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for the frontend behavior harness")


HARNESS = r'''
import fs from 'node:fs';
import assert from 'node:assert/strict';

const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  if (dom.bulkDeleteForm) {');
const end = source.indexOf('  if (dom.titleForm) {', start);
assert.ok(start >= 0 && end > start, 'bulk delete handler boundaries must exist');
const calls = [];
const boxes = JSON.parse(process.argv[2]).map(({id, hasContent}) => ({
  checked: true,
  value: id,
  dataset: {hasSavedContent: hasContent ? 'true' : 'false'},
}));
const form = {addEventListener(type, callback) { this.submit = callback; }};
const dom = {
  bulkDeleteForm: form,
  sessionList: {querySelectorAll() { return boxes; }},
};
let accepted = process.argv[3] === 'accept';
let confirmMessage = null;
let location = null;
const window = {
  confirm(message) { confirmMessage = message; return accepted; },
  location: {assign(value) { location = value; }},
};
const csrfFetch = async (url, options) => {
  calls.push({url, options});
  return {ok: true};
};
const showFlash = () => {};
const parseErrorMessage = () => 'delete failed';
const document = {};
const routeBase = '/workspace';
new Function('dom', 'window', 'document', 'csrfFetch', 'showFlash', 'parseErrorMessage', 'routeBase', source.slice(start, end))(
  dom, window, document, csrfFetch, showFlash, parseErrorMessage, routeBase,
);
await form.submit({preventDefault() {}});
assert.equal(confirmMessage, process.argv[4] === 'expect-prompt' ? process.argv[5] : null);
const expectedDeletes = accepted ? boxes.length : 0;
assert.equal(calls.length, expectedDeletes);
assert.equal(location, accepted ? '/workspace' : null);
if (accepted) {
  assert.deepEqual(calls.map(call => call.url), boxes.map(box => `/api/v1/transcripts/${box.value}`));
  assert.ok(calls.every(call => call.options.method === 'DELETE'));
}
'''


def run_case(boxes: list[dict[str, object]], decision: str, prompt: str | None) -> None:
    args = [NODE, "--input-type=module", "-e", HARNESS, str(ROOT / "app/static/js/transcribe/actions.js"), __import__("json").dumps(boxes), decision]
    if prompt is None:
        args.append("no-prompt")
        args.append("")
    else:
        args.extend(("expect-prompt", prompt))
    completed = subprocess.run(args, text=True, capture_output=True, check=False, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_single_delete_confirmation_cancel_and_proceed() -> None:
    prompt = "This consultation has saved content. Delete it permanently?"
    run_case([{"id": "tx-single", "hasContent": True}], "cancel", prompt)
    run_case([{"id": "tx-single", "hasContent": True}], "accept", prompt)


def test_bulk_delete_confirmation_cancel_and_proceed() -> None:
    prompt = "One or more selected consultations have saved content. Delete them permanently?"
    boxes = [
        {"id": "tx-one", "hasContent": False},
        {"id": "tx-two", "hasContent": True},
    ]
    run_case(boxes, "cancel", prompt)
    run_case(boxes, "accept", prompt)


def test_empty_consultation_deletion_does_not_prompt() -> None:
    run_case([{"id": "tx-empty", "hasContent": False}], "accept", None)
