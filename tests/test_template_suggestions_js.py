import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_template_suggestion_workspace_and_description_hooks():
    workspace = (ROOT / "app/templates/transcribe/_workspace.html").read_text()
    editor = (ROOT / "app/templates/_template_editor_workspace.html").read_text()
    app_js = (ROOT / "app/static/js/transcribe/app.js").read_text()

    assert 'data-template-suggestion role="status" aria-live="polite" hidden' in workspace
    assert "data-template-suggestion-use" in workspace
    assert "data-template-suggestion-dismiss" in workspace
    assert "createTemplateSuggestionController" in app_js
    assert "chooseTemplate: chooseTemplateFromPicker" in app_js
    assert "This helps AI understand what your template is for." in editor
    assert 'aria-describedby="template-description-help"' in editor
    assert 'placeholder="e.g. Mental health follow-up consultations and medication reviews"' in editor


def test_template_suggestion_controller_threshold_suppression_and_acceptance(tmp_path):
    runner = tmp_path / "template-suggestion-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/templateSuggestions.js").as_uri()
    runner.write_text(
        f"""
globalThis.window = {{ setTimeout: (callback) => callback() }};
globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
const {{ createTemplateSuggestionController }} = await import('{module_uri}');

class Target {{
  constructor() {{ this.listeners = new Map(); this.hidden = true; }}
  addEventListener(type, callback) {{ this.listeners.set(type, callback); }}
  removeEventListener() {{}}
  fire(type) {{ this.listeners.get(type)?.(); }}
}}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const text = {{ textContent: 'x'.repeat(1199) }};
const select = new Target();
select.value = 'one';
select.options = [
  {{ value: 'one', dataset: {{ templateName: 'General' }}, textContent: 'General' }},
  {{ value: 'two', dataset: {{ templateName: 'Mental health' }}, textContent: 'Mental health' }},
];
const region = new Target();
const message = {{ textContent: '' }};
const use = new Target();
const dismiss = new Target();
let transcriptId = 'transcript-1';
let posts = 0;
let failNextPost = false;
let chosen = null;
const fetcher = async (_url, options) => {{
  if (options.method === 'POST') {{
    posts += 1;
    if (failNextPost) {{ failNextPost = false; throw new Error('temporary network failure'); }}
  }}
  return {{ ok: true, json: async () => ({{ status: 'completed', suggestion: {{ template_id: 'two', template_name: 'Mental health', confidence: 'high' }} }}) }};
}};
const controller = createTemplateSuggestionController({{
  transcriptText: text, templateSelect: select, suggestionRegion: region,
  suggestionMessage: message, useButton: use, dismissButton: dismiss,
  getTranscriptId: () => transcriptId, chooseTemplate: (id) => {{ chosen = id; select.value = id; select.fire('change'); }}, fetcher,
}});
controller.attach();
await flush();
if (posts !== 0) throw new Error('requested below threshold');
text.textContent += 'y';
await controller.requestIfEligible();
if (posts !== 1 || region.hidden || !message.textContent.includes('Mental health')) throw new Error('suggestion not shown');
await controller.requestIfEligible();
if (posts !== 1) throw new Error('duplicate request');
use.fire('click');
if (chosen !== 'two' || !region.hidden) throw new Error('acceptance did not use selection path');

transcriptId = 'transcript-2';
select.value = 'one';
text.textContent = 'z'.repeat(1200);
await controller.requestIfEligible();
select.fire('change');
await flush();
if (!region.hidden) throw new Error('manual choice did not suppress stale response');

transcriptId = 'transcript-3';
text.textContent = 'r'.repeat(1200);
failNextPost = true;
await controller.requestIfEligible();
await controller.requestIfEligible();
if (posts !== 4) throw new Error('failed POST did not release browser request guard');

let scheduledPoll = null;
let getAttempts = 0;
const resumeController = createTemplateSuggestionController({{
  transcriptText: text, templateSelect: select, suggestionRegion: region,
  suggestionMessage: message, useButton: use, dismissButton: dismiss,
  getTranscriptId: () => transcriptId, chooseTemplate: () => {{}},
  fetcher: async (_url, options) => {{
    if (options.method === 'POST') return {{ ok: true, json: async () => ({{ status: 'pending', suggestion: null }}) }};
    getAttempts += 1;
    if (getAttempts === 1) throw new Error('temporary poll failure');
    return {{ ok: true, json: async () => ({{ status: 'completed', suggestion: null }}) }};
  }},
  schedule: (callback) => {{ scheduledPoll = callback; }},
}});
resumeController.attach();
await flush();
await scheduledPoll();
await flush();
await resumeController.requestIfEligible();
await flush();
if (getAttempts !== 2) throw new Error('failed GET poll was not resumable');
"""
    )
    env = {**os.environ, "NODE_NO_WARNINGS": "1"}
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env=env)
