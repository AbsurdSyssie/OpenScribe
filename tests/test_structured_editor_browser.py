import contextlib
import functools
import http.server
import threading
from pathlib import Path

import pytest


playwright_sync = pytest.importorskip("playwright.sync_api")


class _QuietStaticHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *args):
        return


@pytest.fixture
def static_repo_server():
    root = Path(__file__).resolve().parents[1]
    handler = functools.partial(_QuietStaticHandler, directory=str(root))
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


def _install_editor(page, base_url, *, lines_per_section=1):
    return page.evaluate(
        """async ({ moduleUrl, linesPerSection }) => {
          const observers = [];
          class ControlledIntersectionObserver {
            constructor(callback) {
              this.callback = callback;
              this.targets = new Set();
              observers.push(this);
            }
            observe(target) { this.targets.add(target); }
            unobserve(target) { this.targets.delete(target); }
            disconnect() { this.targets.clear(); }
            trigger(targets = [...this.targets]) {
              this.callback(targets.map((target) => ({ target, isIntersecting: true })));
            }
          }
          window.IntersectionObserver = ControlledIntersectionObserver;
          document.body.innerHTML = `
            <select data-template><option data-template-mode="structured" selected>Structured</option></select>
            <div data-latest data-latest-generated-id="doc-1" data-latest-generated-mode="structured"></div>
            <div data-structured-panel hidden><div data-structured-sections></div></div>
            <div data-freeform-panel hidden><div data-freeform-rows></div></div>
            <button data-copy-all></button><div data-toolbar></div><div data-status></div><div data-badge></div>`;
          const dom = {
            generateOutputTemplateSelect: document.querySelector('[data-template]'),
            generatedStructuredPanel: document.querySelector('[data-structured-panel]'),
            generatedStructuredSections: document.querySelector('[data-structured-sections]'),
            generatedFreeformPanel: document.querySelector('[data-freeform-panel]'),
            generatedFreeformRows: document.querySelector('[data-freeform-rows]'),
            copyStructuredLinesButton: document.querySelector('[data-copy-all]'),
            latestGeneratedOutput: document.querySelector('[data-latest]'),
            noteEditorToolbar: document.querySelector('[data-toolbar]'),
            structuredCopyStatus: document.querySelector('[data-status]'),
            templateModeBadge: document.querySelector('[data-badge]'),
          };
          const { createStructuredEditor } = await import(moduleUrl);
          const editor = createStructuredEditor({
            dom,
            structuredSectionDefinitions: [],
            getTranscriptId: () => 'transcript-1',
            getDraftText: () => '',
            getTranscriptWaitingForText: () => false,
            syncGenerationAvailability: () => {},
            onNoteEditorChanged: () => {},
          });
          const makeLines = (prefix) => Array.from(
            { length: linesPerSection },
            (_, index) => `${prefix} ${index} with enough text to edit`
          ).join('\\n');
          editor.renderGeneratedOutput({
            id: 'doc-1',
            status: 'ready',
            kind: 'generated_note',
            document_mode: 'structured',
            structured_section_definitions_json: { sections: [
              { section_key: 'history', section_label: 'History', section_order: 0 },
              { section_key: 'examination', section_label: 'Examination', section_order: 1 },
            ] },
            sections: [
              { section_key: 'history', edited_text: makeLines('History') },
              { section_key: 'examination', edited_text: makeLines('Examination') },
            ],
          });
          window.testEditor = editor;
          window.testObservers = observers;
          return true;
        }""",
        {
            "moduleUrl": f"{base_url}/app/static/js/transcribe/structured.js",
            "linesPerSection": lines_per_section,
        },
    )


