import os
import subprocess
import textwrap
from pathlib import Path


def test_note_selection_rail_formats_created_at_in_browser_timezone(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "note_created_at_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const sourcePath = __SOURCE_PATH__;
            const source = fs.readFileSync(sourcePath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replace('export function workingNoteToEditorDocument', 'function workingNoteToEditorDocument')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');
            const makeElement = () => ({
              dataset: {},
              hidden: false,
              innerHTML: '',
              textContent: '',
              children: [],
              append(...children) { this.children.push(...children); },
              appendChild(child) { this.children.push(child); },
              setAttribute(name, value) { this[name] = value; },
            });
            const fakeDocument = {
              activeElement: null,
              createElement: () => makeElement(),
              dispatchEvent: () => {},
              querySelector: () => null,
            };
            const sandbox = {
              Array,
              Boolean,
              Date,
              Map,
              Number,
              String,
              window: { document: fakeDocument },
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: sourcePath });

            const noteMeta = makeElement();
            const noteSelector = makeElement();
            const initialTimestamp = makeElement();
            initialTimestamp.dataset.noteCreatedAt = '2026-07-28T14:00:00+00:00';
            initialTimestamp.textContent = '26-07-28 14:00';
            noteSelector.querySelectorAll = () => [initialTimestamp];
            const navigator = sandbox.createDocumentNavigator({
              dom: {
                latestGeneratedOutput: makeElement(),
                noteMeta,
                noteSelector,
                noteSelectorCount: makeElement(),
                noteSelectorWrap: makeElement(),
              },
              helpers: {
                escapeHtml: (value) => String(value || ''),
                refreshIcons: () => {},
                renderGeneratedOutput: () => {},
                renderRedactionDebugPanel: () => {},
              },
              getState: () => ({
                hasActiveTranscript: false,
                selectedNoteDocumentId: 'note-1',
                workspaceNoteDocuments: [{
                  id: 'note-1',
                  created_at: '2026-07-28T14:00:00+00:00',
                  kind: 'generated_note',
                  status: 'ready',
                  title: 'Consultation note',
                }, {
                  id: 'note-2',
                  created_at: '2026-01-28T14:00:00+00:00',
                  kind: 'generated_note',
                  status: 'ready',
                  title: 'Winter consultation note',
                }],
                workspaceStructuredContext: {},
              }),
              setState: () => {},
            });

            assert.equal(initialTimestamp.textContent, '28/07/26, 15:00');
            navigator.renderSelectedNote();

            assert.ok(noteSelector.children[0].children[0].innerHTML.includes('28/07/26, 15:00'));
            assert.ok(noteSelector.children[1].children[0].innerHTML.includes('28/01/26, 14:00'));
            assert.ok(noteMeta.textContent.includes('28/07/26, 15:00'));
            """
        ).replace(
            "__SOURCE_PATH__",
            repr(str(root / "app" / "static" / "js" / "transcribe" / "documents.js")),
        ),
        encoding="utf-8",
    )

    env = {**os.environ, "TZ": "Europe/London"}
    subprocess.run(["node", str(runner)], check=True, cwd=root, env=env)


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
