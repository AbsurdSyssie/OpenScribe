import subprocess
import textwrap
from pathlib import Path


def test_note_save_state_preserves_dirty_working_note_baseline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "note_save_state_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';

            const moduleUrl = __NOTE_SAVE_STATE_URL__;
            const { captureNoteDirtyBaseline, noteBaselineForSave } = await import(moduleUrl);

            const baseline = captureNoteDirtyBaseline({
              currentUpdatedAt: '2026-05-21T10:00:00+00:00',
              workingNoteUpdatedAt: '2026-05-21T09:00:00+00:00',
              isWorkingNote: true,
            });
            assert.equal(baseline, '2026-05-21T10:00:00+00:00');

            const preserved = noteBaselineForSave({
              targetId: 'working:transcript-1',
              noteEditorDirty: true,
              dirtyNoteTargetId: 'working:transcript-1',
              dirtyNoteExpectedUpdatedAt: baseline,
              currentUpdatedAt: '2026-05-21T10:05:00+00:00',
              workingNoteUpdatedAt: '2026-05-21T10:05:00+00:00',
              isWorkingNoteTarget: (targetId) => targetId.startsWith('working:'),
            });
            assert.equal(preserved, '2026-05-21T10:00:00+00:00');
            """
        ).replace("__NOTE_SAVE_STATE_URL__", repr((root / "app" / "static" / "js" / "transcribe" / "noteSaveState.js").as_uri())),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_note_save_state_preserves_empty_working_note_baseline_as_null(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "note_save_empty_baseline_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';

            const moduleUrl = __NOTE_SAVE_STATE_URL__;
            const { captureNoteDirtyBaseline, noteBaselineForSave } = await import(moduleUrl);

            const baseline = captureNoteDirtyBaseline({
              currentUpdatedAt: '',
              workingNoteUpdatedAt: '',
              isWorkingNote: true,
            });
            assert.equal(baseline, '');

            const preserved = noteBaselineForSave({
              targetId: 'working:transcript-1',
              noteEditorDirty: true,
              dirtyNoteTargetId: 'working:transcript-1',
              dirtyNoteExpectedUpdatedAt: baseline,
              currentUpdatedAt: '2026-05-21T10:05:00+00:00',
              workingNoteUpdatedAt: '2026-05-21T10:05:00+00:00',
              isWorkingNoteTarget: (targetId) => targetId.startsWith('working:'),
            });
            assert.equal(preserved, null);
            """
        ).replace("__NOTE_SAVE_STATE_URL__", repr((root / "app" / "static" / "js" / "transcribe" / "noteSaveState.js").as_uri())),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
