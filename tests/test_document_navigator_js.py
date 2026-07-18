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
            const noteTargetsPath = `${root}/app/static/js/transcribe/noteTargets.js`;
            const noteTargetsSource = fs.readFileSync(noteTargetsPath, 'utf8')
              .replaceAll('export const ', 'var ');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replace('export function workingNoteToEditorDocument', 'function workingNoteToEditorDocument')
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
            vm.runInContext(noteTargetsSource, sandbox, { filename: noteTargetsPath });
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


def test_working_note_to_editor_document_maps_virtual_target(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "working_note_document_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = __OPENSCRIBE_ROOT__;
            const documentsPath = `${root}/app/static/js/transcribe/documents.js`;
            const noteTargetsPath = `${root}/app/static/js/transcribe/noteTargets.js`;
            const noteTargetsSource = fs.readFileSync(noteTargetsPath, 'utf8')
              .replaceAll('export const ', 'var ');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replace('export function workingNoteToEditorDocument', 'function workingNoteToEditorDocument')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');
            const sandbox = { Array, Boolean, Object, String, console };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(noteTargetsSource, sandbox, { filename: noteTargetsPath });
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });

            const document = sandbox.workingNoteToEditorDocument({
              transcriptId: 'transcript-1',
              selectedTemplateMode: 'structured',
              structuredSectionDefinitions: [
                { key: 'problem', label: 'Problem' },
                { key: 'history', label: 'History' },
                { key: 'family_history', label: 'Family history' },
                { key: 'social_history', label: 'Social history' },
                { key: 'examination', label: 'Examination' },
                { key: 'comment', label: 'Comment' },
                { key: 'tasks', label: 'Tasks' },
                { key: 'investigations', label: 'Investigations' },
              ],
              workingNote: {
                mode: 'structured',
                updated_at: '2026-05-19T10:00:00+00:00',
                structured_note: {
                  sections: {
                    problem: ['Headache', 'Nausea'],
                    tasks: ['Safety net'],
                  },
                },
              },
            });

            assert.equal(document.id, 'working:transcript-1');
            assert.equal(document.kind, 'working_note');
            assert.equal(document.document_mode, 'structured');
            assert.equal(document.mode_locked, true);
            assert.deepEqual(document.sections.map((section) => [section.section_key, section.text]), [
              ['problem', 'Headache\\nNausea'],
              ['tasks', 'Safety net'],
            ]);
            assert.deepEqual(document.structured_section_definitions_json.sections.map((section) => section.section_key), [
              'problem',
              'history',
              'family_history',
              'social_history',
              'examination',
              'comment',
              'tasks',
              'investigations',
            ]);

            const unlocked = sandbox.workingNoteToEditorDocument({
              transcriptId: 'transcript-2',
              selectedTemplateMode: 'freeform',
              workingNote: {},
            });
            assert.equal(unlocked.id, 'working:transcript-2');
            assert.equal(unlocked.document_mode, 'freeform');
            assert.equal(unlocked.mode_locked, false);
            """
        ).replace("__OPENSCRIBE_ROOT__", repr(str(root))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_selecting_note_centers_rebuilt_selector_item(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "document_navigator_scroll_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = __OPENSCRIBE_ROOT__;
            const documentsPath = `${root}/app/static/js/transcribe/documents.js`;
            const noteTargetsPath = `${root}/app/static/js/transcribe/noteTargets.js`;
            const noteTargetsSource = fs.readFileSync(noteTargetsPath, 'utf8')
              .replaceAll('export const ', 'var ');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replace('export function workingNoteToEditorDocument', 'function workingNoteToEditorDocument')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');

            const scrollCalls = [];
            const scrollBody = {
              scrollTop: 640,
              clientHeight: 400,
              getBoundingClientRect: () => ({ top: 100, height: 400 }),
              scrollTo: (options) => scrollCalls.push(options),
            };
            const makeElement = (tagName = 'div') => {
              let html = '';
              const element = {
                tagName,
                hidden: false,
                dataset: {},
                textContent: '',
                children: [],
                className: '',
                title: '',
                type: '',
                set innerHTML(value) { html = value; if (!value) this.children = []; },
                get innerHTML() { return html; },
                appendChild(child) { this.children.push(child); return child; },
                append(...children) { this.children.push(...children); },
                setAttribute() {},
                closest(selector) { return selector === '.structured-workspace__body' ? scrollBody : null; },
                querySelector(selector) {
                  if (selector !== '.document-switcher-button.active') return null;
                  const pending = [...this.children];
                  while (pending.length) {
                    const child = pending.shift();
                    if (child.className?.split(' ').includes('document-switcher-button') && child.className.split(' ').includes('active')) return child;
                    pending.push(...(child.children || []));
                  }
                  return null;
                },
                getBoundingClientRect() {
                  const index = Number(String(this.dataset.documentId || '').replace('note-', '')) || 1;
                  return { top: 80 + index * 50, height: 50 };
                },
              };
              return element;
            };
            const fakeDocument = {
              activeElement: null,
              createElement: (tagName) => makeElement(tagName),
              querySelector: () => null,
              dispatchEvent: () => {},
            };
            const sandbox = {
              Array,
              Boolean,
              CustomEvent: class CustomEvent {},
              JSON,
              Map,
              Math,
              Number,
              Object,
              String,
              console,
              window: { document: fakeDocument, CustomEvent: null },
            };
            sandbox.window.CustomEvent = sandbox.CustomEvent;
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(noteTargetsSource, sandbox, { filename: noteTargetsPath });
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });

            const noteSelector = makeElement();
            let state = {
              hasActiveTranscript: false,
              workspaceNoteDocuments: Array.from({ length: 8 }, (_, index) => ({
                id: `note-${index + 1}`,
                title: `Note ${index + 1}`,
                status: 'ready',
                created_at: '2026-07-18T12:00:00Z',
              })),
              workspaceStructuredContext: {},
              selectedNoteDocumentId: 'note-1',
            };
            const navigator = sandbox.createDocumentNavigator({
              dom: {
                noteSelector,
                noteSelectorWrap: makeElement(),
              },
              helpers: {
                escapeHtml: (value) => String(value || ''),
                renderGeneratedOutput: () => {},
                renderRedactionDebugPanel: () => {},
                setTab: () => {},
              },
              getState: () => state,
              setState: (patch) => { state = { ...state, ...patch }; },
              clearNoteEditorDirty: () => {},
            });

            await navigator.selectDocumentFromUi('note', 'note-8');

            assert.equal(scrollCalls.length, 1);
            assert.equal(scrollCalls[0].top, 845);
            assert.equal(scrollCalls[0].behavior, 'auto');
            """
        ).replace("__OPENSCRIBE_ROOT__", repr(str(root))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
