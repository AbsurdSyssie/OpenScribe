from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_diarised_turn_projection_and_shared_autosave_controller(tmp_path):
    autosave_source = (ROOT / "app/static/js/transcribe/autosaveController.js").read_text()
    editor_source = (ROOT / "app/static/js/transcribe/transcriptTurnsEditor.js").read_text()
    autosave_module = tmp_path / "autosaveController.mjs"
    editor_module = tmp_path / "transcriptTurnsEditor.mjs"
    autosave_module.write_text(autosave_source)
    editor_module.write_text(
        editor_source.replace("import { csrfFetch } from '../csrf.js';\n", "")
        .replace("import { createAutosaveController } from './autosaveController.js';\n", "")
    )
    autosave = autosave_module.as_uri()
    editor = editor_module.as_uri()
    runner = tmp_path / "diarised-editor-runner.mjs"
    runner.write_text(
        f"""
const {{ createAutosaveController }} = await import('{autosave}');
const {{ codepointToUtf16, projectStructuredTurns }} = await import('{editor}');
const turns = [
  {{ id: 'a', text: 'A😀B', start_index: 0, end_index: 3 }},
  {{ id: 'b', text: 'Second', start_index: 4, end_index: 10 }},
];
if (projectStructuredTurns(turns) !== 'A😀B\\nSecond') throw new Error('turn projection added speaker labels or changed text');
if (codepointToUtf16('A😀B', 2) !== 3) throw new Error('codepoint offset did not account for surrogate pairs');

let now = 0;
let nextId = 0;
const tasks = new Map();
const timers = {{
  setTimeout(callback, delay) {{ const id = ++nextId; tasks.set(id, {{ callback, due: now + delay }}); return id; }},
  clearTimeout(id) {{ tasks.delete(id); }},
}};
const advance = async (milliseconds) => {{
  now += milliseconds;
  for (const [id, task] of [...tasks]) if (task.due <= now) {{ tasks.delete(id); task.callback(); }}
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}};
let dirty = true;
let saves = 0;
const controller = createAutosaveController({{
  timers, delay: 700, isDirty: () => dirty,
  save: async () => {{ saves += 1; dirty = false; return {{ saved: true }}; }},
}});
controller.markDirty();
if (!controller.pending) throw new Error('dirty timer was not reported pending');
await advance(699);
if (saves !== 0) throw new Error('autosave ran before 700ms');
await advance(1);
if (saves !== 1) throw new Error('autosave did not run at 700ms');
if (controller.pending) throw new Error('completed save still reported pending');
controller.dispose();

let synchronousDirty = true;
let failures = 0;
const synchronousThrow = createAutosaveController({{
  timers, isDirty: () => synchronousDirty, save: () => {{ throw new Error('offline'); }},
  onFailure: () => {{ failures += 1; }},
}});
const result = await synchronousThrow.flush();
if (result !== null || failures !== 1 || !synchronousThrow.pending) throw new Error('synchronous save failure was lost');
synchronousThrow.dispose();
""",
        encoding="utf-8",
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT)


