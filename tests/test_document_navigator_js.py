import os
import subprocess
import textwrap
from pathlib import Path


def test_initial_note_render_preserver_is_one_shot_and_requires_exact_note_version(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "initial_note_render_preserver_runner.mjs"
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
              .replaceAll('export function ', 'function ')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');
            const sandbox = { Boolean, String };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: sourcePath });

            const initial = {
              targetId: 'note-1',
              documentMode: 'freeform',
              kind: 'generated_note',
              status: 'ready',
              updatedAt: '2026-08-12T10:00:00+00:00',
              hasEditorDom: true,
            };
            const sameNote = { ...initial };

            const exactMatch = sandbox.createInitialNoteRenderPreserver(initial);
            assert.equal(exactMatch(sameNote), true);
            assert.equal(exactMatch(sameNote), false, 'the server DOM may only skip the first client render');

            const equivalentUtcMarker = sandbox.createInitialNoteRenderPreserver(initial);
            assert.equal(
              equivalentUtcMarker({ ...sameNote, updatedAt: '2026-08-12T10:00:00Z' }),
              true,
              'Jinja and API datetime formats must resolve to the same version marker',
            );

            for (const changedField of ['targetId', 'documentMode', 'kind', 'status', 'updatedAt']) {
              const preserve = sandbox.createInitialNoteRenderPreserver(initial);
              assert.equal(
                preserve({ ...sameNote, [changedField]: `different-${changedField}` }),
                false,
                `${changedField} must identify the same canonical note`,
              );
            }

            const absentDom = sandbox.createInitialNoteRenderPreserver({ ...initial, hasEditorDom: false });
            assert.equal(absentDom(sameNote), false);

            const unfinishedNote = sandbox.createInitialNoteRenderPreserver({ ...initial, status: 'processing' });
            assert.equal(unfinishedNote({ ...sameNote, status: 'processing' }), false);
            """
        ).replace(
            "__SOURCE_PATH__",
            repr(str(root / "app" / "static" / "js" / "transcribe" / "documents.js")),
        ),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_document_navigator_preserves_matching_server_note_once_but_updates_metadata(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "initial_note_navigator_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = __OPENSCRIBE_ROOT__;
            const documentsPath = `${root}/app/static/js/transcribe/documents.js`;
            const noteTargetsPath = `${root}/app/static/js/transcribe/noteTargets.js`;
            const noteTargetsSource = fs.readFileSync(noteTargetsPath, 'utf8').replaceAll('export const ', 'var ');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replaceAll('export function ', 'function ')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');
            const makeElement = () => ({
              dataset: {}, hidden: false, innerHTML: '', textContent: '', children: [],
              append(...children) { this.children.push(...children); },
              appendChild(child) { this.children.push(child); },
              insertAdjacentHTML() {},
              querySelectorAll() { return []; },
              setAttribute(name, value) { this[name] = value; },
            });
            const fakeDocument = {
              activeElement: null,
              createElement: () => makeElement(),
              dispatchEvent: () => {},
              querySelector: () => null,
            };
            const sandbox = {
              Array, Boolean, CustomEvent: class CustomEvent {}, Date, JSON, Map, Number, String,
              window: { document: fakeDocument },
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(noteTargetsSource, sandbox, { filename: noteTargetsPath });
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });

            const note = {
              id: 'note-1',
              document_mode: 'freeform',
              updated_at: '2026-08-12T10:00:00+00:00',
              status: 'ready',
              title: 'Long note',
            };
            const latestGeneratedOutput = makeElement();
            const renderCalls = [];
            const hydratedLifecycleCalls = [];
            const preserveInitial = sandbox.createInitialNoteRenderPreserver({
              targetId: note.id,
              documentMode: note.document_mode,
              kind: 'generated_note',
              status: note.status,
              updatedAt: note.updated_at,
              hasEditorDom: true,
            });
            let pendingHydratedLifecycle = false;
            const state = {
              hasActiveTranscript: true,
              activeTranscriptId: 'transcript-1',
              selectedNoteDocumentId: note.id,
              workspaceNoteDocuments: [note],
              workspaceStructuredContext: {},
            };
            const navigator = sandbox.createDocumentNavigator({
              dom: {
                latestGeneratedOutput,
                noteMeta: makeElement(),
                noteSelector: makeElement(),
                noteSelectorCount: makeElement(),
                noteSelectorWrap: makeElement(),
                outputLlmRequestSlot: makeElement(),
                outputRedactionSlot: makeElement(),
              },
              helpers: {
                escapeHtml: (value) => String(value || ''),
                refreshIcons: () => {},
                renderGeneratedOutput: (...args) => renderCalls.push(args),
                initializeHydratedGeneratedDocument: (document) => hydratedLifecycleCalls.push(document),
                renderRedactionDebugPanel: () => {},
              },
              getState: () => state,
              setState: () => {},
              shouldPreserveNoteEditorRender: (targetId, selectedNote) => preserveInitial({
                targetId,
                documentMode: selectedNote?.document_mode,
                kind: selectedNote?.kind || 'generated_note',
                status: selectedNote?.status,
                updatedAt: selectedNote?.updated_at,
              }) && (pendingHydratedLifecycle = true),
              shouldInitializeHydratedNoteEditor: (selectedNote) => {
                if (!pendingHydratedLifecycle || selectedNote?.id !== note.id) return false;
                pendingHydratedLifecycle = false;
                return true;
              },
            });

            navigator.renderSelectedNote();
            assert.equal(renderCalls.length, 0, 'matching server-rendered note should stay mounted');
            assert.deepEqual(hydratedLifecycleCalls, [note], 'preserved server DOM must initialise editor lifecycle state');
            assert.equal(latestGeneratedOutput.dataset.latestGeneratedId, note.id);
            assert.equal(latestGeneratedOutput.dataset.latestGeneratedMode, note.document_mode);
            assert.equal(latestGeneratedOutput.dataset.latestGeneratedUpdatedAt, note.updated_at);

            navigator.renderSelectedNote();
            assert.equal(renderCalls.length, 1, 'later renders must use current workspace content');
            assert.equal(hydratedLifecycleCalls.length, 1, 'only the one-shot preserved render initialises hydrated lifecycle state');
            """
        ).replace("__OPENSCRIBE_ROOT__", repr(str(root))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_document_switch_flushes_serialized_note_autosave_after_intervening_edit(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "note_autosave_document_switch_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = __OPENSCRIBE_ROOT__;
            const documentsPath = `${root}/app/static/js/transcribe/documents.js`;
            const noteTargetsPath = `${root}/app/static/js/transcribe/noteTargets.js`;
            const autosavePath = `${root}/app/static/js/transcribe/autosaveController.js`;
            const noteTargetsSource = fs.readFileSync(noteTargetsPath, 'utf8').replaceAll('export const ', 'var ');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replaceAll('export function ', 'function ');
            const autosaveSource = fs.readFileSync(autosavePath, 'utf8')
              .replaceAll('export function ', 'function ');
            const makeElement = () => ({
              dataset: {}, hidden: false, innerHTML: '', textContent: '', children: [],
              append(...children) { this.children.push(...children); },
              appendChild(child) { this.children.push(child); },
              replaceChildren(...children) { this.children = children; },
              insertAdjacentHTML() {}, querySelectorAll() { return []; },
              setAttribute(name, value) { this[name] = value; },
              addEventListener() {}, remove() {},
            });
            const fakeDocument = {
              activeElement: null, createElement: makeElement, dispatchEvent() {},
              querySelector() { return null; },
            };
            const timers = {
              setTimeout(callback) { return { callback }; }, clearTimeout() {},
            };
            const sandbox = {
              Array, Boolean, Date, Intl, JSON, Map, Number, Object, String, WeakMap,
              clearTimeout() {}, setTimeout() {},
              window: {
                document: fakeDocument, CustomEvent: class CustomEvent {},
                requestAnimationFrame(callback) { callback(); },
              },
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(noteTargetsSource, sandbox, { filename: noteTargetsPath });
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });
            vm.runInContext(autosaveSource, sandbox, { filename: autosavePath });

            let noteEditDirty = true;
            let editorText = 'first draft';
            let baseline = 'revision-1';
            let serverRevision = baseline;
            let serverText = '';
            let firstPatchStarted;
            let releaseFirstPatch;
            const firstStarted = new Promise((resolve) => { firstPatchStarted = resolve; });
            const firstGate = new Promise((resolve) => { releaseFirstPatch = resolve; });
            const requests = [];
            const autosave = sandbox.createAutosaveController({
              timers,
              isDirty: () => noteEditDirty,
              save: async ({ version }) => {
                const request = { expected_updated_at: baseline, text: editorText };
                requests.push(request);
                if (requests.length === 1) {
                  firstPatchStarted();
                  await firstGate;
                }
                if (request.expected_updated_at !== serverRevision) {
                  throw new Error('409 revision conflict');
                }
                serverText = request.text;
                serverRevision = `revision-${requests.length + 1}`;
                if (version === autosave.editVersion) {
                  noteEditDirty = false;
                } else {
                  baseline = serverRevision;
                }
                return { id: 'note-1', updated_at: serverRevision };
              },
            });

            const note = (id, title) => ({ id, title, status: 'ready', document_mode: 'freeform', updated_at: '2026-08-12T10:00:00Z' });
            const state = {
              hasActiveTranscript: false, activeTranscriptId: null,
              selectedNoteDocumentId: 'note-1', selectedNoteSlotKey: null,
              workspaceNoteDocuments: [note('note-1', 'First'), note('note-2', 'Second')],
              workspaceFollowupDocuments: [], workspaceStructuredContext: {},
            };
            const navigator = sandbox.createDocumentNavigator({
              dom: {
                latestGeneratedOutput: makeElement(), noteMeta: makeElement(), noteSelector: makeElement(),
                noteSelectorCount: makeElement(), noteSelectorWrap: makeElement(),
                outputLlmRequestSlot: makeElement(), outputRedactionSlot: makeElement(),
              },
              helpers: {
                escapeHtml: String, refreshIcons() {}, renderGeneratedOutput() {}, renderRedactionDebugPanel() {},
                setTab() {},
              },
              getState: () => state,
              setState: (patch) => Object.assign(state, patch),
              hasPendingGeneratedNoteEdits: () => noteEditDirty,
              persistNoteEditsSilently: (options) => autosave.flush(options),
              clearNoteEditorDirty: () => { noteEditDirty = false; },
            });

            autosave.markDirty();
            const firstSave = autosave.persist();
            await firstStarted;
            editorText = 'edited while first save was pending';
            noteEditDirty = true;
            autosave.markDirty();
            const switching = navigator.selectDocumentFromUi('note', 'note-2');
            await Promise.resolve();
            assert.equal(state.selectedNoteDocumentId, 'note-1', 'navigation must wait for the current and queued saves');
            releaseFirstPatch();
            await firstSave;
            assert.equal(await switching, true);
            assert.equal(state.selectedNoteDocumentId, 'note-2');
            assert.equal(serverText, 'edited while first save was pending', 'the latest editor text must reach the server before navigation');
            assert.equal(noteEditDirty, false);
            assert.deepEqual(JSON.parse(JSON.stringify(requests)), [
              { expected_updated_at: 'revision-1', text: 'first draft' },
              { expected_updated_at: 'revision-2', text: 'edited while first save was pending' },
            ]);
            assert.equal(serverRevision, 'revision-3');
            """
        ).replace("__OPENSCRIBE_ROOT__", repr(str(root))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_document_navigator_only_renders_hallucination_debug_card_with_payload(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "hallucination_debug_panel_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = __OPENSCRIBE_ROOT__;
            const documentsPath = `${root}/app/static/js/transcribe/documents.js`;
            const noteTargetsPath = `${root}/app/static/js/transcribe/noteTargets.js`;
            const noteTargetsSource = fs.readFileSync(noteTargetsPath, 'utf8').replaceAll('export const ', 'var ');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replaceAll('export function ', 'function ')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');
            const makeElement = () => ({
              dataset: {}, hidden: false, innerHTML: '', textContent: '', children: [],
              append(...children) { this.children.push(...children); },
              appendChild(child) { this.children.push(child); },
              insertAdjacentHTML() {},
              querySelectorAll() { return []; },
              setAttribute(name, value) { this[name] = value; },
            });
            const fakeDocument = {
              activeElement: null,
              createElement: () => makeElement(),
              dispatchEvent: () => {},
              querySelector: () => null,
            };
            const sandbox = {
              Array, Boolean, CustomEvent: class CustomEvent {}, Date, JSON, Map, Number, String,
              window: { document: fakeDocument },
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(noteTargetsSource, sandbox, { filename: noteTargetsPath });
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });

            const slot = makeElement();
            const noteMeta = makeElement();
            const state = {
              hasActiveTranscript: true,
              activeTranscriptId: 'transcript-1',
              selectedNoteDocumentId: 'note-1',
              workspaceNoteDocuments: [],
              workspaceStructuredContext: {},
            };
            const navigator = sandbox.createDocumentNavigator({
              dom: {
                latestGeneratedOutput: makeElement(), noteMeta,
                noteSelector: makeElement(), noteSelectorCount: makeElement(), noteSelectorWrap: makeElement(),
                outputLlmRequestSlot: slot, outputRedactionSlot: makeElement(),
              },
              helpers: {
                escapeHtml: (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
                refreshIcons: () => {}, renderGeneratedOutput: () => {}, renderRedactionDebugPanel: () => {},
              },
              getState: () => state,
              setState: () => {},
            });

            const render = (bucket, debugPayload) => {
              slot.children = [];
              state.workspaceNoteDocuments = [{
                id: 'note-1', title: 'Note', status: 'ready', document_mode: 'freeform',
                hallucination_check_bucket: bucket,
                hallucination_check_debug_json: debugPayload,
              }];
              navigator.renderSelectedNote();
            };
            const debugCards = () => slot.children.filter((child) => child.dataset.hallucinationCheckDebugPanel === 'true');

            for (const bucket of ['checked', 'unchecked']) {
              render(bucket, null);
              assert.equal(debugCards().length, 0, `${bucket} status must not create an orphan debug card`);
              assert.doesNotMatch(slot.children.map((child) => child.innerHTML).join(''), /HALLUCINATION_CHECK_DEBUG_UI/);
              assert.match(noteMeta.textContent, /Draft ready/, 'ordinary draft status uses a human label');
              assert.doesNotMatch(noteMeta.textContent, /checked|unchecked|not_applicable/);
            }

            render('checked', { first_pass: '<synthetic>' });
            assert.equal(debugCards().length, 1, 'an authorized captured payload renders the debug card');
            assert.match(debugCards()[0].innerHTML, /&lt;synthetic&gt;/);
            assert.doesNotMatch(debugCards()[0].innerHTML, /<synthetic>/);
            """
        ).replace("__OPENSCRIBE_ROOT__", repr(str(root))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


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
              .replaceAll('export function ', 'function ')
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
              .replaceAll('export function ', 'function ')
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


def test_note_regeneration_controls_and_scroll_rail_contract():
    root = Path(__file__).resolve().parents[1]
    documents = (root / "app/static/js/transcribe/documents.js").read_text(encoding="utf-8")
    regeneration = (root / "app/static/js/transcribe/regeneration.js").read_text(encoding="utf-8")
    workspace = (root / "app/templates/transcribe/_workspace.html").read_text(encoding="utf-8")
    actions = (root / "app/static/js/transcribe/actions.js").read_text(encoding="utf-8")
    styles = (root / "app/static/css/transcribe.css").read_text(encoding="utf-8")

    assert "noteRegenerate" in documents and "rotate-ccw" in documents
    assert "item.kind !== 'working_note'" in documents
    assert "data-note-selector-scroll-prev" in workspace
    assert "data-note-selector-scroll-next" in workspace
    assert "container.addEventListener('keydown'" in documents
    assert "ArrowLeft" in documents and "ArrowRight" in documents
    assert "createGeneratedNoteRegenerationController" in actions
    assert "steering_text: steeringText || null" in actions
    assert "steering_preset: steeringPreset || null" in actions
    for marker in (
        "data-regeneration-now",
        "data-regeneration-preset",
        "data-regeneration-steering",
        "data-regeneration-submit",
        "Regenerate now",
        "More detail",
        "Less detail",
        "setAttribute('role', 'dialog')",
        "window.document.body || item",
        "getBoundingClientRect",
        "positionPopover",
        "addEventListener?.('scroll', positionPopover, true)",
        "removeEventListener?.('scroll', positionPopover, true)",
        "Escape",
    ):
        assert marker in regeneration
    assert ".document-switcher {" in styles
    assert "overflow-x: auto" in styles
    popover_styles = styles.split(".note-regeneration-popover {", 1)[1].split("}", 1)[0]
    assert "z-index: 600" in popover_styles
    assert "data-generate-output-label" in workspace

    app = (root / "app/static/js/transcribe/app.js").read_text(encoding="utf-8")
    shell = (root / "app/templates/transcribe/_shell_extras.html").read_text(encoding="utf-8")
    assert "actions.js?v=20261021-example-consultation" in app
    assert "documents.js?v=20261014-generation-status" in app
    assert "structured.js?v=20260918-retired-note-workspace" in app
    assert "app.js?v=20261028-split-template-refresh" in shell


def test_retired_note_workspace_compatibility_plumbing_is_absent():
    root = Path(__file__).resolve().parents[1]
    documents = (root / "app/static/js/transcribe/documents.js").read_text(encoding="utf-8")
    structured = (root / "app/static/js/transcribe/structured.js").read_text(encoding="utf-8")
    actions = (root / "app/static/js/transcribe/actions.js").read_text(encoding="utf-8")
    app = (root / "app/static/js/transcribe/app.js").read_text(encoding="utf-8")
    workspace = (root / "app/templates/transcribe/_workspace.html").read_text(encoding="utf-8")

    for source in (documents, structured, actions, app, workspace):
        assert "data-legacy-note-workspace" not in source
        assert "data-note-history" not in source
        assert "openscribe:legacy-workspace-document-selected" not in source
    assert "workspaceNoteHistoryDocuments = [];" in app
    assert "workspaceNoteHistoryDocuments = noteDocuments;" in app


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
              .replaceAll('export function ', 'function ')
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


def test_delayed_generation_status_and_support_message_contract(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / 'generation_status_runner.mjs'
    runner.write_text(textwrap.dedent(r'''
        import assert from 'node:assert/strict';
        import fs from 'node:fs';
        import vm from 'node:vm';
        const source = fs.readFileSync(__SOURCE__, 'utf8')
          .replace(/^import .*$/m, '')
          .replaceAll('export const ', 'var ')
          .replaceAll('export function ', 'function ');
        let pending = null;
        let cleared = 0;
        const sandbox = {Date, WeakMap, Boolean, String, Number,
          setTimeout: (callback, delay) => {pending = {callback, delay}; return 1;},
          clearTimeout: () => {cleared += 1; pending = null;}};
        sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox);
        const status = {textContent: 'Generating your note'};
        const container = {querySelector: () => status};
        sandbox.scheduleDelayedGenerationStatus(container, {since: new Date().toISOString()});
        assert.ok(pending.delay > 14000 && pending.delay <= 15000);
        assert.equal(status.textContent, 'Generating your note');
        sandbox.clearDelayedGenerationStatus(container);
        assert.equal(pending, null, 'switching away cancels delayed updates');
        assert.equal(cleared, 1);
        sandbox.scheduleDelayedGenerationStatus(container, {since: new Date(Date.now()-16000).toISOString()});
        assert.match(status.textContent, /still working/);
        assert.match(sandbox.generationFailureMessage({category: 'temporary'}), /in a minute/);
        assert.match(sandbox.generationFailureMessage({category: 'configuration'}), /team leader/);
        assert.match(sandbox.generationFailureMessage({category: 'configuration', technical_details: {}}), /Team → AI services/);
        assert.match(sandbox.generationFailureMessage({category: 'input_or_validation'}), /saved inputs/);
        assert.equal(sandbox.generationRetryStatusHtml({category: 'temporary'}), '');
        assert.equal(sandbox.generationFailureDetailsHtml(null), '');
        const rendered = sandbox.generationFailureDetailsHtml({support_reference: '<script>', occurred_at: '<img>',
          technical_details: {application_code: '<secret>', provider_http_status: 503}});
        assert.ok(!rendered.includes('<script>') && !rendered.includes('<img>'));
        assert.match(rendered, /Technical details/);
        assert.match(rendered, /Failed \(UTC\)/);
        const localDetails = sandbox.generationFailureDetailsHtml({occurred_at: new Date().toISOString()}, {formatTime: sandbox.formatWorkspaceCreatedAt});
        assert.match(localDetails, /local time/);
        assert.match(sandbox.generationRetryStatusHtml({technical_details: {provider_http_status: 429}}), /We’re retrying/);
    ''').replace('__SOURCE__', repr(str(root / 'app/static/js/transcribe/documents.js'))), encoding='utf-8')
    subprocess.run(['node', str(runner)], check=True, cwd=root)
