from pathlib import Path
import subprocess
import textwrap


def _app_source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "app" / "static" / "js" / "transcribe" / "app.js").read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_structured_working_note_input_uses_incremental_content_state_for_generation_availability():
    source = _app_source()
    availability = _between(source, "      const syncGenerationAvailability = (draftText = '') => {", "\n\n      const isTranscriptWaitingForText")
    content_check = _between(source, "      const workingNoteHasContent = () => {", "\n\n      const isDiscardableEmptyWorkingNoteDraft")

    assert "const hasWorkingNote = workingNoteHasContent();" in availability
    assert "collectWorkingNote()" not in availability
    assert "workingNoteEditorContentTracker.hasContent()" in content_check
    assert "collectWorkingNote()" not in content_check


def test_working_note_input_tracks_only_the_changed_line_and_seeds_existing_line_on_focus():
    source = _app_source()

    assert "const trackWorkingNoteEditorLineContent" in source
    assert "const createWorkingNoteEditorContentTracker" in source
    assert "workingNoteEditorContentTracker.track(input);" in source
    assert "bindWorkingNoteEditorContentTracking(generatedStructuredPanel);" in source
    assert "bindWorkingNoteEditorContentTracking(generatedFreeformPanel);" in source


def test_freeform_working_note_content_count_tracks_each_nonblank_line(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "working_note_line_count_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const source = fs.readFileSync(__SOURCE_PATH__, 'utf8');
            const match = source.match(/const workingNoteContentLineCount = \\(workingNote\\) => \\{[\\s\\S]*?\\n      \\};/);
            assert.ok(match, 'working note content counter must remain executable as a standalone helper');
            const sandbox = { Array, Boolean, Object, String };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(`var ${match[0].slice('const '.length)}`, sandbox);

            const note = { mode: 'freeform', freeform_text: 'first line\\nsecond line\\nthird line' };
            assert.equal(sandbox.workingNoteContentLineCount(note), 3);
            note.freeform_text = 'first line\\n\\nthird line';
            assert.equal(sandbox.workingNoteContentLineCount(note), 2, 'clearing one row must leave other working-note rows available');
            note.freeform_text = '  \\n\\t  ';
            assert.equal(sandbox.workingNoteContentLineCount(note), 0, 'clearing the final row must empty the working note');
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app" / "static" / "js" / "transcribe" / "app.js"))),
        encoding="utf-8",
    )
    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_freeform_working_note_tracker_keeps_remaining_lines_available_after_an_edit(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "working_note_incremental_tracker_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const source = fs.readFileSync(__SOURCE_PATH__, 'utf8');
            const start = source.indexOf('const createWorkingNoteEditorContentTracker');
            const end = source.indexOf('\\n\\n      let workingNoteEditorContentTracker', start);
            assert.ok(start >= 0 && end > start, 'working note tracker must remain a self-contained behavioural seam');
            const sandbox = { Boolean, Math, Number, WeakMap };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(`var ${source.slice(start, end).trim().slice('const '.length)}`, sandbox);

            const tracker = sandbox.createWorkingNoteEditorContentTracker(3);
            const first = { value: 'first line' };
            const second = { value: 'second line' };
            const third = { value: 'third line' };
            [first, second, third].forEach((input) => tracker.seed(input));

            first.value = '';
            tracker.track(first);
            assert.equal(tracker.count(), 2);
            assert.equal(tracker.hasContent(), true, 'clearing one of three freeform rows must leave generation available');

            second.value = '';
            third.value = '';
            tracker.track(second);
            tracker.track(third);
            assert.equal(tracker.count(), 0);
            assert.equal(tracker.hasContent(), false, 'clearing the final freeform rows must empty the working note');
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app" / "static" / "js" / "transcribe" / "app.js"))),
        encoding="utf-8",
    )
    subprocess.run(["node", str(runner)], check=True, cwd=root)
