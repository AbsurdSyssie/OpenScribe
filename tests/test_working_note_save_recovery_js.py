import importlib.util
import shutil
import subprocess
import textwrap
from pathlib import Path
import pytest


def test_working_note_attempt_replays_exactly_once_and_keeps_uncertain_attempt(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "working_note_recovery_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';

            const { createWorkingNoteAttemptStore } = await import(__WORKING_NOTE_MODULE__);
            const store = createWorkingNoteAttemptStore();
            const attempt = store.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note',
              payload: { mode: 'freeform', freeform_text: 'synthetic note' },
              expectedRevision: 0,
              editVersion: 4,
              operationId: '00000000-0000-4000-8000-000000000001',
              targetId: 'working:t1', transcriptId: 't1',
            });
            const firstBody = attempt.body;
            let requests = 0;
            let committed = false;
            const result = await store.transmit(attempt, async (frozen) => {
              requests += 1;
              assert.equal(frozen.body, firstBody);
              assert.equal(frozen.payload.operation_id, attempt.operationId);
              if (requests === 1) {
                committed = true;
                throw new TypeError('connection lost after commit');
              }
              assert.equal(committed, true);
              return {
                ok: true, status: 200,
                json: async () => ({ revision: 1, mode: 'freeform', freeform_text: 'synthetic note' }),
              };
            });
            assert.equal(result.kind, 'acknowledged');
            assert.equal(result.result.revision, 1);
            assert.equal(requests, 2);
            store.acknowledge(attempt);
            assert.equal(store.pending, null);

            const decodeAttempt = store.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note', method: 'PATCH',
              payload: { mode: 'structured', structured_note: { profile: 'emis', sections: { problem: ['synthetic note'] } } },
              expectedRevision: 1, editVersion: 5,
              operationId: '00000000-0000-4000-8000-000000000002',
            });
            const bodies = [];
            let decodes = 0;
            const decoded = await store.transmit(decodeAttempt, async (frozen) => {
              bodies.push(frozen.body);
              decodes += 1;
              return {
                ok: true, status: 200,
                json: async () => {
                  if (decodes === 1) throw new SyntaxError('invalid JSON response');
                  return { revision: 2, mode: 'structured', structured_note: { profile: 'emis', sections: { problem: ['synthetic note'] } } };
                },
              };
            });
            assert.equal(decoded.kind, 'acknowledged');
            assert.equal(bodies[0], bodies[1]);
            store.acknowledge(decodeAttempt);
            assert.equal(store.pending, null);

            const clearStore = createWorkingNoteAttemptStore();
            const clearAttempt = clearStore.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note', method: 'DELETE',
              payload: { expected_updated_at: null }, expectedRevision: 2,
              operationId: '00000000-0000-4000-8000-000000000006',
              targetId: 'working:t1', transcriptId: 't1', editVersion: 8,
            });
            let clearCalls = 0;
            const cleared = await clearStore.transmit(clearAttempt, async (frozen) => {
              clearCalls += 1;
              assert.equal(frozen.body, clearAttempt.body);
              if (clearCalls === 1) throw new Error('delete response lost');
              return {
                ok: true, status: 204,
                headers: { get: (name) => name === 'X-Working-Note-Revision' ? '3' : null },
              };
            });
            assert.equal(cleared.kind, 'acknowledged');
            assert.equal(cleared.result.revision, 3);
            assert.equal(clearCalls, 2);

            const uncertainAttempt = store.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note',
              payload: { mode: 'freeform', freeform_text: 'later edit' },
              expectedRevision: 2, editVersion: 6,
              operationId: '00000000-0000-4000-8000-000000000003',
            });
            let boundedCalls = 0;
            const uncertain = await store.transmit(uncertainAttempt, async () => {
              boundedCalls += 1;
              throw new Error('offline');
            });
            assert.equal(uncertain.kind, 'uncertain');
            assert.equal(boundedCalls, 2, 'one immediate replay is the maximum');
            assert.equal(store.pending, uncertainAttempt, 'uncertain operation remains available for a later flush');
            const later = store.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note',
              payload: { mode: 'freeform', freeform_text: 'newer edit must wait' },
              expectedRevision: 2, editVersion: 7,
              operationId: '00000000-0000-4000-8000-000000000004',
            });
            assert.equal(later, uncertainAttempt, 'new edits cannot be attached to a frozen operation ID');
            assert.throws(() => store.prepare({
              endpoint: '/api/v1/transcripts/t2/working-note',
              payload: {}, expectedRevision: 0, operationId: '00000000-0000-4000-8000-000000000008',
              targetId: 'working:t2', transcriptId: 't2',
            }), /Resolve the pending Working note operation/);

            const mismatchStore = createWorkingNoteAttemptStore();
            const mismatchAttempt = mismatchStore.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note', payload: {}, expectedRevision: 4,
              operationId: '00000000-0000-4000-8000-000000000007',
            });
            let mismatchCalls = 0;
            const mismatch = await mismatchStore.transmit(mismatchAttempt, async () => {
              mismatchCalls += 1;
              return { ok: true, status: 200, json: async () => ({ revision: 6 }) };
            });
            assert.equal(mismatch.kind, 'uncertain');
            assert.equal(mismatchCalls, 2, 'an unrelated server revision is never adopted');
            assert.equal(mismatchStore.pending, mismatchAttempt);

            const conflictStore = createWorkingNoteAttemptStore();
            const conflictAttempt = conflictStore.prepare({
              endpoint: '/api/v1/transcripts/t1/working-note', payload: {}, expectedRevision: 2,
              operationId: '00000000-0000-4000-8000-000000000005',
            });
            let conflicts = 0;
            const conflict = await conflictStore.transmit(conflictAttempt, async () => {
              conflicts += 1;
              return { ok: false, status: 409 };
            });
            assert.equal(conflict.kind, 'rejected');
            assert.equal(conflicts, 1, 'conflicts are never retried automatically');
            """
        ).replace(
            "__WORKING_NOTE_MODULE__",
            repr((root / "app" / "static" / "js" / "transcribe" / "workingNoteSaveRecovery.js").as_uri()),
        ),
        encoding="utf-8",
    )
    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_actual_app_clear_recovery_and_later_edits():
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for the frontend behavior harness")
    root = Path(__file__).resolve().parents[1]
    harness_path = root / "tests/test_dictation_modal_actions.py"
    spec = importlib.util.spec_from_file_location("openscribe_dictation_harness", harness_path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    harness.run_scenario("working-note-clear-retry")
    harness.run_scenario("working-note-clear-late-target")
    harness.run_scenario("working-note-clear-conflict")
    harness.run_scenario("working-note-save-retry")
