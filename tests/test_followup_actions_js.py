import subprocess
import textwrap
from pathlib import Path


def test_followup_actions_route_and_preserve_steering_state_in_browser(tmp_path):
    """Exercise Follow Ups event wiring without sending any clinical content."""
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "followup_actions_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const actionsPath = __ACTIONS_PATH__;
            const documentsPath = __DOCUMENTS_PATH__;
            const appPath = __APP_PATH__;
            const actionsSource = fs.readFileSync(actionsPath, 'utf8')
              .replace("import { csrfFetch } from '../csrf.js';", '')
              .replace('export function attachTranscribeActions', 'function attachTranscribeActions');
            const documentsSource = fs.readFileSync(documentsPath, 'utf8')
              .replace("import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';", '')
              .replaceAll('export const ', 'var ')
              .replace('export function workingNoteToEditorDocument', 'function workingNoteToEditorDocument')
              .replace('export function createDocumentNavigator', 'function createDocumentNavigator');

            class FakeEvent {
              constructor(type, init = {}) { Object.assign(this, init); this.type = type; this.defaultPrevented = false; }
              preventDefault() { this.defaultPrevented = true; }
              stopPropagation() {}
            }
            class FakeElement {
              constructor() {
                this.dataset = {};
                this.listeners = new Map();
                this.value = '';
                this.hidden = false;
                this.disabled = false;
                this.textContent = '';
                this.innerHTML = '';
                this.children = [];
                this.classList = { toggle() {} };
                this.closestMatches = {};
              }
              addEventListener(type, handler) {
                const handlers = this.listeners.get(type) || [];
                handlers.push(handler);
                this.listeners.set(type, handlers);
              }
              dispatchEvent(event) {
                for (const handler of this.listeners.get(event.type) || []) handler(event);
                return !event.defaultPrevented;
              }
              click() { this.dispatchEvent(new FakeEvent('click')); }
              requestSubmit() { this.dispatchEvent(new FakeEvent('submit')); }
              querySelector(selector) { return this.querySelectors?.[selector] || null; }
              querySelectorAll() { return []; }
              setAttribute(name, value) { this[name] = String(value); }
              removeAttribute(name) { delete this[name]; }
              getAttribute() { return null; }
              focus() { this.focused = true; fakeDocument.activeElement = this; }
              select() {}
              appendChild(child) { this.children.push(child); }
              insertAdjacentHTML(_position, html) { this.innerHTML += html; }
              closest(selector) { return this.closestMatches[selector] || null; }
            }
            class FakeTextArea extends FakeElement {}
            class FakeInput extends FakeElement {}

            const documentListeners = new Map();
            const fakeDocument = {
              activeElement: null,
              addEventListener(type, handler) {
                const handlers = documentListeners.get(type) || [];
                handlers.push(handler);
                documentListeners.set(type, handlers);
              },
              dispatchEvent(event) {
                for (const handler of documentListeners.get(event.type) || []) handler(event);
              },
              createElement: () => new FakeElement(),
              querySelector: () => null,
            };
            const mobileMediaListeners = [];
            const mobileMedia = {
              matches: true,
              addEventListener(type, handler) {
                if (type === 'change') mobileMediaListeners.push(handler);
              },
              setMatches(matches) {
                this.matches = matches;
                for (const handler of mobileMediaListeners) handler({ matches });
              },
            };
            let storedHistoryOpen = 'true';
            const persistedHistoryStates = [];
            const sandbox = {
              Array,
              Boolean,
              Date,
              Event: FakeEvent,
              FormData: class FormData {},
              HTMLInputElement: FakeInput,
              HTMLTextAreaElement: FakeTextArea,
              JSON,
              Map,
              Number,
              Object,
              String,
              setTimeout,
              clearTimeout,
                  window: {
                    document: fakeDocument,
                    CustomEvent: FakeEvent,
                    addEventListener() {},
                    confirm: () => true,
                localStorage: {
                  getItem: () => storedHistoryOpen,
                  setItem: (_key, value) => {
                    storedHistoryOpen = value;
                    persistedHistoryStates.push(value);
                  },
                },
                lucide: { createIcons() {} },
                matchMedia: () => mobileMedia,
              },
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(actionsSource, sandbox, { filename: actionsPath });
            vm.runInContext(documentsSource, sandbox, { filename: documentsPath });

            const contextInput = new FakeTextArea();
            const quickActionSelect = new FakeElement();
            quickActionSelect.selectedOptions = [{ dataset: { quickActionName: 'Synthetic action' }, textContent: 'Synthetic action' }];
            const runQuickActionForm = new FakeElement();
            runQuickActionForm.querySelectors = {
              '[data-quick-action-id-input]': new FakeInput(),
              '[data-quick-action-context-hidden]': new FakeInput(),
            };
            const generateFollowupForm = new FakeElement();
            const runQuickActionTrigger = new FakeElement();
            const regenerateButton = new FakeElement();
            const regenerateLabel = new FakeElement();
            regenerateButton.querySelectors = { '[data-followup-regenerate-label]': regenerateLabel };
            const followupWorkspace = new FakeElement();
            const followupHistoryRail = new FakeElement();
            const followupHistoryToggle = new FakeElement();
            const followupHistoryOpenButton = new FakeElement();
            const followupHistoryScrim = new FakeElement();
            const followupHistorySearch = new FakeInput();
            const historyCloseButton = new FakeElement();
            const closedHistoryMenuAction = new FakeElement();
            closedHistoryMenuAction.closestMatches['details:not([open])'] = new FakeElement();
            followupHistoryRail.querySelectorAll = () => [followupHistorySearch, historyCloseButton, closedHistoryMenuAction];
            const followupHistory = new FakeElement();
            const latestFollowupOutput = new FakeElement();
            latestFollowupOutput.dataset.latestFollowupId = 'generated-synthetic';
            latestFollowupOutput.dataset.latestFollowupStatus = 'ready';
            const dom = {
              generateFollowupForm,
              runQuickActionForm,
              runQuickActionSelect: quickActionSelect,
              runQuickActionTrigger,
              quickActionContextInput: contextInput,
              latestFollowupOutput,
              regenerateLatestFollowupButton: regenerateButton,
              followupGenerateLabel: new FakeElement(),
              followupWorkspace,
              followupHistoryRail,
              followupHistoryToggle,
              followupHistoryOpenButton,
              followupHistoryScrim,
              followupHistorySearch,
              followupHistory,
            };
            const requests = [];
            const availabilityDrafts = [];
            const liveDraftText = 'Synthetic transcript source';
            let failNextRequest = false;
            let flushDictationCalls = 0;
            let failNextDictationFlush = false;
            let holdNextDictationFlush = false;
            let releaseDictationFlush = null;
            sandbox.csrfFetch = async (url, options) => {
              requests.push({ url, options });
              if (failNextRequest) {
                failNextRequest = false;
                return { ok: false, text: async () => 'Synthetic failure' };
              }
              return { ok: true, json: async () => ({ id: 'queued-synthetic' }) };
            };
            const flashes = [];
            sandbox.attachTranscribeActions({
              dom,
              getTranscriptId: () => 'transcript-synthetic',
              getTranscriptText: () => '',
              getActiveIngestionMode: () => 'whole_file',
              getIsLiveCaptureUiActive: () => false,
              getIsRecordingSwitchBlocked: () => false,
              selectDocumentFromUi: async () => {},
              showFlash: (message, kind) => flashes.push({ message, kind }),
              showCopyToast: () => {},
              parseErrorMessage: async (_response, fallback) => fallback,
              fetchWorkspace: async () => {},
              pollWorkspace: async () => {},
              scheduleWorkspaceRefreshBurst: () => {},
              syncTranscriptTitleIfNeeded: async () => {},
              persistPendingEditorsBeforeWorkspaceSwitch: async () => true,
              enqueueTemplateGeneration: async () => null,
              setVisibleStatus: () => {},
              setSessionProgress: () => {},
              setRetryAvailability: () => {},
              reflectBackendStatus: () => {},
              syncGenerationAvailability: () => availabilityDrafts.push(liveDraftText),
              persistUserAppPreferences: async () => {},
              handleOutputTemplateChange: () => {},
              setMicButtons: () => {},
              setTab: () => {},
              structuredEditor: { clearStructuredSelection() {}, selectStructuredSelection() {} },
              saveWorkingNoteBeforeGeneration: async () => {},
              saveDictationBeforeGeneration: async () => {
                flushDictationCalls += 1;
                if (failNextDictationFlush) {
                  failNextDictationFlush = false;
                  throw new Error('Synthetic dictation save failure');
                }
                if (holdNextDictationFlush) {
                  holdNextDictationFlush = false;
                  await new Promise((resolve) => { releaseDictationFlush = resolve; });
                }
              },
              clearWorkingNote: async () => {},
            });
            const settle = async () => {
              for (let index = 0; index < 4; index += 1) await new Promise((resolve) => setTimeout(resolve, 0));
            };

            // The drawer ignores a saved desktop preference on a narrow viewport.
            assert.equal(followupHistoryRail.hidden, true);
            assert.equal(followupWorkspace['data-history-collapsed'], 'true');
            assert.deepEqual(persistedHistoryStates, []);
            followupHistoryOpenButton.focus();
            followupHistoryOpenButton.click();
            assert.equal(followupHistoryRail.hidden, false);
            assert.equal(fakeDocument.activeElement, followupHistorySearch);
            assert.equal(followupHistoryRail.role, 'dialog');
            assert.equal(followupHistoryRail['aria-modal'], 'true');
            assert.deepEqual(persistedHistoryStates, []);

            // Tab stays in the open mobile drawer in both directions.
            fakeDocument.activeElement = historyCloseButton;
            const forwardTab = new FakeEvent('keydown', { key: 'Tab' });
            fakeDocument.dispatchEvent(forwardTab);
            assert.equal(forwardTab.defaultPrevented, true);
            assert.equal(fakeDocument.activeElement, followupHistorySearch);
            fakeDocument.activeElement = followupHistorySearch;
            const reverseTab = new FakeEvent('keydown', { key: 'Tab', shiftKey: true });
            fakeDocument.dispatchEvent(reverseTab);
            assert.equal(reverseTab.defaultPrevented, true);
            assert.equal(fakeDocument.activeElement, historyCloseButton);

            // Escape closes the mobile drawer and returns focus to its opener.
            fakeDocument.dispatchEvent(new FakeEvent('keydown', { key: 'Escape' }));
            assert.equal(followupHistoryRail.hidden, true);
            assert.equal(fakeDocument.activeElement, followupHistoryOpenButton);
            assert.deepEqual(persistedHistoryStates, []);

            // Desktop state follows and updates the persisted preference; mobile state never does.
            mobileMedia.setMatches(false);
            assert.equal(followupHistoryRail.hidden, false);
            assert.deepEqual(persistedHistoryStates, []);
            followupHistoryToggle.click();
            assert.equal(followupHistoryRail.hidden, true);
            assert.deepEqual(persistedHistoryStates, ['false']);
            followupHistoryOpenButton.click();
            assert.equal(followupHistoryRail.hidden, false);
            assert.deepEqual(persistedHistoryStates, ['false', 'true']);
            mobileMedia.setMatches(true);
            assert.equal(followupHistoryRail.hidden, true);
            assert.deepEqual(persistedHistoryStates, ['false', 'true']);

            // Context alone creates a custom follow-up, not a Quick Action job.
            contextInput.value = 'Synthetic context';
            runQuickActionTrigger.click();
            await settle();
            assert.equal(flushDictationCalls, 1);
            assert.equal(requests[0].url, '/api/v1/transcripts/transcript-synthetic/generate-followup');
            assert.deepEqual(JSON.parse(requests[0].options.body), { prompt_text: 'Synthetic context' });
            assert.equal(contextInput.value, '');

            // Enter remains a newline; Ctrl/Cmd+Enter submits via the primary control.
            const beforePlainEnter = requests.length;
            contextInput.value = 'Keyboard context';
            contextInput.dispatchEvent(new FakeEvent('keydown', { key: 'Enter' }));
            await settle();
            assert.equal(requests.length, beforePlainEnter);
            contextInput.dispatchEvent(new FakeEvent('keydown', { key: 'Enter', ctrlKey: true }));
            await settle();
            assert.equal(flushDictationCalls, 2);
            assert.equal(requests.length, beforePlainEnter + 1);
            assert.equal(contextInput.value, '');
            contextInput.value = 'Keyboard context';
            contextInput.dispatchEvent(new FakeEvent('keydown', { key: 'Enter', metaKey: true }));
            await settle();
            assert.equal(flushDictationCalls, 3);
            assert.equal(requests.length, beforePlainEnter + 2);

            // A dictation-save failure aborts custom generation and keeps the steering text.
            contextInput.value = 'Keep custom steering after dictation save failure';
            const requestsBeforeCustomDictationFailure = requests.length;
            failNextDictationFlush = true;
            runQuickActionTrigger.click();
            await settle();
            assert.equal(flushDictationCalls, 4);
            assert.equal(requests.length, requestsBeforeCustomDictationFailure);
            assert.equal(contextInput.value, 'Keep custom steering after dictation save failure');

            // A selected Quick Action routes to its dedicated endpoint and survives a successful queue.
            quickActionSelect.value = 'quick-action-synthetic';
            contextInput.value = 'Use concise language';
            runQuickActionTrigger.click();
            await settle();
            assert.equal(flushDictationCalls, 5);
            const quickActionRequest = requests.at(-1);
            assert.equal(quickActionRequest.url, '/api/v1/transcripts/transcript-synthetic/run-quick-action');
            assert.deepEqual(JSON.parse(quickActionRequest.options.body), {
              quick_action_id: 'quick-action-synthetic',
              context_text: 'Use concise language',
            });
            assert.equal(contextInput.value, '');
            assert.equal(quickActionSelect.value, 'quick-action-synthetic');

            // A failed queue leaves the clinician's steering in place.
            contextInput.value = 'Keep this steering after failure';
            failNextRequest = true;
            runQuickActionTrigger.click();
            await settle();
            assert.equal(flushDictationCalls, 6);
            assert.equal(contextInput.value, 'Keep this steering after failure');

            // A dictation-save failure aborts Quick Action generation and keeps the steering text.
            contextInput.value = 'Keep quick action steering after dictation save failure';
            const requestsBeforeQuickActionDictationFailure = requests.length;
            failNextDictationFlush = true;
            runQuickActionTrigger.click();
            await settle();
            assert.equal(flushDictationCalls, 7);
            assert.equal(requests.length, requestsBeforeQuickActionDictationFailure);
            assert.equal(contextInput.value, 'Keep quick action steering after dictation save failure');
            assert.equal(availabilityDrafts.at(-1), liveDraftText);

            // Regeneration first flushes fresh dictation before it can queue a new document.
            contextInput.value = 'Regenerate with another tone';
            const requestsBeforeHeldRegeneration = requests.length;
            holdNextDictationFlush = true;
            regenerateButton.click();
            await settle();
            assert.equal(flushDictationCalls, 8);
            assert.equal(requests.length, requestsBeforeHeldRegeneration);
            releaseDictationFlush();
            await settle();
            assert.equal(requests.at(-1).url, '/api/v1/generated-documents/generated-synthetic/regenerate');
            assert.deepEqual(JSON.parse(requests.at(-1).options.body), { steering_text: 'Regenerate with another tone' });

            // A dictation-save failure must not queue regeneration or clear fresh steering.
            contextInput.value = 'Keep this steering after dictation save failure';
            const requestsBeforeDictationFailure = requests.length;
            failNextDictationFlush = true;
            regenerateButton.click();
            await settle();
            assert.equal(flushDictationCalls, 9);
            assert.equal(requests.length, requestsBeforeDictationFailure);
            assert.equal(contextInput.value, 'Keep this steering after dictation save failure');

            // A queue failure also keeps steering for a retry.
            failNextRequest = true;
            regenerateButton.click();
            await settle();
            assert.equal(flushDictationCalls, 10);
            assert.equal(contextInput.value, 'Keep this steering after dictation save failure');

            // Regeneration carries null when no new steering is supplied.
            contextInput.value = '   ';
            regenerateButton.click();
            await settle();
            assert.deepEqual(JSON.parse(requests.at(-1).options.body), { steering_text: null });

            // Copy aborts when switching away would discard unsaved Follow Up edits.
            const copyCard = new FakeElement();
            copyCard.dataset.documentId = 'different-followup';
            const copyButton = new FakeElement();
            copyButton.closestMatches['[data-followup-copy]'] = copyButton;
            copyButton.closestMatches['[data-document-id]'] = copyCard;
            const copySelections = [];
            // The production callback returns false when its silent save fails.
            // A second attached handler isolates that failed-selection path.
            const failedCopyHistory = new FakeElement();
            sandbox.attachTranscribeActions({
              dom: { followupHistory: failedCopyHistory, latestFollowupOutput },
              getTranscriptId: () => 'transcript-synthetic',
              selectDocumentFromUi: async (_kind, id) => { copySelections.push(id); return false; },
              showFlash: () => {}, showCopyToast: () => { throw new Error('Copy must not run'); },
              parseErrorMessage: async (_response, fallback) => fallback,
              fetchWorkspace: async () => {}, pollWorkspace: async () => {}, scheduleWorkspaceRefreshBurst: () => {},
              syncTranscriptTitleIfNeeded: async () => {}, persistPendingEditorsBeforeWorkspaceSwitch: async () => true,
              enqueueTemplateGeneration: async () => null, setVisibleStatus: () => {}, setSessionProgress: () => {},
              setRetryAvailability: () => {}, reflectBackendStatus: () => {}, syncGenerationAvailability: () => {},
              persistUserAppPreferences: async () => {}, handleOutputTemplateChange: () => {}, setMicButtons: () => {},
              setTab: () => {}, structuredEditor: {}, saveWorkingNoteBeforeGeneration: async () => {},
              saveDictationBeforeGeneration: async () => {}, clearWorkingNote: async () => {},
            });
            failedCopyHistory.dispatchEvent(new FakeEvent('click', { target: copyButton }));
            await settle();
            assert.deepEqual(copySelections, ['different-followup']);

            // History labels derive only from current title/source metadata, never the old prompt field.
            const history = new FakeElement();
            const navigator = sandbox.createDocumentNavigator({
              dom: { followupHistory: history },
              helpers: {
                escapeHtml: (value) => String(value || ''),
                renderGeneratedOutput: () => {},
                renderFollowupOutput: () => {},
                renderPiiEntities: () => {},
                renderRedactionDebugPanel: () => {},
                refreshIcons: () => {},
                setTab: () => {},
              },
              getState: () => ({
                workspaceFollowupDocuments: [{
                  id: 'history-synthetic',
                  generator_type: 'quick_action',
                  title: 'Quick action: stale prompt label',
                  source_quick_action_name: 'Synthetic action',
                  follow_up_prompt_text: 'This old field must not label history',
                  status: 'ready',
                  created_at: '2026-01-01T00:00:00+00:00',
                }],
                selectedFollowupDocumentId: 'history-synthetic',
              }),
              setState: () => {},
            });
            navigator.renderSelectedFollowup();
            assert.match(history.children[0].innerHTML, /Synthetic action/);
            assert.doesNotMatch(history.children[0].innerHTML, /old field must not label history/);

            // Follow-up completion must ask the app to recompute availability from the live transcript
            // after both a queued Quick Action and a rejected one.
            assert.ok(availabilityDrafts.length >= 8);
            assert.equal(availabilityDrafts.at(-1), liveDraftText);
            const appSource = fs.readFileSync(appPath, 'utf8');
            assert.ok(appSource.includes(
              'syncGenerationAvailability: () => syncGenerationAvailability(readActiveDraftText().trim())',
            ));
            """
        )
        .replace("__ACTIONS_PATH__", repr(str(root / "app" / "static" / "js" / "transcribe" / "actions.js")))
        .replace("__APP_PATH__", repr(str(root / "app" / "static" / "js" / "transcribe" / "app.js")))
        .replace("__DOCUMENTS_PATH__", repr(str(root / "app" / "static" / "js" / "transcribe" / "documents.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_workspace_tabs_support_keyboard_roving_selection(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "workspace_tab_roving_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const layoutPath = __LAYOUT_PATH__;
            const layoutSource = fs.readFileSync(layoutPath, 'utf8')
              .replace('export function createTranscribeLayout', 'function createTranscribeLayout');

            class FakeEvent {
              constructor(type, init = {}) { Object.assign(this, init); this.type = type; this.defaultPrevented = false; }
              preventDefault() { this.defaultPrevented = true; }
            }
            class FakeElement {
              constructor(target) {
                this.dataset = { tabTrigger: target, tabPanel: target };
                this.listeners = new Map();
                this.classList = { toggle: (_name, active) => { this.active = active; } };
                this.hidden = false;
                this.tabIndex = 0;
              }
              addEventListener(type, handler) {
                const handlers = this.listeners.get(type) || [];
                handlers.push(handler);
                this.listeners.set(type, handlers);
              }
              dispatchEvent(event) {
                for (const handler of this.listeners.get(event.type) || []) handler(event);
              }
              setAttribute(name, value) { this[name] = String(value); }
              focus() { fakeDocument.activeElement = this; }
            }
            const fakeDocument = { activeElement: null };
            const sandbox = {
              document: fakeDocument,
              window: { localStorage: { setItem() {} } },
            };
            vm.createContext(sandbox);
            vm.runInContext(layoutSource, sandbox, { filename: layoutPath });

            const output = new FakeElement('output');
            const followups = new FakeElement('followups');
            const history = new FakeElement('history');
            const currentTab = { value: 'output' };
            const controller = sandbox.createTranscribeLayout({
              dom: {
                shell: { dataset: {}, style: { setProperty() {} } },
                triggers: [output, followups, history],
                panels: [new FakeElement('output'), new FakeElement('followups'), new FakeElement('history')],
                paneToggles: [],
              },
              getCurrentAssistantTab: () => currentTab.value,
              setCurrentAssistantTab: (value) => { currentTab.value = value; },
              getTranscriptId: () => 'synthetic',
              paneStorageKey: 'pane', splitRatioStorageKey: 'ratio',
              initialPaneState: 'normal', initialSplitRatio: 50,
            });
            controller.attach();

            const right = new FakeEvent('keydown', { key: 'ArrowRight' });
            output.dispatchEvent(right);
            assert.equal(right.defaultPrevented, true);
            assert.equal(currentTab.value, 'followups');
            assert.equal(fakeDocument.activeElement, followups);
            assert.equal(followups['aria-selected'], 'true');
            assert.equal(followups.tabIndex, 0);
            assert.equal(output.tabIndex, -1);

            const end = new FakeEvent('keydown', { key: 'End' });
            followups.dispatchEvent(end);
            assert.equal(currentTab.value, 'history');
            assert.equal(fakeDocument.activeElement, history);

            const home = new FakeEvent('keydown', { key: 'Home' });
            history.dispatchEvent(home);
            assert.equal(currentTab.value, 'output');
            assert.equal(fakeDocument.activeElement, output);

            const left = new FakeEvent('keydown', { key: 'ArrowLeft' });
            output.dispatchEvent(left);
            assert.equal(currentTab.value, 'history');
            assert.equal(fakeDocument.activeElement, history);
            """
        ).replace("__LAYOUT_PATH__", repr(str(root / "app" / "static" / "js" / "transcribe" / "layout.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