def test_telehealth_role_prefix_is_view_only_and_unicode_offsets_stay_canonical(tmp_path):
    autosave_source = (ROOT / "app/static/js/transcribe/autosaveController.js").read_text()
    editor_source = (ROOT / "app/static/js/transcribe/transcriptTurnsEditor.js").read_text()
    autosave_module = tmp_path / "autosaveController.mjs"
    editor_module = tmp_path / "transcriptTurnsEditor.mjs"
    autosave_module.write_text(autosave_source)
    editor_module.write_text(
        editor_source.replace("import { csrfFetch } from '../csrf.js';\n", "")
        .replace("import { createAutosaveController } from './autosaveController.js';", f"import {{ createAutosaveController }} from '{autosave_module.as_uri()}';")
    )
    runner = tmp_path / "role-prefix-editor-runner.mjs"
    runner.write_text(
        f"""
const {{ createTranscriptTurnsEditor }} = await import('{editor_module.as_uri()}');
class FakeTextArea {{
  constructor(value = '') {{ this.value = value; this.dataset = {{ turnText: 'legacy' }}; this.selection = null; }}
  matches() {{ return true; }}
  focus() {{}}
  setSelectionRange(start, end) {{ this.selection = [start, end]; }}
  scrollIntoView() {{}}
}}
globalThis.HTMLTextAreaElement = FakeTextArea;
globalThis.Element = class {{}};
globalThis.CSS = {{ escape: (value) => value }};
globalThis.document = {{ activeElement: null }};
globalThis.window = {{ setTimeout: (callback) => setTimeout(callback, 0), clearTimeout }};
const listeners = {{}};
const textarea = new FakeTextArea('😀 says hello');
const root = {{
  hidden: false, innerHTML: '', parentElement: {{ querySelector: () => null, addEventListener: () => {{}} }},
  addEventListener: (name, callback) => {{ listeners[name] = callback; }},
  contains: () => false,
  querySelector: () => textarea,
}};
let canonical = '';
let savedBody = null;
const prefix = 'Patient: ';
const body = '😀 says hello';
const raw = prefix + body;
const points = Array.from(raw);
const piiStart = Array.from(prefix + '😀 says ').length;
const editor = createTranscriptTurnsEditor({{
  root, getTranscriptId: () => 'synthetic', onCanonicalText: (text) => {{ canonical = text; }},
  fetcher: async (_url, options) => {{
    savedBody = JSON.parse(options.body);
    return {{ status: 200, ok: true, json: async () => ({{ transcript: {{
      structured_revision_id: 'rev-2', structured_editable: true,
      structured_turns: [{{ id: 'legacy', text: raw, speaker_label: 'Patient', speaker_text_prefix: prefix,
        start_index: 0, end_index: points.length }}],
    }} }}) }};
  }},
}});
editor.render({{ transcript_id: 'synthetic', revision_id: 'rev-1', editable: true,
  pii_revision_id: 'rev-1', entities: [{{ source: 'detected', value: 'hello', start_index: piiStart, end_index: piiStart + 5 }}],
  turns: [{{ id: 'legacy', text: raw, speaker_label: 'Patient', speaker_text_prefix: prefix,
    start_index: 0, end_index: points.length }}] }});
if (root.innerHTML.includes('Patient: 😀')) throw new Error('legacy prefix leaked into visible body');
if (!root.innerHTML.includes('data-speaker-tone="2"')) throw new Error('patient role tone is not deterministic');
if (!root.innerHTML.includes('<mark class="transcript-pii-highlight">hello</mark>')) throw new Error('PII highlight was shifted by hidden prefix');
if (!editor.jumpToOffset(9, 10) || textarea.selection?.[0] !== 0 || textarea.selection?.[1] !== 2) throw new Error('Unicode jump offset did not subtract hidden prefix');
textarea.value = '😀 says changed';
listeners.input({{ target: textarea }});
if (canonical !== raw.replace('hello', 'changed')) throw new Error('canonical text lost the legacy prefix after editing');
editor.render({{ transcript_id: 'synthetic', revision_id: 'rev-1', editable: true, masked: true,
  pii_revision_id: 'rev-1', turns: [{{ id: 'legacy', text: raw.replace('hello', 'changed'), speaker_label: 'Patient',
    speaker_text_prefix: prefix, start_index: 0, end_index: points.length }}] }});
if (root.innerHTML.includes('Patient:') || root.innerHTML.includes('Patient: ')) throw new Error('dirty masked view exposed the trusted prefix');
if (!root.innerHTML.includes('• •••• •••••••')) throw new Error('dirty masking did not cover the full canonical body');
await editor.flush();
if (savedBody?.text !== raw.replace('hello', 'changed')) throw new Error('saved edit did not preserve legacy prefix');

const spoken = {{ id: 'spoken', text: 'Patient: I said patient', speaker_label: 'Clinician' }};
const noPrefixEditorRoot = {{ ...root, innerHTML: '', addEventListener: () => {{}} }};
const noPrefixEditor = createTranscriptTurnsEditor({{ root: noPrefixEditorRoot, getTranscriptId: () => null, fetcher: async () => ({{}}) }});
noPrefixEditor.render({{ revision_id: 'rev', editable: true, turns: [spoken,
  {{ id: 'spoken-after-legacy-prefix', speaker_label: 'Patient', speaker_text_prefix: prefix,
    text: 'Patient: Patient: words', start_index: 0, end_index: 22 }}] }});
if (!noPrefixEditorRoot.innerHTML.includes('Patient: I said patient')) throw new Error('spoken words were stripped without trusted prefix metadata');
if (!noPrefixEditorRoot.innerHTML.includes('>Patient: words</button>')) throw new Error('speaker prefix hiding stripped spoken same-role words');
if (!noPrefixEditorRoot.innerHTML.includes('data-speaker-tone="1"')) throw new Error('clinician role tone is not deterministic');
""",
        encoding="utf-8",
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT)
