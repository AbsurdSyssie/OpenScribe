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
