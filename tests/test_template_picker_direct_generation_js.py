"""Synthetic behavior checks for the template picker direct-generation guard."""

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

const source = fs.readFileSync(process.argv[2], 'utf8');
const helperStart = source.indexOf('      const isTemplatePickerDirectGenerationAvailable =');
const helperEnd = source.indexOf('\n      const splitSuggestionReadiness', helperStart);
const actionStart = source.indexOf('      const generateTemplateFromPicker = async (button) => {');
const actionEnd = source.indexOf('\n\n      const navigateFromTemplatePicker', actionStart);
assert.ok(helperStart >= 0 && helperEnd > helperStart, 'picker eligibility helper boundaries must exist');
assert.ok(actionStart >= 0 && actionEnd > actionStart, 'picker action boundaries must exist');

function createPicker(state) {
  const submitButton = {disabled: false};
  const form = {querySelector: () => submitButton, submits: 0, requestSubmit() { this.submits += 1; }};
  const select = {value: 'template-a', dispatchEvent() {}};
  const option = {dataset: {templateId: 'template-a'}};
  const row = {querySelector: () => option};
  const button = {disabled: false, closest: () => row};
  state.actionButtons = [button];
  let transcriptId = 'synthetic-transcript';
  let multipleProblemsSavePending = Boolean(state.savePending);
  let templatePickerActionInFlight = false;
  let templatePickerSelectionInFlight = false;
  let noteGenerationBusy = false;
  let templateChangeInFlight = Boolean(state.changePending);
  state.select = select;
  state.form = form;
  const fragment = source.slice(helperStart, helperEnd) + '\n' + source.slice(actionStart, actionEnd);
  const factory = new Function('state', 'button', `
    let transcriptId = 'synthetic-transcript';
    let multipleProblemsSavePending = Boolean(state.savePending);
    let templatePickerActionInFlight = false;
    let templatePickerSelectionInFlight = false;
    let noteGenerationBusy = false;
    let templateChangeInFlight = Boolean(state.changePending);
    const templatePickerActionButtons = state.actionButtons;
    const splitCreateEnabled = () => Boolean(state.splitEnabled);
    const syncGenerationAvailability = () => {};
    const readActiveDraftText = () => 'synthetic source';
    const handleOutputTemplateChange = () => state.changePromise;
    const closeTemplatePicker = () => { state.closed = true; };
    const generateOutputTemplateSelect = state.select;
    const generateOutputForm = state.form;
    const Event = class { constructor(type) { this.type = type; }};
    ${fragment}
    return { generateTemplateFromPicker, isTemplatePickerDirectGenerationAvailable, syncTemplatePickerDirectGenerationVisibility };
  `);
  return { ...factory(state, button), form, select, submitButton, button };
}

// Direct Generate is blocked before it can enqueue when split creation is active.
{
  const state = {splitEnabled: true, form: null, select: null};
  const setup = createPicker(state);
  assert.equal(setup.isTemplatePickerDirectGenerationAvailable(), false);
  setup.syncTemplatePickerDirectGenerationVisibility();
  assert.equal(setup.button.hidden, true);
  state.splitEnabled = false;
  setup.syncTemplatePickerDirectGenerationVisibility();
  assert.equal(setup.button.hidden, false);
  state.splitEnabled = true;
  await setup.generateTemplateFromPicker(setup.button);
  assert.equal(setup.form.submits, 0);
}

// An in-flight selection/save that becomes split-enabled while awaited cannot submit afterward.
{
  let resolveChange;
  const changePromise = new Promise(resolve => { resolveChange = resolve; });
  const state = {splitEnabled: false, changePending: true, changePromise};
  const setup = createPicker(state);
  assert.equal(setup.isTemplatePickerDirectGenerationAvailable(), true);
  const action = setup.generateTemplateFromPicker(setup.button);
  state.splitEnabled = true;
  setup.syncTemplatePickerDirectGenerationVisibility();
  assert.equal(setup.button.hidden, true);
  resolveChange(true);
  await action;
  assert.equal(setup.form.submits, 0);
  state.splitEnabled = false;
  setup.syncTemplatePickerDirectGenerationVisibility();
  assert.equal(setup.button.hidden, false);
}

// Save-pending state also blocks an otherwise valid direct action.
{
  const state = {splitEnabled: false, savePending: true};
  const setup = createPicker(state);
  assert.equal(setup.isTemplatePickerDirectGenerationAvailable(), false);
  setup.syncTemplatePickerDirectGenerationVisibility();
  assert.equal(setup.button.hidden, true);
  await setup.generateTemplateFromPicker(setup.button);
  assert.equal(setup.form.submits, 0);
}

// Ordinary single-note direct generation still submits through the canonical form.
{
  const state = {splitEnabled: false};
  const setup = createPicker(state);
  await setup.generateTemplateFromPicker(setup.button);
  assert.equal(setup.form.submits, 1);
}
'''


def test_template_picker_direct_generation_stays_single_note_only(tmp_path: Path) -> None:
    runner = tmp_path / "template-picker-direct-generation.mjs"
    runner.write_text(HARNESS)
    completed = subprocess.run(
        [NODE, str(runner), str(ROOT / "app/static/js/transcribe/app.js")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
