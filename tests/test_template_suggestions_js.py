import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_template_suggestion_workspace_and_description_hooks():
    workspace = (ROOT / "app/templates/transcribe/_workspace.html").read_text()
    preferences = (ROOT / "app/templates/settings/_preferences.html").read_text()
    editor = (ROOT / "app/templates/_template_editor_workspace.html").read_text()
    app_js = (ROOT / "app/static/js/transcribe/app.js").read_text()

    assert 'data-template-suggestion role="status" aria-live="polite" hidden' in workspace
    assert "data-template-suggestion-use" in workspace
    assert "data-template-suggestion-dismiss" in workspace
    assert "data-template-suggestion-preference" not in workspace
    assert "data-template-suggestion-preference" in preferences
    assert "Suggest a template based on the consultation" in preferences
    assert "AI will suggest which of your templates matches the consultation" in preferences
    assert "createTemplateSuggestionController" in app_js
    assert "chooseTemplate: chooseTemplateFromPicker" in app_js
    assert "This helps AI understand what your template is for." in editor
    assert 'aria-describedby="template-description-help"' in editor
    assert 'placeholder="e.g. Mental health follow-up consultations and medication reviews"' in editor


def test_template_suggestion_popover_uses_static_csp_safe_styles():
    controller = (ROOT / "app/static/js/transcribe/templateSuggestions.js").read_text()
    styles = (ROOT / "app/static/css/transcribe.css").read_text()

    assert "installPopoverStyles" not in controller
    assert "createElement('style')" not in controller
    assert ".template-suggestion.template-suggestion--popover" in styles
    assert ".template-picker-button--compact.template-picker-button--suggested" in styles
    assert '[data-template-suggestion-placement="viewport"]::before' in styles


