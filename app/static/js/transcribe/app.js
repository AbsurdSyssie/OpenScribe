import { attachTranscribeActions } from './actions.js';
import { readTranscribeBootstrap } from './bootstrap.js';
import { createDocumentNavigator } from './documents.js';
import { createTranscribeLayout } from './layout.js';
import { createAudioCaptureController } from './media.js';
import { createStructuredEditor } from './structured.js';
import { createGuidedTour } from './tour.js';

      const bootstrap = readTranscribeBootstrap();
      const shell = document.querySelector('[data-workspace-endpoint]');
      const routeBase = shell?.dataset.routeBase || '/transcribe-glm-2';
      const triggers = [...document.querySelectorAll('[data-tab-trigger]')];
      const panels = [...document.querySelectorAll('[data-tab-panel]')];
      const paneToggles = [...document.querySelectorAll('[data-pane-toggle]')];
      const dividerGrip = document.querySelector('[data-divider-grip]');
      const splitWorkspace = document.querySelector('[data-split-workspace]');
      const initialWorkspaceEndpoint = shell?.dataset.workspaceEndpoint || '';
      const initialWorkspaceStreamEndpoint = shell?.dataset.workspaceStreamEndpoint || '';
      let transcriptId = bootstrap.activeTranscriptId;
      let activeIngestionMode = bootstrap.activeIngestionMode;
      let nextLiveChunkSequenceNo = bootstrap.nextLiveChunkSequenceNo;
      let hasSttSelection = bootstrap.hasSttSelection;
      let hasLlmSelection = bootstrap.hasLlmSelection;
      let sttAvailable = bootstrap.sttAvailable;
      let sttStatusMessage = bootstrap.sttStatusMessage;
      let latestIngestionJobStatus = bootstrap.latestIngestionJobStatus;
      let latestIngestionErrorMessage = bootstrap.latestIngestionErrorMessage;
      let currentTranscriptStatus = null;
      let workspaceEventSource = null;
      let workspaceEventSourceEndpoint = null;
      let workspaceStreamFallbackPolling = false;
      let workspaceNoteDocuments = [];
      let workspaceFollowupDocuments = [];
      let workspaceStructuredContext = {};
      let selectedNoteDocumentId = null;
      let selectedFollowupDocumentId = null;
      const showRedactionDebug = bootstrap.showRedactionDebug;
      const initialTranscriptErrorMessage = bootstrap.initialTranscriptErrorMessage;

      const activeStatus = document.querySelector('[data-active-status]');
      const activeIngestionModeChip = document.querySelector('[data-active-ingestion-mode]');
      currentTranscriptStatus = activeStatus?.textContent?.trim() || null;
      const sessionTitleDisplay = document.querySelector('[data-session-title-display]');
      const activeDraft = document.querySelector('[data-active-draft]');
      const transcriptStats = document.querySelector('[data-transcript-stats]');
      const activeProgress = document.querySelector('[data-session-progress]');
      const micStatus = document.querySelector('[data-mic-status]');
      const micTimer = document.querySelector('[data-mic-timer]');
      const flashWrap = document.querySelector('[data-flash-wrap]');
      const flashBanner = document.querySelector('[data-flash]');
      const latestGeneratedOutput = document.querySelector('[data-latest-generated-output]');
      const generatedStructuredPanel = document.querySelector('[data-generated-structured-panel]');
      const generatedStructuredSections = document.querySelector('[data-generated-structured-sections]');
      const latestFollowupOutput = document.querySelector('[data-latest-followup-output]');
      const noteSelectorWrap = document.querySelector('[data-note-selector-wrap]');
      const noteSelector = document.querySelector('[data-note-selector]');
      const noteSelectorCount = document.querySelector('[data-note-selector-count]');
      const followupSelectorWrap = document.querySelector('[data-followup-selector-wrap]');
      const followupSelector = document.querySelector('[data-followup-selector]');
      const followupSelectorCount = document.querySelector('[data-followup-selector-count]');
      const noteMeta = document.querySelector('[data-note-meta]');
      const followupMeta = document.querySelector('[data-followup-meta]');
      const noteHistory = document.querySelector('[data-note-history]');
      const followupHistory = document.querySelector('[data-followup-history]');
      const outputRedactionSlot = document.querySelector('[data-output-redaction-debug-slot]');
      const followupRedactionSlot = document.querySelector('[data-followup-redaction-debug-slot]');
      const copyStructuredLinesButton = document.querySelector('[data-copy-structured-lines]');
      const clearStructuredSelectionButton = document.querySelector('[data-clear-structured-selection]');
      const structuredCopyStatus = document.querySelector('[data-structured-copy-status]');
      const copyTranscriptButton = document.querySelector('[data-copy-transcript]');
      const templateModeBadge = document.querySelector('[data-selected-template-mode]');
      const structuredContextHiddenInputs = [...document.querySelectorAll('[data-structured-context-hidden]')];
      const sessionLinks = [...document.querySelectorAll('[data-session-link]')];
      const selectionBoxes = [...document.querySelectorAll('[data-session-select]')];
      const deleteButton = document.querySelector('[data-delete-selected]');
      const newSessionButton = document.querySelector('[data-new-session-button]');
      const newSessionBlockMessage = document.querySelector('[data-new-session-block-message]');
      const uploadForm = document.querySelector('[data-upload-form]');
      const fileInput = document.querySelector('[data-audio-file-input]');
      const retryIngestionForm = document.querySelector('[data-retry-ingestion-form]');
      const retryIngestionTrigger = document.querySelector('[data-retry-ingestion-trigger]');
      const retryTranscriptIdInput = document.querySelector('[data-retry-transcript-id]');
      const newSessionForm = document.querySelector('#new-session-form');
      const bulkDeleteForm = document.querySelector('#bulk-delete-sessions');
      const titleForm = document.querySelector('[data-transcript-title-form]');
      const renameTitleInput = document.querySelector('[data-transcript-title-input]');
      const generateOutputForm = document.querySelector('[data-generate-output-form]');
      const generateOutputTemplateSelect = generateOutputForm?.querySelector('select[name="template_id"]') || null;
      const generateFollowupForm = document.querySelector('[data-generate-followup-form]');
      const runQuickActionForm = document.querySelector('[data-run-quick-action-form]');
      const runQuickActionSelect = runQuickActionForm?.querySelector('select[name="quick_action_id"]') || null;
      const workspaceSettingsLink = document.querySelector('[data-workspace-settings-link]');
      const audioActionTrigger = document.querySelector('[data-audio-action-trigger]');
      const recordingModeSelect = document.querySelector('[data-recording-mode-select]');
      const recordToggleButton = document.querySelector('[data-record-toggle]');
      const recordToggleLabel = document.querySelector('[data-record-toggle-label]');
      const recordToggleIcon = document.querySelector('[data-record-toggle-icon]');
      const localBusyProtected = [...document.querySelectorAll('[data-local-busy-protected]')];
      const newSessionControls = [newSessionButton].filter(Boolean);
      const copyToast = document.querySelector('#copy-toast');
      const guideStartButtons = [...document.querySelectorAll('[data-start-guide]')];
      const tourOverlay = document.querySelector('[data-tour-overlay]');
      const tourScrims = {
        top: document.querySelector('[data-tour-scrim="top"]'),
        right: document.querySelector('[data-tour-scrim="right"]'),
        bottom: document.querySelector('[data-tour-scrim="bottom"]'),
        left: document.querySelector('[data-tour-scrim="left"]'),
      };
      const tourHighlight = document.querySelector('[data-tour-highlight]');
      const tourCard = document.querySelector('[data-tour-card]');
      const tourTitle = document.querySelector('[data-tour-title]');
      const tourBody = document.querySelector('[data-tour-body]');
      const tourProgress = document.querySelector('[data-tour-progress]');
      const tourBackButton = document.querySelector('[data-tour-back]');
      const tourNextButton = document.querySelector('[data-tour-next]');
      const tourCloseButtons = [...document.querySelectorAll('[data-tour-close], [data-tour-close-button]')];

      selectedNoteDocumentId = latestGeneratedOutput?.dataset.latestGeneratedId || null;
      selectedFollowupDocumentId = latestFollowupOutput?.dataset.latestFollowupId || null;

      let currentTranscriptTitle = (renameTitleInput?.value || '').trim();
      let captureController = null;
      let structuredEditor = null;
      let workspaceRefreshBurstTimeoutIds = [];
      const protectedInitialDisabled = new Map(localBusyProtected.map((button) => [button, button.disabled]));
      const liveVadBundleVersion = '0.0.29';
      const liveVadModel = 'v5';
      const liveVadAssetBasePath = `https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@${liveVadBundleVersion}/dist/`;
      const liveVadOnnxBasePath = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/';
      const liveVadSampleRate = 16000;
      const livePreRollMs = 800;
      const livePostRollTrimMs = 1000;
      const liveSilenceThresholdMs = 2000;
      const liveMaxChunkMs = 30000;
      const liveMinChunkMs = 400;
      const liveChunkOverlapMs = 800;
      const liveRestartDelayMs = 150;
      const structuredSectionDefinitions = bootstrap.emisSections;

      let currentAssistantTab = bootstrap.activeTab;
      const paneStorageKey = 'openscribe-glm2-pane-state';
      const splitRatioStorageKey = 'openscribe-glm2-split-ratio';
      const transcribeTourStorageKey = `openscribe:tour:transcribe:${bootstrap.viewerRole}`;
      const initialPaneState = window.localStorage.getItem(paneStorageKey) || shell?.dataset.layoutState || 'normal';
      const initialSplitRatio = Number.parseFloat(window.localStorage.getItem(splitRatioStorageKey) || '');

      const friendlyModeLabel = (mode) => mode === 'live_chunked' ? 'live capture' : 'recorded upload';

      const syncRecordingModeControl = (mode = activeIngestionMode) => {
        if (!recordingModeSelect) return;
        recordingModeSelect.value = mode === 'live_chunked' ? 'live_chunked' : 'whole_file';
      };

      const layoutController = createTranscribeLayout({
        dom: {
          dividerGrip,
          generateOutputTemplateSelect,
          paneToggles,
          panels,
          runQuickActionSelect,
          shell,
          splitWorkspace,
          triggers,
          workspaceSettingsLink,
        },
        getCurrentAssistantTab: () => currentAssistantTab,
        setCurrentAssistantTab: (value) => {
          currentAssistantTab = value;
        },
        getTranscriptId: () => transcriptId,
        paneStorageKey,
        splitRatioStorageKey,
        initialPaneState,
        initialSplitRatio,
      });
      const setTab = layoutController.setTab;

      const showFlash = (message, kind = 'success') => {
        if (!flashBanner) return;
        if (flashWrap) {
          flashWrap.hidden = !message;
        }
        flashBanner.hidden = !message;
        flashBanner.textContent = message || '';
        flashBanner.classList.remove('success', 'error');
        flashBanner.classList.add(kind === 'error' ? 'error' : 'success');
      };

      const parseErrorMessage = async (response, fallback) => {
        try {
          const payload = await response.json();
          return payload?.error?.message || fallback;
        } catch (_) {
          return fallback;
        }
      };

      const showCopyToast = () => {
        if (!copyToast) return;
        copyToast.classList.add('show');
        window.clearTimeout(showCopyToast._timer);
        showCopyToast._timer = window.setTimeout(() => {
          copyToast.classList.remove('show');
        }, 1400);
      };

      const setVisibleStatus = (label) => {
        if (activeStatus) activeStatus.textContent = label;
        if (transcriptId) {
          const sidebarStatus = document.querySelector(`[data-sidebar-status="${transcriptId}"]`);
          if (sidebarStatus) sidebarStatus.textContent = label;
        }
      };

      const setSessionProgress = (message) => {
        if (activeProgress) {
          activeProgress.textContent = message;
        }
      };

      const setMicStatus = (message, kind = '') => {
        if (!micStatus) return;
        micStatus.textContent = message;
        micStatus.classList.remove('text-coral', 'text-success');
        if (kind === 'error') {
          micStatus.classList.add('text-coral');
        } else if (kind === 'success') {
          micStatus.classList.add('text-success');
        }
      };

      const displayStatusLabel = (statusLabel, ingestionMode) => {
        if (statusLabel === 'recording') {
          return 'idle';
        }
        return statusLabel || 'idle';
      };

      const reflectBackendStatus = (statusLabel, errorMessage = null) => {
        currentTranscriptStatus = statusLabel || null;
        const visibleStatus = displayStatusLabel(statusLabel, activeIngestionMode);
        setVisibleStatus(visibleStatus);
        if (visibleStatus === 'queued') {
          setSessionProgress('Waiting to be turned into text.');
        } else if (visibleStatus === 'transcribing') {
          setSessionProgress('Turning your recording into text.');
        } else if (visibleStatus === 'ready') {
          setSessionProgress('Ready to review.');
        } else if (visibleStatus === 'failed') {
          setSessionProgress(`The last attempt to turn the recording into text failed${errorMessage ? `: ${errorMessage}` : ''}.`);
        } else if (visibleStatus === 'recording') {
          setSessionProgress('Recording on this device. Nothing has been sent yet.');
        } else if (visibleStatus === 'uploading') {
          setSessionProgress('Uploading your recording...');
        } else {
          setSessionProgress('Ready when you are.');
        }
      };

      const clearWorkspaceRefreshBurst = () => {
        workspaceRefreshBurstTimeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
        workspaceRefreshBurstTimeoutIds = [];
      };

      const scheduleWorkspaceRefreshBurst = ({ attempts = 25, intervalMs = 1500 } = {}) => {
        clearWorkspaceRefreshBurst();
        for (let index = 1; index <= attempts; index += 1) {
          const timeoutId = window.setTimeout(() => {
            workspaceRefreshBurstTimeoutIds = workspaceRefreshBurstTimeoutIds.filter((value) => value !== timeoutId);
            void fetchWorkspace();
          }, intervalMs * index);
          workspaceRefreshBurstTimeoutIds.push(timeoutId);
        }
      };

      const renderTranscriptStats = (text) => {
        if (!transcriptStats) return;
        if (!text || !text.trim()) {
          transcriptStats.textContent = '0 blocks - 0 words';
          return;
        }
        const blocks = text.split('\n').filter((line) => line.trim().length > 0).length || 1;
        const words = text.trim().split(/\s+/).filter(Boolean).length;
        transcriptStats.textContent = `${blocks} blocks - ${words} words`;
      };

      const renderDraft = (text) => {
        if (!activeDraft) return;
        if (text && text.trim()) {
          activeDraft.textContent = text;
        } else {
          activeDraft.innerHTML = '<span class="text-slate">No transcript text yet. Upload a recording or use the microphone to begin.</span>';
        }
        renderTranscriptStats(text || '');
      };

      const canUseWholeFileInput = () => Boolean(transcriptId && hasSttSelection && sttAvailable && activeIngestionMode === 'whole_file');

      const setRetryAvailability = (canRetry) => {
        if (retryIngestionForm) {
          retryIngestionForm.hidden = !canRetry;
        }
        if (retryIngestionTrigger) {
          retryIngestionTrigger.dataset.retryAvailable = canRetry ? 'true' : 'false';
          retryIngestionTrigger.hidden = !canRetry;
          retryIngestionTrigger.disabled = !canRetry;
        }
        if (retryTranscriptIdInput) {
          retryTranscriptIdInput.value = transcriptId || '';
        }
      };

      const canUseLiveInput = () => Boolean(transcriptId && hasSttSelection && sttAvailable && activeIngestionMode === 'live_chunked');

      const defaultMicStatusState = () => {
        if (!transcriptId) return { message: 'Create or open a consultation to begin.', kind: '' };
        if (activeIngestionMode === 'live_chunked') {
          if (latestIngestionJobStatus === 'failed' && latestIngestionErrorMessage) {
            return { message: latestIngestionErrorMessage, kind: 'error' };
          }
          if (!hasSttSelection) {
            return { message: 'Live capture is not ready for your team yet.', kind: 'error' };
          }
          if (!sttAvailable) {
            return { message: sttStatusMessage || 'Live capture is not available right now.', kind: 'error' };
          }
          return { message: 'Ready for live capture. Press Start live capture when you are ready.', kind: '' };
        }
        if (activeIngestionMode !== 'whole_file') return { message: `This consultation is in ${friendlyModeLabel(activeIngestionMode)}.`, kind: '' };
        if (!hasSttSelection) {
          return { message: 'Uploading recordings is not ready for your team yet.', kind: 'error' };
        }
        if (!sttAvailable) {
          return { message: sttStatusMessage || 'Uploading recordings is not available right now.', kind: 'error' };
        }
        return { message: 'Ready to upload a recording or capture one from your microphone.', kind: '' };
      };

      const hasSelectableOptions = (select) => {
        if (!select) return false;
        return [...select.options].some((option) => option.value);
      };

      const hasStructuredContextContent = () => {
        const structuredContext = structuredEditor?.collectStructuredContext() || {};
        return Object.values(structuredContext).some((lines) => Array.isArray(lines) && lines.length > 0);
      };

      const syncGenerationAvailability = (draftText = '') => {
        const hasDraft = Boolean(draftText && draftText.trim());
        const hasStructuredInput = structuredEditor?.selectedOutputTemplateMode() === 'structured' && hasStructuredContextContent();
        const canGenerateNote = Boolean(transcriptId && hasLlmSelection && hasSelectableOptions(generateOutputTemplateSelect) && (hasDraft || hasStructuredInput));
        const canRunQuickAction = Boolean(transcriptId && hasLlmSelection && hasDraft && hasSelectableOptions(runQuickActionForm?.querySelector('select[name="quick_action_id"]')));
        const canGenerateFollowup = Boolean(transcriptId && hasLlmSelection && hasDraft);

        if (generateOutputTemplateSelect) {
          generateOutputTemplateSelect.disabled = !canGenerateNote;
        }
        const generateOutputButton = generateOutputForm?.querySelector('button[type="submit"]');
        if (generateOutputButton) {
          generateOutputButton.disabled = !canGenerateNote;
        }

        const quickActionSelect = runQuickActionForm?.querySelector('select[name="quick_action_id"]');
        if (quickActionSelect) {
          quickActionSelect.disabled = !canRunQuickAction;
        }
        const quickActionButton = runQuickActionForm?.querySelector('button[type="submit"]');
        if (quickActionButton) {
          quickActionButton.disabled = !canRunQuickAction;
        }

        const followupTextarea = generateFollowupForm?.querySelector('textarea[name="prompt_text"]');
        if (followupTextarea) {
          followupTextarea.disabled = !canGenerateFollowup;
        }
        const followupButton = generateFollowupForm?.querySelector('button[type="submit"]');
        if (followupButton) {
          followupButton.disabled = !canGenerateFollowup;
        }
      };

      const setMicButtons = (isRecording) => {
        const liveMode = activeIngestionMode === 'live_chunked';
        const canUseRecorder = liveMode ? canUseLiveInput() : canUseWholeFileInput();
        localBusyProtected.forEach((button) => {
          const initiallyDisabled = protectedInitialDisabled.get(button) ?? false;
          if (button === retryIngestionTrigger) {
            const retryAvailable = button.dataset.retryAvailable === 'true';
            button.disabled = isRecording || !retryAvailable;
            return;
          }
          button.disabled = isRecording || initiallyDisabled;
        });
        if (audioActionTrigger) {
          audioActionTrigger.hidden = liveMode;
          audioActionTrigger.disabled = isRecording || liveMode || !canUseWholeFileInput();
        }
        if (recordingModeSelect) {
          recordingModeSelect.disabled = isRecording || !transcriptId;
          recordingModeSelect.title = !transcriptId ? 'Create or open a consultation to choose the recording mode.' : '';
          syncRecordingModeControl();
        }
        if (fileInput) {
          fileInput.disabled = isRecording || liveMode || !canUseWholeFileInput();
        }
        if (recordToggleButton) {
          recordToggleButton.disabled = !isRecording && !canUseRecorder;
          recordToggleButton.title = (!isRecording && !canUseRecorder && !sttAvailable && sttStatusMessage) ? sttStatusMessage : '';
        }
        if (recordToggleLabel) {
          if (isRecording) {
            recordToggleLabel.textContent = 'Stop';
          } else {
            recordToggleLabel.textContent = liveMode ? 'Start live capture' : 'Start recording';
          }
        }
        if (recordToggleIcon) {
          recordToggleIcon.innerHTML = isRecording
            ? '<rect x="7" y="7" width="10" height="10" rx="2"></rect>'
            : (liveMode
              ? '<path d="M12 3v18M3 12h18"></path><circle cx="12" cy="12" r="8"></circle>'
              : '<circle cx="12" cy="12" r="6"></circle>');
        }
        if (audioActionTrigger) {
          audioActionTrigger.title = (!isRecording && !canUseWholeFileInput() && !sttAvailable && sttStatusMessage) ? sttStatusMessage : '';
        }
        if (fileInput) {
          fileInput.title = (!isRecording && !canUseWholeFileInput() && !sttAvailable && sttStatusMessage) ? sttStatusMessage : '';
        }
      };

      const setNewSessionAvailability = (canCreate, message) => {
        newSessionControls.forEach((button) => {
          button.disabled = !canCreate;
          if (canCreate || !message) {
            button.removeAttribute('title');
          } else {
            button.title = message;
          }
        });
        if (newSessionBlockMessage) {
          const shouldShow = !canCreate && Boolean(message);
          newSessionBlockMessage.textContent = message || '';
          newSessionBlockMessage.classList.toggle('hidden', !shouldShow);
        }
      };

      const syncTranscriptTitleIfNeeded = async () => {
        if (!transcriptId || !renameTitleInput) return;
        const nextTitle = renameTitleInput.value.trim();
        if (nextTitle === currentTranscriptTitle) return;
        const response = await fetch(`/api/v1/transcripts/${transcriptId}`, {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: nextTitle || null }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not update the session title.'));
        }
        currentTranscriptTitle = nextTitle;
      };

        captureController = createAudioCaptureController({
        dom: {
          audioActionTrigger,
          fileInput,
          micTimer,
          recordToggleButton,
          uploadForm,
        },
        config: {
          liveChunkOverlapMs,
          liveMaxChunkMs,
          liveMinChunkMs,
          liveOnnxBasePath: liveVadOnnxBasePath,
          livePostRollTrimMs,
          livePreRollMs,
          liveRestartDelayMs,
          liveSilenceThresholdMs,
          liveVadAssetBasePath,
          liveVadModel,
          liveVadOnnxBasePath,
          liveVadSampleRate,
        },
        canUseLiveInput,
        canUseWholeFileInput,
        getState: () => ({
          transcriptId,
          activeIngestionMode,
          nextLiveChunkSequenceNo,
          latestIngestionJobStatus,
        }),
        setNextLiveChunkSequenceNo: (value) => {
          nextLiveChunkSequenceNo = value;
        },
        getDefaultMicStatusState: defaultMicStatusState,
        syncTranscriptTitleIfNeeded,
        fetchWorkspace,
        pollWorkspace,
        scheduleWorkspaceRefreshBurst,
        parseErrorMessage,
        setMicButtons,
        setMicStatus,
        setVisibleStatus,
        setSessionProgress,
        setRetryAvailability,
        showFlash,
        reflectBackendStatus,
      });

      const syncDeleteState = () => {
        if (!deleteButton) return;
        deleteButton.disabled = !selectionBoxes.some((checkbox) => checkbox.checked);
        selectionBoxes.forEach((checkbox) => {
          checkbox.closest('.session-item')?.classList.toggle('selected', checkbox.checked);
        });
      };

      selectionBoxes.forEach((checkbox) => {
        checkbox.addEventListener('change', syncDeleteState);
      });
      syncDeleteState();
      structuredEditor = createStructuredEditor({
        dom: {
          generateOutputTemplateSelect,
          generatedStructuredPanel,
          generatedStructuredSections,
          latestGeneratedOutput,
          structuredContextHiddenInputs,
          structuredCopyStatus,
          templateModeBadge,
        },
        structuredSectionDefinitions,
        getTranscriptId: () => transcriptId,
        getDraftText: () => activeDraft?.textContent?.trim() || '',
        syncGenerationAvailability,
        persistStructuredContextSilently: async () => saveStructuredContext({ silent: true }),
      });
      structuredEditor.bootstrapFromDom();

      const renderFollowupOutput = (document) => {
        if (!latestFollowupOutput) return;
        if (!document) {
          latestFollowupOutput.innerHTML = '<span class="text-slate">No messages yet.</span>';
          return;
        }
        if (document.status === 'ready' && document.edited_output_text_encrypted) {
          latestFollowupOutput.textContent = document.edited_output_text_encrypted;
          return;
        }
        if (document.status === 'queued') {
          latestFollowupOutput.innerHTML = '<span class="text-slate">Your message is waiting to be written.</span>';
          return;
        }
        if (document.status === 'processing') {
          latestFollowupOutput.innerHTML = '<span class="text-slate">Your message is being written.</span>';
          return;
        }
        if (document.status === 'failed') {
          latestFollowupOutput.innerHTML = `<span class="text-slate">The latest message could not be created${document.error_message ? `: ${document.error_message}` : ''}.</span>`;
          return;
        }
        latestFollowupOutput.innerHTML = '<span class="text-slate">No messages yet.</span>';
      };

      const renderRedactionDebugPanel = (slot, document) => {
        if (!slot) return;
        slot.innerHTML = '';
        if (!showRedactionDebug || !document) return;
        const wrapper = document.createElement('details');
        wrapper.className = 'border border-stone bg-parchment/40 p-3';
        wrapper.setAttribute('data-redaction-debug-panel', '');
        wrapper.dataset.generatedDocumentId = document.id;
        wrapper.innerHTML = `
          <summary class="cursor-pointer text-sm font-medium text-ink">Dev redaction debug</summary>
          <div class="text-xs text-slate mt-3" data-redaction-debug-meta>Open to load debug data.</div>
          <pre class="mt-3 text-xs whitespace-pre-wrap text-slate overflow-x-auto" data-redaction-debug-text>Redaction debug not loaded yet.</pre>
        `;
        slot.appendChild(wrapper);
        wrapper.addEventListener('toggle', () => {
          if (wrapper.open) {
            loadRedactionDebug(wrapper);
          }
        });
      };

      const escapeHtml = (value) => {
        return String(value ?? '')
          .replaceAll('&', '&amp;')
          .replaceAll('<', '&lt;')
          .replaceAll('>', '&gt;')
          .replaceAll('"', '&quot;')
          .replaceAll("'", '&#39;');
      };

      const {
        selectedDocumentFromList,
        selectDocumentFromUi,
        renderSelectedNote,
        renderSelectedFollowup,
      } = createDocumentNavigator({
        dom: {
          noteSelectorWrap,
          noteSelector,
          noteSelectorCount,
          followupSelectorWrap,
          followupSelector,
          followupSelectorCount,
          noteMeta,
          followupMeta,
          noteHistory,
          followupHistory,
          latestGeneratedOutput,
          latestFollowupOutput,
          outputRedactionSlot,
          followupRedactionSlot,
        },
        helpers: {
          escapeHtml,
          renderGeneratedOutput: (...args) => structuredEditor.renderGeneratedOutput(...args),
          renderFollowupOutput,
          renderRedactionDebugPanel,
          setTab,
        },
        getState: () => ({
          workspaceNoteDocuments,
          workspaceFollowupDocuments,
          workspaceStructuredContext,
          selectedNoteDocumentId,
          selectedFollowupDocumentId,
        }),
        setState: (nextState) => {
          if (Object.prototype.hasOwnProperty.call(nextState, 'selectedNoteDocumentId')) {
            selectedNoteDocumentId = nextState.selectedNoteDocumentId;
          }
          if (Object.prototype.hasOwnProperty.call(nextState, 'selectedFollowupDocumentId')) {
            selectedFollowupDocumentId = nextState.selectedFollowupDocumentId;
          }
        },
      });

      const saveStructuredContext = async ({ silent = false } = {}) => {
        if (!transcriptId) return null;
        const structuredContext = structuredEditor.collectStructuredContext();
        const serializedContext = JSON.stringify(structuredContext);
        if (structuredEditor.getLastSavedStructuredContext() === serializedContext) {
          return structuredContext;
        }
        const response = await fetch(`/api/v1/transcripts/${transcriptId}`, {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ structured_context_json: { profile: 'emis', sections: structuredContext } }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not save EMIS session context.'));
        }
        structuredEditor.setLastSavedStructuredContext(serializedContext);
        return structuredContext;
      };

      const workspaceEndpointForTranscript = (nextTranscriptId) => {
        if (nextTranscriptId) {
          return `/api/v1/transcribe/workspace?transcript_id=${nextTranscriptId}`;
        }
        return initialWorkspaceEndpoint || '/api/v1/transcribe/workspace';
      };

      const workspaceStreamEndpointForTranscript = (nextTranscriptId) => {
        if (nextTranscriptId) {
          return `/api/v1/transcribe/workspace/stream?transcript_id=${nextTranscriptId}`;
        }
        return initialWorkspaceStreamEndpoint || '/api/v1/transcribe/workspace/stream';
      };

      const closeWorkspaceEventSource = () => {
        if (!workspaceEventSource) return;
        workspaceEventSource.close();
        workspaceEventSource = null;
        workspaceEventSourceEndpoint = null;
      };

      const shouldUseWorkspacePollingFallback = () => {
        return !window.EventSource || workspaceStreamFallbackPolling || !workspaceEventSource;
      };

      const syncWorkspaceRealtimeConnection = () => {
        if (!window.EventSource) return;
        const endpoint = workspaceStreamEndpointForTranscript(transcriptId);
        if (!endpoint) {
          closeWorkspaceEventSource();
          return;
        }
        if (workspaceEventSource && workspaceEventSourceEndpoint === endpoint) {
          return;
        }
        closeWorkspaceEventSource();
        workspaceStreamFallbackPolling = false;
        workspaceEventSourceEndpoint = endpoint;
        workspaceEventSource = new window.EventSource(endpoint);
        workspaceEventSource.addEventListener('open', () => {
          workspaceStreamFallbackPolling = false;
        });
        workspaceEventSource.addEventListener('workspace', (event) => {
          try {
            const workspace = JSON.parse(event.data);
            applyWorkspacePayload(workspace);
          } catch (_) {}
        });
        workspaceEventSource.onerror = () => {
          workspaceStreamFallbackPolling = true;
          window.setTimeout(pollWorkspace, 1200);
        };
      };

      const loadRedactionDebug = async (panel) => {
        if (!panel || panel.dataset.loaded === 'true' || panel.dataset.loading === 'true') return;
        const generatedDocumentId = panel.dataset.generatedDocumentId;
        const meta = panel.querySelector('[data-redaction-debug-meta]');
        const text = panel.querySelector('[data-redaction-debug-text]');
        if (!generatedDocumentId || !meta || !text) return;
        panel.dataset.loading = 'true';
        meta.textContent = 'Loading redaction debug...';
        try {
          const response = await fetch(`/api/v1/generated-documents/${generatedDocumentId}/redaction-debug`, {
            credentials: 'include',
          });
          if (!response.ok) {
            throw new Error('Could not load redaction debug.');
          }
          const payload = await response.json();
          const placeholderLines = (payload.entities || []).map((entity) => `${entity.placeholder} · ${entity.entity_type} · occurrences ${entity.occurrence_count}`);
          const failedOutputBlock = payload.failed_provider_output_redacted_text
            ? `\n\nFailed raw provider output (redacted):\n${payload.failed_provider_output_redacted_text}`
            : '';
          meta.textContent = `Redaction applied via ${payload.api_provider}${payload.api_model_or_version ? ` (${payload.api_model_or_version})` : ''} · ${payload.entity_count} entities`;
          text.textContent = `${payload.redacted_text}\n\nPlaceholders:\n${placeholderLines.join('\n') || 'None'}${failedOutputBlock}`;
          panel.dataset.loaded = 'true';
        } catch (_) {
          meta.textContent = 'Could not load redaction debug.';
          text.textContent = 'The debug payload is unavailable for this generated document.';
        } finally {
          panel.dataset.loading = 'false';
        }
      };

      const applyWorkspacePayload = (workspace) => {
        if (!workspace || typeof workspace !== 'object') return;
        const transcript = workspace.active_transcript || null;
        const generatedDocuments = Array.isArray(workspace.generated_documents) ? workspace.generated_documents : [];
        const noteDocuments = generatedDocuments.filter((document) => document.generator_type === 'template');
        const followupDocuments = generatedDocuments.filter((document) => document.generator_type === 'followup' || document.generator_type === 'quick_action');
        const sidebarTranscripts = Array.isArray(workspace.recent_transcripts) ? workspace.recent_transcripts : [];

        transcriptId = transcript?.id || null;
        currentTranscriptStatus = transcript?.status || null;
        activeIngestionMode = transcript?.ingestion_mode || null;
        nextLiveChunkSequenceNo = transcript?.next_live_chunk_sequence_no_upload || 1;
        hasSttSelection = Boolean(workspace.stt_selected);
        sttAvailable = Boolean(workspace.stt_available);
        sttStatusMessage = workspace.stt_status_message || null;
        latestIngestionJobStatus = transcript?.latest_ingestion_job_status || null;
        latestIngestionErrorMessage = transcript?.latest_ingestion_error_message || null;
        const retryAvailable = Boolean(
          transcript
          && transcript.latest_ingestion_retry_available
          && workspace.stt_selected
          && workspace.stt_available
          && transcript.ingestion_mode === 'whole_file'
          && transcript.status === 'failed'
        );
        setNewSessionAvailability(Boolean(workspace.can_create_new_session), workspace.new_session_block_message || '');
        setRetryAvailability(retryAvailable);

        sessionLinks.forEach((link) => {
          const isActive = link.dataset.transcriptId === transcriptId;
          link.classList.toggle('active', isActive);
          link.closest('.session-item')?.classList.toggle('active', isActive);
        });

        sidebarTranscripts.forEach((item) => {
          const node = document.querySelector(`[data-sidebar-status="${item.id}"]`);
          if (node) node.textContent = displayStatusLabel(item.status, item.ingestion_mode);
          const titleNode = document.querySelector(`[data-session-link][data-transcript-id="${item.id}"] .session-title`);
          if (titleNode) titleNode.textContent = item.title || 'Untitled session';
        });

        if (transcript) {
          reflectBackendStatus(transcript.status, transcript.latest_ingestion_error_message || null);
          const draftText = transcript.current_draft_text_encrypted || '';
          renderDraft(draftText);
          syncGenerationAvailability(draftText);
          if (sessionTitleDisplay) sessionTitleDisplay.value = transcript.title || '';
          if (renameTitleInput) renameTitleInput.value = transcript.title || '';
          currentTranscriptTitle = (transcript.title || '').trim();
          if (activeIngestionModeChip) {
            activeIngestionModeChip.textContent = transcript.ingestion_mode === 'whole_file' ? 'Recorded upload' : 'Live capture';
          }
          document.querySelectorAll('input[name="transcript_id"]').forEach((input) => {
            input.value = transcript.id;
          });
          syncRecordingModeControl(transcript.ingestion_mode);
        } else {
          currentTranscriptStatus = null;
          renderDraft('');
          syncGenerationAvailability('');
          if (sessionTitleDisplay) sessionTitleDisplay.value = '';
          if (renameTitleInput) renameTitleInput.value = '';
          if (activeIngestionModeChip) activeIngestionModeChip.textContent = '';
          syncRecordingModeControl('whole_file');
          latestIngestionJobStatus = null;
          latestIngestionErrorMessage = null;
          setRetryAvailability(false);
          setSessionProgress('Create or open a consultation to begin.');
        }
        const micStatusState = defaultMicStatusState();
        setMicStatus(micStatusState.message, micStatusState.kind);

        const structuredContext = workspace.active_structured_context || {};
        workspaceNoteDocuments = noteDocuments;
        workspaceFollowupDocuments = followupDocuments;
        workspaceStructuredContext = structuredContext;
        selectedNoteDocumentId = selectedDocumentFromList(noteDocuments, selectedNoteDocumentId)?.id || null;
        selectedFollowupDocumentId = selectedDocumentFromList(followupDocuments, selectedFollowupDocumentId)?.id || null;
        renderSelectedNote();
        renderSelectedFollowup();
        structuredEditor.setLastSavedStructuredContext(JSON.stringify(structuredContext));
        const generatedStructuredDraft = structuredEditor.buildGeneratedStructuredDraft(
          selectedDocumentFromList(noteDocuments, selectedNoteDocumentId),
          structuredContext,
        );
        structuredEditor.setGeneratedStructuredDraft(generatedStructuredDraft);
        structuredEditor.renderStructuredSections(generatedStructuredDraft);
        structuredEditor.syncStructuredContextHiddenInputs();
        structuredEditor.syncStructuredEditorAvailability();
        setMicButtons(isLiveCaptureUiActive());
        structuredEditor.syncStructuredTemplateUi();
        if (
          latestIngestionJobStatus === 'queued'
          || latestIngestionJobStatus === 'processing'
          || currentTranscriptStatus === 'uploading'
          || currentTranscriptStatus === 'queued'
          || currentTranscriptStatus === 'transcribing'
          || currentTranscriptStatus === 'processing'
          || noteDocuments.some((document) => document.status === 'queued' || document.status === 'processing')
          || followupDocuments.some((document) => document.status === 'queued' || document.status === 'processing')
        ) {
          scheduleWorkspaceRefreshBurst();
        }
        syncWorkspaceRealtimeConnection();
      };

      async function fetchWorkspace(targetTranscriptId = transcriptId) {
        const endpoint = workspaceEndpointForTranscript(targetTranscriptId);
        if (!endpoint) return null;
        try {
          const response = await fetch(endpoint, { credentials: 'include' });
          if (!response.ok) return null;
          const workspace = await response.json();
          applyWorkspacePayload(workspace);
          return workspace;
        } catch (_) {
          return null;
        }
      }

      const shouldPollWhileLiveCaptureActive = () => {
        return captureController.shouldPollWhileLiveCaptureActive();
      };

      const isLiveCaptureUiActive = () => {
        return captureController.isLiveCaptureUiActive();
      };

      const shouldPreserveLiveMicStatus = () => {
        return captureController.shouldPreserveLiveMicStatus();
      };

      async function pollWorkspace() {
        if (!shouldUseWorkspacePollingFallback()) return;
        if (!transcriptId || !activeStatus) return;
        const liveCaptureActive = shouldPollWhileLiveCaptureActive();
        if (!liveCaptureActive) return;
        const workspace = await fetchWorkspace();
        const keepPollingForLiveCapture = shouldPollWhileLiveCaptureActive();
        if (workspace && keepPollingForLiveCapture) {
          window.setTimeout(pollWorkspace, 1200);
        }
      }

      if (activeStatus) {
        reflectBackendStatus(activeStatus.textContent.trim(), initialTranscriptErrorMessage);
      }
      renderDraft(activeDraft?.textContent?.trim() || '');
      syncGenerationAvailability(activeDraft?.textContent?.trim() || '');
      structuredEditor.syncStructuredTemplateUi();
      layoutController.attach();
      setMicButtons(false);
      {
        if (!(shouldPreserveLiveMicStatus() && !(latestIngestionJobStatus === 'failed' && latestIngestionErrorMessage))) {
          const micStatusState = defaultMicStatusState();
          setMicStatus(micStatusState.message, micStatusState.kind);
        }
      }
      syncWorkspaceRealtimeConnection();
      if (transcriptId) {
        window.setTimeout(() => {
          fetchWorkspace();
        }, 0);
      }

      syncRecordingModeControl(activeIngestionMode || 'whole_file');

      captureController.attachDomListeners();
      attachTranscribeActions({
        dom: {
          activeDraft,
          bulkDeleteForm,
          clearStructuredSelectionButton,
          copyStructuredLinesButton,
          copyTranscriptButton,
          fileInput,
          followupSelector,
          generateFollowupForm,
          generateOutputForm,
          generateOutputTemplateSelect,
          newSessionForm,
          noteSelector,
          recordingModeSelect,
          renameTitleInput,
          runQuickActionForm,
          selectionBoxes,
          sessionLinks,
          structuredCopyStatus,
          titleForm,
          uploadForm,
        },
        routeBase,
        getTranscriptId: () => transcriptId,
        getActiveIngestionMode: () => activeIngestionMode,
        getIsLiveCaptureUiActive: () => isLiveCaptureUiActive(),
        selectDocumentFromUi,
        showFlash,
        showCopyToast,
        parseErrorMessage,
        fetchWorkspace,
        pollWorkspace,
        scheduleWorkspaceRefreshBurst,
        syncTranscriptTitleIfNeeded,
        saveStructuredContext,
        setVisibleStatus,
        setSessionProgress,
        setRetryAvailability,
        reflectBackendStatus,
        setMicButtons,
        setTab,
        structuredEditor,
      });

      createGuidedTour({
        dom: {
          guideStartButtons,
          tourOverlay,
          tourScrims,
          tourHighlight,
          tourCard,
          tourTitle,
          tourBody,
          tourProgress,
          tourBackButton,
          tourNextButton,
          tourCloseButtons,
        },
        storageKey: transcribeTourStorageKey,
        steps: [
          {
            target: '[data-tour-target="new-session"]',
            title: 'Start a new consultation',
            body: 'Use this button to create a fresh blank consultation that you can title and work in straight away.',
          },
          {
            target: '[data-tour-target="session-list"]',
            title: 'Find recent consultations',
            body: 'Your recent consultations stay here so you can jump back in without losing your place.',
          },
          {
            target: '[data-tour-target="record-controls"]',
            title: 'Add the conversation',
            body: 'Use these controls to choose live capture or recorded upload, then add the consultation audio in the way that fits best.',
          },
          {
            target: '[data-tour-target="notes-panel"]',
            title: 'Review notes',
            body: 'Generated notes appear here. If you regenerate a note, you can switch between versions.',
          },
          {
            target: '[data-tour-target="messages-panel"]',
            title: 'Review messages',
            body: 'Messages, follow-ups, and saved instruction outputs appear here. You can switch between earlier versions any time.',
          },
        ],
      }).attach();

      window.addEventListener('beforeunload', () => {
        clearWorkspaceRefreshBurst();
        closeWorkspaceEventSource();
      });

      window.setTimeout(fetchWorkspace, 250);
      window.setTimeout(pollWorkspace, 1200);