def _install_freeform_editor(page, base_url, *, line_count=376):
    return page.evaluate(
        """async ({ moduleUrl, lineCount }) => {
          window.IntersectionObserver = class {
            observe() {}
            unobserve() {}
            disconnect() {}
          };
          document.body.innerHTML = `
            <select data-template><option data-template-mode="freeform" selected>Freeform</option></select>
            <div data-latest data-latest-generated-id="doc-1" data-latest-generated-mode="freeform"></div>
            <div data-structured-panel hidden><div data-structured-sections></div></div>
            <div data-freeform-panel hidden><div data-freeform-rows></div></div>
            <button data-copy-all></button><div data-toolbar></div><div data-status></div><div data-badge></div>`;
          const dom = {
            generateOutputTemplateSelect: document.querySelector('[data-template]'),
            generatedStructuredPanel: document.querySelector('[data-structured-panel]'),
            generatedStructuredSections: document.querySelector('[data-structured-sections]'),
            generatedFreeformPanel: document.querySelector('[data-freeform-panel]'),
            generatedFreeformRows: document.querySelector('[data-freeform-rows]'),
            copyStructuredLinesButton: document.querySelector('[data-copy-all]'),
            latestGeneratedOutput: document.querySelector('[data-latest]'),
            noteEditorToolbar: document.querySelector('[data-toolbar]'),
            structuredCopyStatus: document.querySelector('[data-status]'),
            templateModeBadge: document.querySelector('[data-badge]'),
          };
          const originalAddEventListener = EventTarget.prototype.addEventListener;
          let statementControlListenerAdds = 0;
          EventTarget.prototype.addEventListener = function(type, listener, options) {
            if (this instanceof HTMLElement && this.matches(
              '[data-freeform-note-row], [data-freeform-note-input], [data-freeform-note-checkbox]'
            )) {
              statementControlListenerAdds += 1;
            }
            return originalAddEventListener.call(this, type, listener, options);
          };
          try {
            const { createStructuredEditor } = await import(moduleUrl);
            const editor = createStructuredEditor({
              dom,
              structuredSectionDefinitions: [],
              getTranscriptId: () => 'transcript-1',
              getDraftText: () => '',
              getTranscriptWaitingForText: () => false,
              syncGenerationAvailability: () => {},
              onNoteEditorChanged: () => {},
            });
            editor.renderGeneratedOutput({
              id: 'doc-1',
              status: 'ready',
              kind: 'generated_note',
              document_mode: 'freeform',
              edited_output_text: Array.from(
                { length: lineCount },
                (_, index) => `Freeform line ${index}`
              ).join('\\n'),
            });
            window.testEditor = editor;
            window.testStatementControlListenerAdds = statementControlListenerAdds;
          } finally {
            EventTarget.prototype.addEventListener = originalAddEventListener;
          }
          return true;
        }""",
        {
            "moduleUrl": f"{base_url}/app/static/js/transcribe/structured.js",
            "lineCount": line_count,
        },
    )


