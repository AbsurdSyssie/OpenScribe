import contextlib
import functools
import http.server
import threading
from pathlib import Path

import pytest


playwright_sync = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


class _QuietStaticHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *args):
        return


@pytest.fixture
def static_repo_server():
    handler = functools.partial(_QuietStaticHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextlib.contextmanager
def _browser_page(base_url):
    try:
        playwright = playwright_sync.sync_playwright().start()
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Playwright browser unavailable: {exc}")
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base_url)
        yield page
    finally:
        browser.close()
        playwright.stop()


def _install_turn_editor(page, base_url, *, turns=None, editable=True):
    return page.evaluate(
        """async ({ moduleUrl, turns, editable }) => {
          document.head.innerHTML = `<link rel="stylesheet" href="${location.origin}/app/static/css/transcribe-tailwind.css"><link rel="stylesheet" href="${location.origin}/app/static/css/tokens.css"><link rel="stylesheet" href="${location.origin}/app/static/css/components.css"><link rel="stylesheet" href="${location.origin}/app/static/css/transcribe.css">`;
          document.body.innerHTML = `
            <main><div class="transcript-review-grid"><section class="transcript-review-grid__transcript">
              <div class="transcript-content flex-1 min-h-0 overflow-y-auto" data-active-draft data-turn-editor></div>
              <div data-turn-save-status hidden role="status" aria-live="polite">
                <span data-turn-save-message></span><button type="button" data-turn-retry hidden>Retry</button>
              </div>
              <section class="dictation-compact"><div class="dictation-compact__header">Post-consultation dictation</div><div class="dictation-compact__body">No post-consultation dictation yet.</div></section>
            </section><aside class="pii-sidebar">Transcript review</aside></div>
            </main>`;
          const clone = (value) => JSON.parse(JSON.stringify(value));
          const state = {
            revision: 'rev-1', turns: clone(turns), editable,
            pending: null, requests: [],
          };
          const detail = () => ({
            structured_revision_id: state.revision,
            structured_turns: clone(state.turns),
            structured_editable: state.editable,
          });
          const response = (body, status = 200) => new Response(JSON.stringify(body), {
            status, headers: { 'Content-Type': 'application/json' },
          });
          const refreshOffsets = () => {
            let offset = 0;
            state.turns.forEach((turn, index) => {
              turn.start_index = offset;
              offset += [...turn.text].length;
              turn.end_index = offset;
              if (index < state.turns.length - 1) offset += 1;
            });
          };
          window.fetch = async (url, init = {}) => {
            const path = String(url);
            const method = String(init.method || 'GET').toUpperCase();
            const body = init.body ? JSON.parse(init.body) : {};
            state.requests.push({ path, method, body });
            if (state.pending) await state.pending;
            if (body.expected_version_id && body.expected_version_id !== state.revision) return response({ detail: 'conflict' }, 409);
            const match = path.match(new RegExp('/turns(?:/([^/]+))?$'));
            if (!match) return response(detail(), 404);
            const turnId = match[1] ? decodeURIComponent(match[1]) : null;
            if (method === 'PATCH') {
              const turn = state.turns.find((candidate) => candidate.id === turnId);
              if (turn) { turn.text = body.text; turn.edited = true; }
            } else if (method === 'DELETE') {
              state.turns = state.turns.filter((candidate) => candidate.id !== turnId);
            } else if (method === 'POST') {
              const previous = body.after_turn_id ? state.turns.find((candidate) => candidate.id === body.after_turn_id) : state.turns.at(-1);
              const index = previous ? state.turns.indexOf(previous) + 1 : state.turns.length;
              state.turns.splice(index, 0, {
                id: `turn-${state.requests.length}`, scope_id: previous?.scope_id || 'scope-1',
                speaker_id: null, speaker_label: '?', start_ms: null, end_ms: null,
                text: body.text, edited: false, start_index: 0, end_index: [...body.text].length,
              });
            }
            refreshOffsets();
            state.revision = `rev-${Number(state.revision.split('-')[1]) + 1}`;
            return response(detail());
          };
          const { createTranscriptTurnsEditor } = await import(moduleUrl);
          const root = document.querySelector('[data-turn-editor]');
          const editor = createTranscriptTurnsEditor({ root, getTranscriptId: () => 'transcript-1' });
          window.turnTest = { editor, state, root, detail };
          editor.render({ transcript_id: 'transcript-1', revision_id: state.revision, turns: state.turns, editable: state.editable });
          return true;
        }""",
        {
            "moduleUrl": f"{base_url}/app/static/js/transcribe/transcriptTurnsEditor.js",
            "turns": turns or [
                {"id": "turn-1", "scope_id": "scope-1", "speaker_id": "1", "speaker_label": "Speaker 1", "start_ms": 1000, "end_ms": 2500, "text": "Patient text", "edited": False, "start_index": 0, "end_index": 12},
                {"id": "turn-2", "scope_id": "scope-1", "speaker_id": "2", "speaker_label": "Speaker 2", "start_ms": 3000, "end_ms": 4000, "text": "Clinician text", "edited": False, "start_index": 13, "end_index": 27},
            ],
            "editable": editable,
        },
    )