def test_template_suggestion_controller_threshold_suppression_and_acceptance(tmp_path):
    runner = tmp_path / "template-suggestion-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/templateSuggestions.js").as_uri()
    runner.write_text(
        f"""
globalThis.window = {{
  setTimeout: (callback) => callback(),
  innerWidth: 1024,
  addEventListener() {{}}, removeEventListener() {{}},
}};
globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
const {{ createTemplateSuggestionController }} = await import('{module_uri}');

class Target {{
  constructor() {{
    this.listeners = new Map(); this.hidden = true;
    this.style = {{ setProperty() {{}}, removeProperty() {{}} }};
    this.dataset = {{}};
  }}
  addEventListener(type, callback) {{ this.listeners.set(type, callback); }}
  removeEventListener() {{}}
  fire(type) {{ this.listeners.get(type)?.(); }}
  getBoundingClientRect() {{ return {{ left: 0, top: 0, width: 320, height: 80 }}; }}
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
let enabled = true;
let posts = 0;
let failNextPost = false;
let chosen = null;
let disabledFetches = 0;
const disabledController = createTemplateSuggestionController({{
  transcriptText: text, templateSelect: select, suggestionRegion: region,
  suggestionMessage: message, useButton: use, dismissButton: dismiss,
  getTranscriptId: () => transcriptId, isEnabled: () => false, chooseTemplate: () => {{}},
  fetcher: async () => {{ disabledFetches += 1; throw new Error('disabled preference called fetch'); }},
}});
disabledController.attach();
await flush();
if (disabledFetches !== 0) throw new Error('disabled preference requested a suggestion');
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
  getTranscriptId: () => transcriptId, isEnabled: () => enabled,
  chooseTemplate: (id) => {{ chosen = id; select.value = id; select.fire('change'); }}, fetcher,
}});
controller.attach();
await flush();
if (posts !== 0) throw new Error('requested below threshold');
text.textContent += 'y';
await controller.requestIfEligible();
if (posts !== 1 || region.hidden || !message.textContent.includes('Mental health')) throw new Error('suggestion not shown');
enabled = false;
controller.onPreferenceChanged();
if (region.hidden) throw new Error('visible suggestion disappeared after preference was disabled');
enabled = true;
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
  getTranscriptId: () => transcriptId, isEnabled: () => true, chooseTemplate: () => {{}},
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


def test_template_suggestion_controller_hides_stale_popup_and_supports_escape(tmp_path):
    runner = tmp_path / "template-suggestion-popup-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/templateSuggestions.js").as_uri()
    runner.write_text(
        f"""
class EventTarget {{
  constructor() {{ this.listeners = new Map(); }}
  addEventListener(type, callback) {{ this.listeners.set(type, callback); }}
  removeEventListener(type) {{ this.listeners.delete(type); }}
  fire(type, event = {{}}) {{ this.listeners.get(type)?.(event); }}
}}
const documentTarget = new EventTarget();
globalThis.document = {{
  addEventListener: (...args) => documentTarget.addEventListener(...args),
  removeEventListener: (...args) => documentTarget.removeEventListener(...args),
  querySelector: () => picker,
  documentElement: {{ clientWidth: 1024 }},
  body: {{ appendChild() {{}} }},
}};
globalThis.window = {{
  setTimeout: (callback) => callback(),
  addEventListener() {{}}, removeEventListener() {{}},
}};
globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
const {{ createTemplateSuggestionController }} = await import('{module_uri}');

class Target extends EventTarget {{
  constructor() {{ super(); this.hidden = true; this.textContent = ''; this.value = 'one'; }}
  setAttribute() {{}}
}}
const text = {{ textContent: 'x'.repeat(1200) }};
let pickerIsVisible = true;
const picker = {{
  classList: {{ add() {{}}, remove() {{}} }},
  getBoundingClientRect: () => pickerIsVisible
    ? ({{ left: 180, top: 280, width: 180, height: 36 }})
    : ({{ left: 0, top: 0, width: 0, height: 0 }}),
}};
const select = new Target();
select.options = [
  {{ value: 'one', dataset: {{}}, textContent: 'General' }},
  {{ value: 'two', dataset: {{ templateName: 'Mental health' }}, textContent: 'Mental health' }},
];
const region = new Target();
region.querySelector = () => ({{}});
region.closest = () => null;
region.classList = {{ add() {{}}, remove() {{}} }};
region.setAttribute = () => {{}};
region.removeAttribute = () => {{}};
region.getBoundingClientRect = () => ({{ width: 320, height: 80 }});
region.style = {{ setProperty() {{}}, removeProperty() {{}} }};
region.dataset = {{}};
const message = new Target();
const use = new Target();
const dismiss = new Target();
let transcriptId = 'transcript-1';
const controller = createTemplateSuggestionController({{
  transcriptText: text, templateSelect: select, suggestionRegion: region,
  suggestionMessage: message, useButton: use, dismissButton: dismiss,
  getTranscriptId: () => transcriptId, isEnabled: () => true, chooseTemplate: () => {{}},
  fetcher: async () => ({{ ok: true, json: async () => ({{
    status: transcriptId === 'transcript-1' ? 'completed' : 'pending',
    suggestion: transcriptId === 'transcript-1'
      ? {{ template_id: 'two', template_name: 'Mental health', confidence: 'high' }} : null,
  }}) }}),
}});
controller.attach();
await new Promise((resolve) => setTimeout(resolve, 0));
if (region.hidden) throw new Error('initial popup did not show');

transcriptId = 'transcript-2';
controller.onTranscriptChanged();
if (!region.hidden) throw new Error('popup from prior transcript remained visible');

transcriptId = 'transcript-1';
pickerIsVisible = false;
const escapeController = createTemplateSuggestionController({{
  transcriptText: text, templateSelect: select, suggestionRegion: region,
  suggestionMessage: message, useButton: use, dismissButton: dismiss,
  getTranscriptId: () => transcriptId, isEnabled: () => true, chooseTemplate: () => {{}},
  fetcher: async () => ({{ ok: true, json: async () => ({{
    status: 'completed',
    suggestion: {{ template_id: 'two', template_name: 'Mental health', confidence: 'high' }},
  }}) }}),
}});
escapeController.attach();
await new Promise((resolve) => setTimeout(resolve, 0));
if (region.dataset.templateSuggestionPlacement !== 'viewport') throw new Error('hidden picker did not use viewport placement');
documentTarget.fire('keydown', {{ key: 'Escape', preventDefault() {{ this.prevented = true; }} }});
if (!region.hidden) throw new Error('Escape did not dismiss popup');
"""
    )
    env = {**os.environ, "NODE_NO_WARNINGS": "1"}
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env=env)