def test_editing_a_moved_row_relocks_its_destination_section(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_editor(page, static_repo_server)
        page.wait_for_timeout(50)

        result = page.evaluate(
            """async () => {
              const sections = [...document.querySelectorAll('[data-generated-structured-section]')];
              const sourceRows = sections[0].querySelector('[data-generated-structured-section-rows]');
              const destinationRows = sections[1].querySelector('[data-generated-structured-section-rows]');
              const movedRow = sourceRows.querySelector('[data-structured-statement-row]');
              destinationRows.appendChild(movedRow);
              window.testEditor.notifyNoteRowsChanged({ mode: 'structured' });
              await new Promise((resolve) => requestAnimationFrame(resolve));
              await new Promise((resolve) => requestAnimationFrame(resolve));

              window.testObservers.forEach((observer) => observer.trigger());
              const beforeEdit = window.testEditor.noteCopyReviewBlocker({ section: sections[1] });
              const input = movedRow.querySelector('[data-structured-line-input]');
              input.value += ' changed';
              input.dispatchEvent(new Event('input', { bubbles: true }));
              const afterEdit = window.testEditor.noteCopyReviewBlocker({ section: sections[1] });
              return { beforeEdit, afterEdit };
            }"""
        )

    assert result["beforeEdit"] is None
    assert "Scroll to the bottom" in result["afterEdit"]


def test_copy_review_unlocks_when_final_structured_row_reaches_viewport(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_editor(page, static_repo_server)
        page.wait_for_timeout(50)

        result = page.evaluate(
            """async () => {
              const section = document.querySelector('[data-generated-structured-section]');
              const finalRow = section.querySelector('[data-structured-statement-row]:last-child');
              const input = finalRow.querySelector('[data-structured-line-input]');
              const sectionRect = {
                bottom: 930, height: 930, left: 0, right: 500, top: 0, width: 500,
              };
              const rowRect = {
                bottom: 899, height: 40, left: 0, right: 500, top: 859, width: 500,
              };
              section.getBoundingClientRect = () => sectionRect;
              section.getClientRects = () => [sectionRect];
              finalRow.getBoundingClientRect = () => rowRect;
              finalRow.getClientRects = () => [rowRect];

              input.value += ' changed';
              input.dispatchEvent(new Event('input', { bubbles: true }));
              await new Promise((resolve) => requestAnimationFrame(resolve));
              await new Promise((resolve) => requestAnimationFrame(resolve));

              const copyObserver = window.testObservers.find((observer) => observer.targets.has(section));
              copyObserver.trigger([section]);
              return {
                blocker: window.testEditor.noteCopyReviewBlocker({ section }),
                sectionBottom: sectionRect.bottom,
                finalRowBottom: rowRect.bottom,
                viewportHeight: window.innerHeight,
              };
            }"""
        )

    assert result["sectionBottom"] > result["viewportHeight"]
    assert result["finalRowBottom"] <= result["viewportHeight"]
    assert result["blocker"] is None


def test_copy_review_uses_the_clipping_scroll_container_bottom(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_editor(page, static_repo_server)
        page.wait_for_timeout(50)

        result = page.evaluate(
            """async () => {
              const section = document.querySelector('[data-generated-structured-section]');
              const finalRow = section.querySelector('[data-structured-statement-row]:last-child');
              const input = finalRow.querySelector('[data-structured-line-input]');
              const scrollContainer = document.createElement('div');
              scrollContainer.style.overflowY = 'auto';
              section.parentElement.insertBefore(scrollContainer, section);
              scrollContainer.appendChild(section);
              Object.defineProperties(scrollContainer, {
                clientHeight: { configurable: true, value: 600 },
                clientTop: { configurable: true, value: 0 },
              });
              const scrollRect = {
                bottom: 700, height: 600, left: 0, right: 500, top: 100, width: 500,
              };
              const sectionRect = {
                bottom: 850, height: 850, left: 0, right: 500, top: 0, width: 500,
              };
              let finalRowBottom = 750;
              scrollContainer.getBoundingClientRect = () => scrollRect;
              section.getBoundingClientRect = () => sectionRect;
              section.getClientRects = () => [sectionRect];
              finalRow.getBoundingClientRect = () => ({
                bottom: finalRowBottom,
                height: 40,
                left: 0,
                right: 500,
                top: finalRowBottom - 40,
                width: 500,
              });
              finalRow.getClientRects = () => [finalRow.getBoundingClientRect()];

              input.value += ' changed';
              input.dispatchEvent(new Event('input', { bubbles: true }));
              await new Promise((resolve) => requestAnimationFrame(resolve));
              await new Promise((resolve) => requestAnimationFrame(resolve));

              const copyObserver = window.testObservers.find((observer) => observer.targets.has(section));
              copyObserver.trigger([section]);
              const beforeRowReachesContainerBottom = window.testEditor.noteCopyReviewBlocker({ section });
              finalRowBottom = 700;
              copyObserver.trigger([section]);
              return {
                beforeRowReachesContainerBottom,
                afterRowReachesContainerBottom: window.testEditor.noteCopyReviewBlocker({ section }),
                finalRowBottom,
                scrollContainerBottom: scrollRect.bottom,
                viewportHeight: window.innerHeight,
              };
            }"""
        )

    assert result["scrollContainerBottom"] < result["viewportHeight"]
    assert "Scroll to the bottom" in result["beforeRowReachesContainerBottom"]
    assert result["afterRowReachesContainerBottom"] is None


def test_copy_stays_locked_until_deferred_rows_are_laid_out(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_editor(page, static_repo_server, lines_per_section=81)
        page.wait_for_function(
            """() => window.testObservers?.some((observer) =>
              [...observer.targets].some((target) => target instanceof HTMLTextAreaElement)
            )"""
        )
        page.wait_for_function(
            """() => {
              const sections = [...document.querySelectorAll('[data-generated-structured-section]')];
              const section = sections[1];
              return window.testObservers?.some((observer) => observer.targets.has(section));
            }"""
        )

        result = page.evaluate(
            """() => {
              const sections = [...document.querySelectorAll('[data-generated-structured-section]')];
              const section = sections[1];
              const finalRow = section.querySelector('[data-structured-statement-row]:last-child');
              section.getBoundingClientRect = () => ({
                bottom: 100, height: 100, left: 0, right: 500, top: 0, width: 500,
              });
              finalRow.getBoundingClientRect = () => ({
                bottom: 100, height: 40, left: 0, right: 500, top: 60, width: 500,
              });
              finalRow.getClientRects = () => [finalRow.getBoundingClientRect()];
              const autosizeObserver = window.testObservers.find((observer) =>
                [...observer.targets].some((target) => target instanceof HTMLTextAreaElement)
              );
              const copyObserver = window.testObservers.find((observer) => observer.targets.has(section));
              const deferredInSection = [...autosizeObserver.targets].filter((target) => section.contains(target));

              copyObserver.trigger([section]);
              const whileDeferred = window.testEditor.noteCopyReviewBlocker({ section });
              autosizeObserver.trigger(deferredInSection);
              copyObserver.trigger([section]);
              const whileEarlierSectionDeferred = window.testEditor.noteCopyReviewBlocker({ section });
              autosizeObserver.trigger();
              copyObserver.trigger([section]);
              const afterLayout = window.testEditor.noteCopyReviewBlocker({ section });
              return { whileDeferred, whileEarlierSectionDeferred, afterLayout, deferredCount: deferredInSection.length };
            }"""
        )

    assert result["deferredCount"] > 0
    assert "Scroll to the bottom" in result["whileDeferred"]
    assert "Scroll to the bottom" in result["whileEarlierSectionDeferred"]
    assert result["afterLayout"] is None


def test_long_freeform_note_keeps_row_editing_without_per_row_event_bindings(static_repo_server):
    with _browser_page(static_repo_server) as page:
        _install_freeform_editor(page, static_repo_server)
        page.wait_for_timeout(50)

        result = page.evaluate(
            """() => {
              const rows = [...document.querySelectorAll('[data-freeform-note-row]')];
              const editedRow = rows[200];
              const input = editedRow.querySelector('[data-freeform-note-input]');
              const checkbox = editedRow.querySelector('[data-freeform-note-checkbox]');
              input.value = 'Changed freeform line';
              input.dispatchEvent(new Event('input', { bubbles: true }));
              checkbox.checked = false;
              checkbox.dispatchEvent(new Event('change', { bubbles: true }));
              input.dispatchEvent(new KeyboardEvent('keydown', {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
              }));
              const countAfterAdd = document.querySelectorAll('[data-freeform-note-row]').length;
              const addedRow = editedRow.nextElementSibling;
              const addedInput = addedRow.querySelector('[data-freeform-note-input]');
              addedInput.dispatchEvent(new KeyboardEvent('keydown', {
                bubbles: true,
                cancelable: true,
                key: 'Backspace',
              }));

              const nextInput = rows[201].querySelector('[data-freeform-note-input]');
              rows[201].querySelector('.statement-content').dispatchEvent(new MouseEvent('click', { bubbles: true }));
              const rowClickFocused = document.activeElement === nextInput;
              nextInput.setSelectionRange(nextInput.value.length, nextInput.value.length);
              nextInput.dispatchEvent(new KeyboardEvent('keydown', {
                bubbles: true,
                cancelable: true,
                key: 'ArrowDown',
              }));
              return {
                listenerAdds: window.testStatementControlListenerAdds,
                lineCount: document.querySelectorAll('[data-freeform-note-row]').length,
                countAfterAdd,
                rowUnchecked: editedRow.classList.contains('is-unchecked'),
                rowClickFocused,
                arrowFocusedNext: document.activeElement === rows[202].querySelector('[data-freeform-note-input]'),
                editedOutput: window.testEditor.serializeCurrentNoteEditor().edited_output_text,
              };
            }"""
        )

    assert result["listenerAdds"] == 0
    assert result["countAfterAdd"] == 377
    assert result["lineCount"] == 376
    assert result["rowUnchecked"] is True
    assert result["rowClickFocused"] is True
    assert result["arrowFocusedNext"] is True
    assert "Changed freeform line" in result["editedOutput"]


def test_hydrated_generated_note_starts_copy_review_without_rebuilding_rows(static_repo_server):
    with _browser_page(static_repo_server) as page:
        result = page.evaluate(
            """async ({ moduleUrl }) => {
              const observers = [];
              class ControlledIntersectionObserver {
                constructor(callback) { this.callback = callback; this.targets = new Set(); observers.push(this); }
                observe(target) { this.targets.add(target); }
                unobserve(target) { this.targets.delete(target); }
                disconnect() { this.targets.clear(); }
                trigger(targets = [...this.targets]) {
                  this.callback(targets.map((target) => ({ target, isIntersecting: true })));
                }
              }
              window.IntersectionObserver = ControlledIntersectionObserver;
              document.body.innerHTML = `
                <select data-template><option data-template-mode="freeform" selected>Freeform</option></select>
                <div data-latest data-latest-generated-id="doc-1" data-latest-generated-mode="freeform"></div>
                <div data-structured-panel hidden><div data-structured-sections></div></div>
                <div data-freeform-panel data-generated-freeform-panel><div data-freeform-rows>
                  <article data-freeform-note-row><input type="checkbox" checked data-freeform-note-checkbox><div class="statement-content"><textarea data-freeform-note-input>First line</textarea></div></article>
                  <article data-freeform-note-row><input type="checkbox" checked data-freeform-note-checkbox><div class="statement-content"><textarea data-freeform-note-input>Second line</textarea></div></article>
                </div></div>
                <button data-copy-all></button><div data-toolbar></div><div data-status></div><div data-badge></div>`;
              const dom = {
                generateOutputTemplateSelect: document.querySelector('[data-template]'),
                generatedStructuredPanel: document.querySelector('[data-structured-panel]'),
                generatedStructuredSections: document.querySelector('[data-structured-sections]'),
                generatedFreeformPanel: document.querySelector('[data-freeform-panel]'),
                generatedFreeformRows: document.querySelector('[data-freeform-rows]'),
                copyStructuredLinesButton: document.querySelector('[data-copy-all]'),
                latestGeneratedOutput: document.querySelector('[data-latest]'),
                noteEditorToolbar: document.querySelector('[data-toolbar]'),
                structuredCopyStatus: document.querySelector('[data-status]'),
                templateModeBadge: document.querySelector('[data-badge]'),
              };
              const originalRows = [...dom.generatedFreeformRows.children];
              const finalRow = originalRows.at(-1);
              let finalRowBottom = 1000;
              dom.generatedFreeformPanel.getBoundingClientRect = () => ({
                bottom: 1000, height: 1000, left: 0, right: 500, top: 0, width: 500,
              });
              finalRow.getBoundingClientRect = () => ({
                bottom: finalRowBottom,
                height: 40,
                left: 0,
                right: 500,
                top: finalRowBottom - 40,
                width: 500,
              });
              finalRow.getClientRects = () => [finalRow.getBoundingClientRect()];
              const { createStructuredEditor } = await import(moduleUrl);
              const editor = createStructuredEditor({
                dom,
                structuredSectionDefinitions: [],
                getTranscriptId: () => 'transcript-1',
                getDraftText: () => '',
                getTranscriptWaitingForText: () => false,
                syncGenerationAvailability: () => {},
                onNoteEditorChanged: () => {},
              });
              editor.bootstrapFromDom();
              editor.initializeHydratedGeneratedDocument({
                id: 'doc-1', kind: 'generated_note', status: 'ready', document_mode: 'freeform',
                edited_output_text: 'First line\\nSecond line',
              });
              await new Promise((resolve) => requestAnimationFrame(resolve));
              await new Promise((resolve) => requestAnimationFrame(resolve));
              const panel = dom.generatedFreeformPanel;
              const beforeReview = editor.noteCopyReviewBlocker();
              panel.getBoundingClientRect = () => ({ bottom: 100, height: 100, left: 0, right: 500, top: 0, width: 500 });
              finalRowBottom = 100;
              const copyObserver = observers.find((observer) => observer.targets.has(panel));
              copyObserver.trigger([panel]);
              const afterReview = editor.noteCopyReviewBlocker();

              editor.initializeHydratedGeneratedDocument({
                id: 'working:transcript-1', kind: 'working_note', status: 'ready', document_mode: 'freeform',
              });
              return {
                beforeReview,
                afterReview,
                preservedRows: originalRows.every((row, index) => dom.generatedFreeformRows.children[index] === row),
                workingNoteBlocker: editor.noteCopyReviewBlocker(),
              };
            }""",
            {"moduleUrl": f"{static_repo_server}/app/static/js/transcribe/structured.js"},
        )

    assert "Scroll to the bottom" in result["beforeReview"]
    assert result["afterReview"] is None
    assert result["preservedRows"] is True
    assert result["workingNoteBlocker"] is None
