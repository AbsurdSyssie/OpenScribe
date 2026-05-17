import subprocess
import textwrap
from pathlib import Path


def test_document_navigator_clears_stale_note_editor_when_note_selection_becomes_empty(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "document_navigator_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = __OPENSCRIBE_ROOT__;
            const documentsPath = `${root}/app/static/js/transcribe/documents.js`;
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');

            const makeElement = () => ({
              hidden: false,
              dataset: {},
              innerHTML: '',
              textContent: '',
              children: [],
              className: '',
              title: '',
              type: '',
              role: '',
              tabIndex: 0,
              appendChild(child) { this.children.push(child); },
              querySelector() { return null; },
              closest(selector) {
                if (selector === '[data-generated-structured-panel], [data-generated-freeform-panel]') {
                  return this.insideNoteEditor ? this : null;
                }
                return null;
              },
            });

            const fakeDocument = {
              activeElement: null,
              createElement: () => makeElement(),
              querySelector: () => null,
              dispatchEvent: () => {},
            };

            const sandbox = {
              Array,
              Boolean,
              CustomEvent: class CustomEvent {
                constructor(type, init) {
                  this.type = type;
                  this.detail = init?.detail;
                }
              },
              JSON,
              Map,
              String,
              console,
              window: { document: fakeDocument, CustomEvent: null },
            };
            sandbox.window.CustomEvent = sandbox.CustomEvent;
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });

            const latestGeneratedOutput = makeElement();
            latestGeneratedOutput.dataset.latestGeneratedId = 'note-a';
            latestGeneratedOutput.dataset.latestGeneratedMode = 'freeform';
            latestGeneratedOutput.dataset.latestGeneratedStatus = 'ready';

            const focusedEditorInput = makeElement();
            focusedEditorInput.insideNoteEditor = true;
            fakeDocument.activeElement = focusedEditorInput;

            const renderCalls = [];
            let state = {
              workspaceNoteDocuments: [],
              workspaceStructuredContext: {},
              selectedNoteDocumentId: 'note-a',
            };

            const navigator = sandbox.createDocumentNavigator({
              dom: {
                latestGeneratedOutput,
                noteHistory: makeElement(),
                noteMeta: makeElement(),
                noteSelector: makeElement(),
                noteSelectorCount: makeElement(),
                noteSelectorWrap: makeElement(),
                outputLlmRequestSlot: makeElement(),
                outputRedactionSlot: makeElement(),
              },
              helpers: {
                escapeHtml: (value) => String(value || ''),
                renderGeneratedOutput: (document, structuredContext) => {
                  renderCalls.push({ document, structuredContext });
                },
                renderRedactionDebugPanel: () => {},
              },
              getState: () => state,
              setState: (patch) => { state = { ...state, ...patch }; },
              shouldPreserveNoteEditorRender: (nextSelectedNoteDocumentId = latestGeneratedOutput.dataset.latestGeneratedId || '') => {
                const currentDocumentId = latestGeneratedOutput.dataset.latestGeneratedId || '';
                const targetDocumentId = nextSelectedNoteDocumentId || '';
                return Boolean(
                  fakeDocument.activeElement?.closest('[data-generated-structured-panel], [data-generated-freeform-panel]')
                  && currentDocumentId === targetDocumentId
                );
              },
            });

            navigator.renderSelectedNote();

            assert.equal(renderCalls.length, 1, 'empty note selection should force a render instead of preserving focused stale DOM');
            assert.equal(renderCalls[0].document, null);
            assert.equal(latestGeneratedOutput.dataset.latestGeneratedId, '');
            assert.equal(state.selectedNoteDocumentId, null);
            """
        ).replace("__OPENSCRIBE_ROOT__", repr(str(root))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