def test_masked_turns_never_expose_plaintext_textarea(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        page.evaluate("""() => window.turnTest.editor.render({
          transcript_id: 'transcript-1', revision_id: 'rev-1', turns: window.turnTest.state.turns,
          editable: true, masked: true,
          entities: [{ source: 'detected', start_index: 0, end_index: 7, value: 'Patient' }],
        })""")
        assert page.locator('[data-turn-text]').count() == 0
        visible = page.locator('[data-turn-id="turn-1"]').inner_text()
        assert 'Patient' not in visible
        assert '•••••••' in visible


def test_inline_edit_is_one_turn_and_poll_preserves_focus_caret(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        page.locator('[data-turn-display="turn-1"]').click()
        textarea = page.locator('[data-turn-text="turn-1"]')
        assert textarea.input_value() == 'Patient text'
        textarea.evaluate("node => { node.focus(); node.setSelectionRange(3, 3); }")
        page.evaluate("""() => window.turnTest.editor.render({
          transcript_id: 'transcript-1', revision_id: 'rev-1', turns: window.turnTest.state.turns, editable: true,
        })""")
        assert textarea.evaluate("node => document.activeElement === node")
        assert textarea.evaluate("node => node.selectionStart") == 3
        textarea.fill('Edited patient text')
        assert page.evaluate("() => window.turnTest.editor.getCanonicalText()") == 'Edited patient text\nClinician text'
        assert page.locator('[data-turn-text="turn-2"]').count() == 0
        page.wait_for_timeout(800)
        assert textarea.evaluate("node => document.activeElement === node")
        assert textarea.input_value() == 'Edited patient text'
        assert page.evaluate("() => window.turnTest.state.requests.some(item => item.method === 'PATCH')")


def test_remote_revision_conflict_keeps_dirty_orphan_without_saving(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        page.locator('[data-turn-display="turn-1"]').click()
        page.locator('[data-turn-text="turn-1"]').fill('local unsaved edit')
        page.evaluate("""() => window.turnTest.editor.render({
          transcript_id: 'transcript-1', revision_id: 'remote-revision', turns: [], editable: true,
        })""")
        page.wait_for_timeout(800)
        assert page.locator('[data-turn-text="turn-1"]').input_value() == 'local unsaved edit'
        assert page.evaluate("() => window.turnTest.state.requests.length") == 0
        assert 'changed elsewhere' in page.locator('[data-turn-save-status]').inner_text()


def test_late_poll_cannot_restore_a_revision_superseded_by_our_save(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        page.evaluate("() => { window.oldTurns = JSON.parse(JSON.stringify(window.turnTest.state.turns)); }")
        page.locator('[data-turn-display="turn-1"]').click()
        textarea = page.locator('[data-turn-text="turn-1"]')
        textarea.fill('First confirmed correction')
        page.evaluate("() => window.turnTest.editor.flush()")
        page.evaluate("""() => window.turnTest.editor.render({
          transcript_id: 'transcript-1', revision_id: 'rev-1', turns: window.oldTurns, editable: true,
        })""")
        assert textarea.input_value() == 'First confirmed correction'
        textarea.fill('Second correction')
        page.evaluate("() => window.turnTest.editor.flush()")
        assert page.evaluate("() => window.turnTest.state.turns[0].text") == 'Second correction'
        assert page.evaluate("() => window.turnTest.state.requests.at(-1).body.expected_version_id") == 'rev-2'


def test_mask_waits_for_pii_from_the_same_saved_revision(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        page.evaluate("""() => window.turnTest.editor.render({
          transcript_id: 'transcript-1', revision_id: 'rev-1', turns: window.turnTest.state.turns,
          editable: true, masked: true, pii_revision_id: null, entities: [],
        })""")
        assert page.locator('[data-turn-text]').count() == 0
        assert 'Patient' not in page.locator('[data-turn-editor]').inner_text()
        assert 'Clinician' not in page.locator('[data-turn-editor]').inner_text()


def test_deleting_final_turn_keeps_add_and_creates_empty_unattributed_turn(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server, turns=[
            {"id": "only", "scope_id": "scope-1", "speaker_id": "1", "speaker_label": "Speaker 1", "start_ms": 0, "end_ms": 1000, "text": "Only turn", "edited": False, "start_index": 0, "end_index": 9},
        ])
        page.locator('.transcript-turn__actions summary').click()
        page.locator('[data-turn-delete="only"]').click()
        page.wait_for_function("() => window.turnTest.state.requests.length === 1")
        page.wait_for_function("() => document.querySelector('[data-turn-add]') !== null")
        assert page.locator('[data-turn-add]').is_visible()
        page.locator('[data-turn-add]').click()
        page.wait_for_function("() => window.turnTest.state.requests.length === 2")
        page.wait_for_function("() => document.querySelector('[data-turn-text]') !== null")
        added = page.locator('[data-turn-text]')
        assert added.count() == 1
        assert added.input_value() == ''
        assert page.evaluate("() => window.turnTest.state.requests.map(item => item.method)") == ['DELETE', 'POST']


def test_compact_turn_rows_use_stable_speaker_tints_and_neutral_fallback(static_repo_server):
    turns = [
        {"id": f"turn-{index}", "scope_id": "scope-1", "speaker_id": str(index), "speaker_label": f"Speaker {index}",
         "start_ms": index * 1000, "end_ms": (index + 1) * 1000,
         "text": "A short first line.\nA second line for wrapping." if index == 1 else f"Turn {index}.",
         "edited": False, "start_index": 0, "end_index": 10}
        for index in (1, 2, 3, 4, 5)
    ]
    turns.extend([
        {"id": "turn-1-again", "scope_id": "scope-1", "speaker_id": "1", "speaker_label": "Speaker 1",
         "start_ms": 6000, "end_ms": 7000, "text": "Speaker one again.", "edited": False, "start_index": 0, "end_index": 10},
        {"id": "unattributed", "scope_id": "scope-1", "speaker_id": None, "speaker_label": "?",
         "start_ms": None, "end_ms": None, "text": "Unattributed line.", "edited": False, "start_index": 0, "end_index": 10},
        {"id": "new-scope-speaker", "scope_id": "scope-2", "speaker_id": "1", "speaker_label": "Speaker 1",
         "start_ms": 0, "end_ms": 500, "text": "New recording.", "edited": False, "start_index": 0, "end_index": 14},
    ])
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server, turns=turns)
        gap_after = page.locator('[data-active-draft]').evaluate("node => ({gap: node.querySelector('.transcript-turns-editor').getBoundingClientRect().top - node.getBoundingClientRect().top, whiteSpace: getComputedStyle(node).whiteSpace, paddingTop: getComputedStyle(node).paddingTop})")
        page.screenshot(path="/tmp/openscribe-transcript-gap-after.png", full_page=True)
        assert gap_after["gap"] < 6, f"unexpected blank space above structured transcript surface: {gap_after}"
        assert page.locator('.transcript-turn-scope__heading').all_text_contents() == [
            'Recording 1', 'Recording 2 · speakers not matched to previous recording',
        ]
        tones = page.locator('.transcript-turn').evaluate_all("nodes => nodes.map(node => node.dataset.speakerTone)")
        assert tones == ['1', '2', '3', '4', 'neutral', '1', 'neutral', '1']
        measurements = page.locator('[data-turn-id="turn-1"]').evaluate("node => ({height: node.getBoundingClientRect().height, meta: node.querySelector('.transcript-turn__meta').getBoundingClientRect().height, lineHeight: getComputedStyle(node.querySelector('.transcript-turn__display')).lineHeight, whitespace: getComputedStyle(node).whiteSpace})")
        assert measurements["height"] < 90
        assert measurements["meta"] < 30
        assert measurements["lineHeight"] not in ("normal", "")
        assert measurements["whitespace"] == "normal"
        colors = page.locator('.transcript-turn').evaluate_all("nodes => [...new Set(nodes.filter(node => node.dataset.speakerTone !== 'neutral').map(node => getComputedStyle(node).backgroundColor))]")
        assert len(colors) == 4
        first_color = page.locator('[data-turn-id="turn-1"]').evaluate("node => getComputedStyle(node).backgroundColor")
        repeated_color = page.locator('[data-turn-id="turn-1-again"]').evaluate("node => getComputedStyle(node).backgroundColor")
        assert first_color == repeated_color
        assert page.locator('[data-turn-id="new-scope-speaker"]').evaluate("node => getComputedStyle(node).backgroundColor") == first_color
        page.locator('[data-turn-id="turn-1"] .transcript-turn__actions summary').click()
        page.locator('[data-turn-delete="turn-1"]').click()
        page.wait_for_function("() => window.turnTest.state.turns.every(turn => turn.id !== 'turn-1')")
        assert page.locator('[data-turn-id="turn-1-again"]').get_attribute('data-speaker-tone') == '1'
        assert page.locator('[data-turn-id="new-scope-speaker"]').get_attribute('data-speaker-tone') == '1'
        page.set_viewport_size({"width": 420, "height": 800})
        assert page.locator('body').evaluate("node => node.scrollWidth <= node.clientWidth")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path="/tmp/openscribe-diarised-compact-rows.png", full_page=True)


def test_single_recording_hides_scope_heading_and_keeps_compact_top_gap(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        assert page.locator('.transcript-turn-scope__heading').count() == 0
        gap = page.locator('[data-active-draft]').evaluate("node => node.querySelector('.transcript-turn').getBoundingClientRect().top - node.getBoundingClientRect().top")
        assert gap < 8


def test_input_during_pending_save_is_retained_and_flush_drains(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_turn_editor(page, static_repo_server)
        page.locator('[data-turn-display="turn-1"]').click()
        textarea = page.locator('[data-turn-text="turn-1"]')
        textarea.fill('first version')
        page.evaluate("""() => { window.releaseSave = null; window.turnTest.state.pending = new Promise(resolve => { window.releaseSave = resolve; }); }""")
        page.wait_for_timeout(750)
        page.wait_for_function("() => window.turnTest.state.requests.some(item => item.method === 'PATCH')")
        textarea.fill('newer version while saving')
        page.evaluate("() => window.releaseSave()")
        page.wait_for_timeout(50)
        assert textarea.input_value() == 'newer version while saving'
        assert page.evaluate("() => window.turnTest.editor.dirty")
        flushed = page.evaluate("() => window.turnTest.editor.flush().then(Boolean)")
        assert flushed
        assert page.evaluate("() => !window.turnTest.editor.dirty")
        assert page.evaluate("() => window.turnTest.state.turns.find(turn => turn.id === 'turn-1').text") == 'newer version while saving'


def test_transcript_flush_guards_workspace_and_generation_transitions():
    source = (ROOT / "app/static/js/transcribe/app.js").read_text()
    switch_start = source.index("const persistPendingEditorsBeforeWorkspaceSwitch")
    switch_end = source.index("function scheduleNoteAutosave", switch_start)
    switch_guard = source[switch_start:switch_end]
    assert "transcriptTurnsEditor.flush({ keepalive: false })" in switch_guard
    assert "if (!savedTranscript && transcriptTurnsEditor.dirty) return false" in switch_guard
    generation_start = source.index("const saveWorkingNoteBeforeGeneration")
    generation_end = source.index("const enqueueTemplateGeneration", generation_start)
    generation_guard = source[generation_start:generation_end]
    assert "transcriptTurnsEditor.flush({ keepalive: false })" in generation_guard
    assert "throw new Error('Save the transcript edits before generating.')" in generation_guard


def test_microphone_batch_capture_token_is_frozen_and_bound_to_every_upload():
    media = (ROOT / "app/static/js/transcribe/media.js").read_text()
    app = (ROOT / "app/static/js/transcribe/app.js").read_text()
    settings_request = media.index("/capture-settings")
    vad_start = media.index("batchVadInstance.start()", settings_request)
    assert settings_request < vad_start
    assert "formData.append('capture_settings_id', captureSettingsId)" in media
    assert "captureSettingsId: batchCaptureSettings?.captureSettingsId || null" in media
    assert "captureSettingsId = batchCaptureSettings?.captureSettingsId || null" in media
    assert "captureSettingsId });" in media
    assert "event.detail?.diarisation_enabled" in app
    assert "diarizationEnabled: captureSettings.diarisation_enabled" in media
