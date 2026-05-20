import { attachTranscribeActions } from './actions.js?v=20260520-working-note-template-guard';
import { readTranscribeBootstrap } from './bootstrap.js?v=20260421-pii-refresh';
import { createDocumentNavigator } from './documents.js?v=20260520-working-note-template-guard';
import { createTranscribeLayout } from './layout.js?v=20260421-pii-refresh';
import { createAudioCaptureController } from './media.js?v=20260513-vad-inactivity-prompt';
import { createStructuredEditor } from './structured.js?v=20260520-working-note-template-guard';
import { attachSmartPhraseExpander } from './smart-phrases.js?v=20260430-smart-phrases-reorder';
import { attachNoteReordering } from './reorder.js?v=20260501-blank-line-reorder-guard';
import { createGuidedTour } from './tour.js?v=20260421-pii-refresh';
import { csrfFetch } from '../csrf.js';
import { isWorkingNoteTargetId, workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';

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
      let hasDictationSttSelection = bootstrap.hasDictationSttSelection;
      let hasLlmSelection = bootstrap.hasLlmSelection;
      let sttAvailable = bootstrap.sttAvailable;
      let dictationSttAvailable = bootstrap.dictationSttAvailable;
      let sttStatusMessage = bootstrap.sttStatusMessage;
      let sttHealth = bootstrap.sttHealth || null;
      let dictationSttStatusMessage = bootstrap.dictationSttStatusMessage;
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
      let noteEditorDirty = false;
      let dirtyNoteTargetId = null;
      let dirtyNoteMode = null;
      let dirtyNoteExpectedUpdatedAt = null;
      let noteEditVersion = 0;
      let noteSaveTimer = null;
      let noteSaveInFlight = null;
      let noteSaveQueued = false;
      let noteSaveConflictShown = false;
      let followupEditorDirty = false;
      let dirtyFollowupDocumentId = null;
      let followupEditVersion = 0;
      let followupSaveTimer = null;
      let followupSaveInFlight = null;
      let followupSaveQueued = false;
      let followupSaveConflictShown = false;
      let userAppPreferences = (bootstrap.userAppPreferences && typeof bootstrap.userAppPreferences === 'object') ? { ...bootstrap.userAppPreferences } : {};
      let dictationDirty = false;
      let dictationSaveInFlight = null;
      let dictationUiBusy = false;
      let lastSavedDictationText = '';
      let activeWorkingNote = bootstrap.activeWorkingNote || null;
      let dictationPendingAudioBlob = null;
      let dictationPendingAudioFilename = 'dictation.webm';
      let dictationMediaRecorder = null;
      let dictationMediaStream = null;
      let dictationAudioChunks = [];
      let dictationDiscardOnStop = false;
      let dictationRecordingState = 'idle';
      let dictationRecordingStartedAt = null;
      let dictationRecordedMs = 0;
      let dictationTimerId = null;
      let dictationAudioContext = null;
      let dictationAnalyser = null;
      let dictationVisualizerFrameId = null;
      let dictationCreatedTranscriptForModal = false;
      let currentDraftText = '';
      let currentPiiEntities = [];
      let workspaceTranscriptPiiEntities = Array.isArray(bootstrap.activeTranscriptPiiEntities) ? [...bootstrap.activeTranscriptPiiEntities] : [];
      let piiMasked = false;
      let workspaceRedactionStatus = bootstrap.activeTranscriptRedactionStatus || { status: 'not_run', entity_count: 0, error_code: null };
      let workspaceClinicalNlpStatus = bootstrap.activeTranscriptClinicalNlpStatus || { status: 'not_run', entity_count: 0, error_code: null };
      const showRedactionDebug = bootstrap.showRedactionDebug;
      const initialTranscriptErrorMessage = bootstrap.initialTranscriptErrorMessage;

      const activeStatus = document.querySelector('[data-active-status]');
      const activeStatusPill = document.querySelector('[data-active-status-pill]');
      const activeIngestionModeChip = document.querySelector('[data-active-ingestion-mode]');
      currentTranscriptStatus = activeStatus?.textContent?.trim() || null;
      const sessionTitleDisplay = document.querySelector('[data-session-title-display]');
      const activeDraft = document.querySelector('[data-active-draft]');
      const transcriptStats = document.querySelector('[data-transcript-stats]');
      const piiCount = document.querySelector('[data-pii-count]');
      const piiStatus = document.querySelector('[data-pii-status]');
      const clinicalNlpStatus = document.querySelector('[data-clinical-nlp-status]');
      const piiTableWrap = document.querySelector('[data-pii-table-wrap]');
      const piiVisibilityToggle = document.querySelector('[data-toggle-pii-visibility]');
      const piiAddForm = document.querySelector('[data-pii-add-form]');
      const piiAddTypeInput = document.querySelector('[data-pii-add-type]');
      const piiAddValueInput = document.querySelector('[data-pii-add-value]');
      const activeProgress = document.querySelector('[data-session-progress]');
      const micStatus = document.querySelector('[data-mic-status]');
      const micTimer = document.querySelector('[data-mic-timer]');
      const micVisualizer = document.querySelector('[data-mic-visualizer]');
      const silencePrompt = document.querySelector('[data-vad-silence-prompt]');
      const silencePromptDismiss = document.querySelector('[data-vad-silence-prompt-dismiss]');
      const dictationMicStatus = document.querySelector('[data-dictation-mic-status]');
      const dictationMicTimer = document.querySelector('[data-dictation-mic-timer]');
      const dictationMicVisualizer = document.querySelector('[data-dictation-mic-visualizer]');
      const dictationSessionProgress = document.querySelector('[data-dictation-session-progress]');
      const dictationCombinedInput = document.querySelector('[data-dictation-combined-input]');
      const dictationProvenance = document.querySelector('[data-dictation-provenance]');
      const dictationModal = document.querySelector('[data-dictation-modal]');
      const dictationModalCloseButtons = [...document.querySelectorAll('[data-dictation-modal-close]')];
      const dictationPauseRecordingButton = document.querySelector('[data-dictation-pause-recording]');
      const dictationPauseRecordingIcon = document.querySelector('[data-dictation-pause-recording-icon]');
      const dictationCancelButton = document.querySelector('[data-dictation-cancel]');
      const dictationSaveButton = document.querySelector('[data-dictation-save]');
      const dictationSaveGenerateButton = document.querySelector('[data-dictation-save-generate]');
      const dictationSaveGenerateLabel = document.querySelector('[data-dictation-save-generate-label]');
      const dictationTemplateSelect = document.querySelector('[data-dictation-template-select]');
      const dictationCompact = document.querySelector('[data-dictation-compact]');
      const dictationCompactBody = document.querySelector('[data-dictation-compact-body]');
      const dictationCompactEdit = document.querySelector('[data-dictation-compact-edit]');
      const dictationCompactMore = document.querySelector('[data-dictation-compact-more]');
      const transcriptReviewGrid = document.querySelector('[data-transcript-review-grid]');
      const dictationCta = document.querySelector('[data-dictation-cta]');
      const dictationCtaStatus = document.querySelector('[data-dictation-cta-status]');
      const flashWrap = document.querySelector('[data-flash-wrap]');
      const flashBanner = document.querySelector('[data-flash]');
      const latestGeneratedOutput = document.querySelector('[data-latest-generated-output]');
      const noteEditorToolbar = document.querySelector('[data-note-editor-toolbar]');
      const generatedFreeformPanel = document.querySelector('[data-generated-freeform-panel]');
      const generatedFreeformRows = document.querySelector('[data-generated-freeform-rows]');
      const structuredNoteEmptyState = document.querySelector('[data-structured-note-empty-state]');
      const freeformNoteEmptyState = document.querySelector('[data-freeform-note-empty-state]');
      const generatedStructuredPanel = document.querySelector('[data-generated-structured-panel]');
      const generatedStructuredSections = document.querySelector('[data-generated-structured-sections]');
      const latestFollowupOutput = document.querySelector('[data-latest-followup-output]');
      const followupOutputTitle = document.querySelector('[data-followup-output-title]');
      const followupOutputSubtitle = document.querySelector('[data-followup-output-subtitle]');
      const noteSelectorWrap = document.querySelector('[data-note-selector-wrap]');
      const noteSelector = document.querySelector('[data-note-selector]');
      const noteDeleteButton = document.querySelector('[data-note-delete]');
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
      const outputLlmRequestSlot = document.querySelector('[data-output-llm-request-slot]');
      const followupLlmRequestSlot = document.querySelector('[data-followup-llm-request-slot]');
      const copyStructuredLinesButton = document.querySelector('[data-copy-structured-lines]');
      const clearStructuredSelectionButton = document.querySelector('[data-clear-structured-selection]');
      const selectStructuredSelectionButton = document.querySelector('[data-select-structured-selection]');
      const structuredCopyStatus = document.querySelector('[data-structured-copy-status]');
      const copyTranscriptButton = document.querySelector('[data-copy-transcript]');
      const tabActions = [...document.querySelectorAll('[data-tab-action]')];
      const templateModeBadge = document.querySelector('[data-selected-template-mode]');
      const structuredContextHiddenInputs = [...document.querySelectorAll('[data-structured-context-hidden]')];
      const sessionLinks = [...document.querySelectorAll('[data-session-link]')];
      const selectionBoxes = [...document.querySelectorAll('[data-session-select]')];
      const deleteButton = document.querySelector('[data-delete-selected]');
      const newSessionButton = document.querySelector('[data-new-session-button]');
      const uploadForm = document.querySelector('[data-upload-form]');
      const fileInput = document.querySelector('[data-audio-file-input]');
      const dictationFileInput = document.querySelector('[data-dictation-audio-file-input]');
      const retryIngestionForm = document.querySelector('[data-retry-ingestion-form]');
      const retryIngestionTrigger = document.querySelector('[data-retry-ingestion-trigger]');
      const retryTranscriptIdInput = document.querySelector('[data-retry-transcript-id]');
      const newSessionForm = document.querySelector('#new-session-form');
      const bulkDeleteForm = document.querySelector('#bulk-delete-sessions');
      const titleForm = document.querySelector('[data-transcript-title-form]');
      const renameTitleInput = document.querySelector('[data-transcript-title-input]');
      const generateOutputForm = document.querySelector('[data-generate-output-form]');
      const generateOutputTemplateSelect = document.querySelector('[data-template-select]');
      const templatePickerButton = document.querySelector('[data-template-picker-button]');
      const templatePickerLabel = document.querySelector('[data-template-picker-label]');
      const templatePickerMode = document.querySelector('[data-template-picker-mode]');
      const templatePickerModal = document.querySelector('[data-template-picker-modal]');
      const templatePickerOptions = [...document.querySelectorAll('[data-template-picker-option]')];
      const templatePickerCloseButtons = [...document.querySelectorAll('[data-template-picker-close]')];
      const generateFollowupForm = document.querySelector('[data-generate-followup-form]');
      const generateFollowupPromptInput = document.querySelector('[data-followup-prompt-input]');
      const generateFollowupTrigger = document.querySelector('[data-generate-followup-trigger]');
      const runQuickActionForm = document.querySelector('[data-run-quick-action-form]');
      const runQuickActionSelect = document.querySelector('[data-quick-action-select]');
      const runQuickActionTrigger = document.querySelector('[data-run-quick-action-trigger]');
      const quickActionContextInput = document.querySelector('[data-quick-action-context-input]');
      const quickActionSearchInput = document.querySelector('[data-quick-action-search]');
      const quickActionContextRecordButton = document.querySelector('[data-quick-action-context-record]');
      const quickActionContextRecordLabel = document.querySelector('[data-quick-action-context-record-label]');
      const quickActionContextStatus = document.querySelector('[data-quick-action-context-status]');
      const followupClearButton = document.querySelector('[data-followup-clear]');
      const contextCharCount = document.querySelector('[data-context-char-count]');
      const customPromptCharCount = document.querySelector('[data-custom-prompt-char-count]');
      const recordCustomPromptButton = document.querySelector('[data-record-custom-prompt]');
      const recordCustomPromptLabel = document.querySelector('[data-record-custom-prompt-label]');
      const followupPromptPreviewTitle = document.querySelector('[data-followup-prompt-preview-title]');
      const followupPromptPreviewBody = document.querySelector('[data-followup-prompt-preview-body]');
      const followupPromptPreviewNote = document.querySelector('[data-followup-prompt-preview-note]');
      const clearQuickActionButton = document.querySelector('[data-clear-quick-action]');
      const copyLatestFollowupButton = document.querySelector('[data-copy-latest-followup]');
      const deleteLatestFollowupButton = document.querySelector('[data-followup-delete-latest]');
      const followupLlmRequestToggles = [...document.querySelectorAll('[data-followup-llm-request-toggle]')];
      const quickActionQuickPicks = [...document.querySelectorAll('[data-quick-action-quick-pick]')];
      const quickActionCardRunButtons = [...document.querySelectorAll('[data-quick-action-card-run]')];
      const workspaceSettingsLink = document.querySelector('[data-workspace-settings-link]');
      const audioActionTrigger = document.querySelector('[data-audio-action-trigger]');
      const dictationAudioActionTrigger = document.querySelector('[data-dictation-audio-action-trigger]');
      const recordingModeSelect = document.querySelector('[data-recording-mode-select]');
      const recordToggleButton = document.querySelector('[data-record-toggle]');
      const recordToggleLabel = document.querySelector('[data-record-toggle-label]');
      const getRecordToggleIcon = () => document.querySelector('[data-record-toggle-icon]');
      const dictationRecordToggleButton = document.querySelector('[data-dictation-record-toggle]');
      const dictationRecordToggleLabel = document.querySelector('[data-dictation-record-toggle-label]');
      const dictationRecordToggleIcon = document.querySelector('[data-dictation-record-toggle-icon]');
      const dictationRetryTranscriptionButton = document.querySelector('[data-dictation-retry-transcription]');
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
      let localStatusLabel = null;
      let micIssue = null;
      let lastRenderedTranscriptId = transcriptId || null;
      let statusDetailsVisible = false;
let statusDetailsHideTimer = null;
      let structuredEditor = null;
      let workspaceRefreshBurstTimeoutIds = [];
      const protectedInitialDisabled = new Map(localBusyProtected.map((button) => [button, button.disabled]));
      const liveVadBundleVersion = '0.0.29';
      const liveVadModel = 'v5';
      const liveVadAssetBasePath = `/static/vendor/vad-web/${liveVadBundleVersion}/`;
      const liveVadOnnxBasePath = '/static/vendor/onnxruntime-web/1.22.0/';
      const liveVadSampleRate = 16000;
      const livePreRollMs = 800;
      const livePostRollTrimMs = 1000;
      const liveSilenceThresholdMs = 2000;
      const liveMaxChunkMs = 30000;
      const liveMinChunkMs = 400;
      const liveChunkOverlapMs = 800;
      const liveChunkUploadMinIntervalMs = 1100;
      const liveChunkRateLimitRetryMs = 1200;
      const liveRestartDelayMs = 150;
      const batchVadPreRollMs = 800;
      const batchVadSilenceThresholdMs = 1200;
      const batchVadTrailingBufferMs = 350;
      const batchRolloverMaxDurationMs = 12 * 60 * 1000;
      const batchRolloverMaxBytes = 22 * 1024 * 1024;
      const batchRolloverConflictRetryMs = 5000;
      const structuredSectionDefinitions = bootstrap.emisSections;

      let currentAssistantTab = bootstrap.activeTab;
      const paneStorageKey = 'openscribe-glm2-pane-state';
      const splitRatioStorageKey = 'openscribe-glm2-split-ratio';
      const transcribeTourStorageKey = `openscribe:tour:transcribe:${bootstrap.viewerRole}`;
      const dictationNudgeStoragePrefix = 'openscribe:dictation-nudge:';
      const initialPaneState = window.localStorage.getItem(paneStorageKey) || shell?.dataset.layoutState || 'normal';
      const initialSplitRatio = Number.parseFloat(window.localStorage.getItem(splitRatioStorageKey) || '');
      let lastSelectedOutputTemplateId = generateOutputTemplateSelect?.value || '';

      const friendlyModeLabel = (mode) => mode === 'live_chunked' ? 'live capture' : 'recorded upload';
      const refreshIcons = (root) => {
        window.refreshLucideIcons?.(root);
      };

      const markNoteEditorDirty = () => {
        const targetId = currentRenderedNoteTargetId();
        if (!noteEditorDirty || dirtyNoteTargetId !== targetId) {
          dirtyNoteExpectedUpdatedAt = currentNoteUpdatedAt() || (isWorkingNoteTargetId(targetId) ? activeWorkingNote?.updated_at || '' : '');
        }
        noteEditorDirty = true;
        dirtyNoteTargetId = targetId;
        dirtyNoteMode = currentRenderedNoteMode();
        noteEditVersion += 1;
        scheduleNoteAutosave();
      };

      const clearNoteEditorDirty = () => {
        noteEditorDirty = false;
        dirtyNoteTargetId = null;
        dirtyNoteMode = null;
        dirtyNoteExpectedUpdatedAt = null;
        noteSaveConflictShown = false;
      };

      const markFollowupEditorDirty = () => {
        followupEditorDirty = true;
        dirtyFollowupDocumentId = latestFollowupOutput?.dataset.latestFollowupId || selectedFollowupDocumentId || '';
        followupEditVersion += 1;
        scheduleFollowupAutosave();
      };

      const clearFollowupEditorDirty = () => {
        followupEditorDirty = false;
        dirtyFollowupDocumentId = null;
        followupSaveConflictShown = false;
      };

      const currentRenderedNoteTargetId = () => latestGeneratedOutput?.dataset?.latestGeneratedId || '';

      const currentRenderedNoteMode = () => latestGeneratedOutput?.dataset?.latestGeneratedMode || selectedWorkingNoteMode();

      const isNoteEditorFocused = () => {
        const activeElement = document.activeElement;
        return activeElement instanceof HTMLElement
          && Boolean(activeElement.closest('[data-generated-structured-panel], [data-generated-freeform-panel]'));
      };

      const hasPendingGeneratedNoteEdits = () => {
        const currentTargetId = currentRenderedNoteTargetId();
        return Boolean(noteEditorDirty && currentTargetId && dirtyNoteTargetId === currentTargetId);
      };

      const shouldPreserveNoteEditorRender = (nextSelectedNoteDocumentId = currentRenderedNoteTargetId()) => {
        const currentTargetId = currentRenderedNoteTargetId();
        const targetDocumentId = nextSelectedNoteDocumentId || '';
        if (noteEditorDirty) {
          return dirtyNoteTargetId === targetDocumentId;
        }
        return Boolean(isNoteEditorFocused() && currentTargetId === targetDocumentId);
      };
      const currentNoteUpdatedAt = () => latestGeneratedOutput?.dataset?.latestGeneratedUpdatedAt || '';

      const currentRenderedFollowupDocumentId = () => latestFollowupOutput?.dataset?.latestFollowupId || '';

      const hasPendingGeneratedFollowupEdits = () => {
        const currentDocumentId = currentRenderedFollowupDocumentId();
        return Boolean(followupEditorDirty && currentDocumentId && dirtyFollowupDocumentId === currentDocumentId);
      };

      const shouldPreserveFollowupEditorRender = (nextSelectedFollowupDocumentId = currentRenderedFollowupDocumentId()) => {
        const currentDocumentId = currentRenderedFollowupDocumentId();
        const targetDocumentId = nextSelectedFollowupDocumentId || '';
        if (followupEditorDirty) {
          return dirtyFollowupDocumentId === targetDocumentId;
        }
        return Boolean(currentDocumentId === targetDocumentId && document.activeElement?.closest?.('[data-latest-followup-output], [data-followup-output-title]'));
      };

      const buildNoteSaveRequest = () => {
        const targetId = currentRenderedNoteTargetId();
        if (isWorkingNoteTargetId(targetId)) {
          const mode = dirtyNoteMode || currentRenderedNoteMode();
          const serializedEditor = structuredEditor?.serializeCurrentNoteEditor?.({
            mode,
          }) || { mode };
          const expectedUpdatedAt = dirtyNoteExpectedUpdatedAt || currentNoteUpdatedAt() || activeWorkingNote?.updated_at || null;
          return {
            targetId,
            kind: 'working_note',
            endpoint: `/api/v1/transcripts/${transcriptId}/working-note`,
            payload: buildWorkingNotePayload(serializedEditor, expectedUpdatedAt),
          };
        }
        const generatedDocumentId = latestGeneratedOutput?.dataset?.latestGeneratedId || selectedNoteDocumentId || '';
        const mode = latestGeneratedOutput?.dataset?.latestGeneratedMode || '';
        const expectedUpdatedAt = dirtyNoteExpectedUpdatedAt || currentNoteUpdatedAt();
        if (!generatedDocumentId || !expectedUpdatedAt || (mode !== 'structured' && mode !== 'freeform')) {
          return null;
        }
        if (mode === 'structured') {
          const serializedEditor = structuredEditor?.serializeCurrentNoteEditor?.({ mode: 'structured' }) || { mode: 'structured', sections: [] };
          return {
            targetId: generatedDocumentId,
            kind: 'generated_note',
            generatedDocumentId,
            endpoint: `/api/v1/generated-documents/${generatedDocumentId}`,
            payload: {
              expected_updated_at: expectedUpdatedAt,
              edited_output_text: '',
              sections: serializedEditor.sections || [],
            },
          };
        }
        const serializedEditor = structuredEditor?.serializeCurrentNoteEditor?.({ mode: 'freeform' }) || { mode: 'freeform', edited_output_text: '' };
        return {
          targetId: generatedDocumentId,
          kind: 'generated_note',
          generatedDocumentId,
          endpoint: `/api/v1/generated-documents/${generatedDocumentId}`,
          payload: {
            expected_updated_at: expectedUpdatedAt,
            edited_output_text: serializedEditor.edited_output_text || '',
            sections: [],
          },
        };
      };

      const currentFollowupUpdatedAt = () => latestFollowupOutput?.dataset?.latestFollowupUpdatedAt || '';

      const buildFollowupSavePayload = () => {
        const generatedDocumentId = latestFollowupOutput?.dataset?.latestFollowupId || selectedFollowupDocumentId || '';
        const expectedUpdatedAt = currentFollowupUpdatedAt();
        const titleInput = document.querySelector('[data-followup-title-input]');
        const bodyInput = document.querySelector('[data-followup-body-input]');
        if (!generatedDocumentId || !expectedUpdatedAt || !bodyInput) {
          return null;
        }
        return {
          generatedDocumentId,
          payload: {
            expected_updated_at: expectedUpdatedAt,
            title: titleInput?.value || '',
            edited_output_text: bodyInput.value || '',
            sections: [],
          },
        };
      };

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
          tabActions,
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
        if (flashWrap) {
          flashWrap.hidden = true;
        }
        if (flashBanner) {
          flashBanner.hidden = true;
          flashBanner.textContent = message || '';
          flashBanner.classList.remove('success', 'error', 'warning', 'info');
          flashBanner.classList.add(['error', 'warning', 'info'].includes(kind) ? kind : 'success');
        }
        if (message) {
          window.showToast?.(message, kind);
        }
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

      const persistUserAppPreferences = async (patch) => {
        const nextPreferences = {
          favorite_quick_action_ids: Array.isArray(userAppPreferences.favorite_quick_action_ids) ? userAppPreferences.favorite_quick_action_ids : [],
          favorite_template_ids: Array.isArray(userAppPreferences.favorite_template_ids) ? userAppPreferences.favorite_template_ids : [],
          default_quick_action_id: userAppPreferences.default_quick_action_id || null,
          default_template_id: userAppPreferences.default_template_id || null,
          llm_detail_level: userAppPreferences.llm_detail_level || null,
          preferred_recording_mode: userAppPreferences.preferred_recording_mode || null,
          preferred_transcribe_tab: userAppPreferences.preferred_transcribe_tab || null,
          ...patch,
        };
        const response = await csrfFetch('/api/v1/app-preferences', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(nextPreferences),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not save your workspace preferences.'));
        }
        userAppPreferences = await response.json();
        const newSessionModeInput = document.querySelector('#new-session-form input[name="ingestion_mode"]');
        if (newSessionModeInput && userAppPreferences.preferred_recording_mode) {
          newSessionModeInput.value = userAppPreferences.preferred_recording_mode;
        }
        return userAppPreferences;
      };

      const persistDictationExplicitly = async () => {
        if (dictationSaveInFlight) {
          return dictationSaveInFlight;
        }
        if (!transcriptId || !dictationCombinedInput) {
          return null;
        }
        const combinedText = dictationCombinedInput.value;
        dictationSaveInFlight = (async () => {
          try {
            const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/post-consultation-dictation`, {
              method: 'PATCH',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ combined_text: combinedText }),
            });
            if (!response.ok) {
              throw new Error(await parseErrorMessage(response, 'Could not save dictation.'));
            }
            const savedDictation = await response.json();
            lastSavedDictationText = savedDictation.effective_text || '';
            dictationDirty = false;
            renderDictation(savedDictation);
            return savedDictation;
          } catch (error) {
            showFlash(error instanceof Error ? error.message : 'Could not save dictation.', 'error');
            return null;
          } finally {
            dictationSaveInFlight = null;
          }
        })();
        return dictationSaveInFlight;
      };

      const persistNoteEditsSilently = async ({ keepalive = false } = {}) => {
        if (noteSaveInFlight) {
          noteSaveQueued = true;
          return noteSaveInFlight;
        }
        if (!noteEditorDirty) {
          return null;
        }
        if (discardEmptyWorkingNoteDraft()) {
          return { kind: 'working_note_empty_draft_discarded' };
        }
        const saveRequest = buildNoteSaveRequest();
        if (!saveRequest) {
          return null;
        }
        const requestVersion = noteEditVersion;
        noteSaveInFlight = (async () => {
          try {
            if (saveRequest.kind === 'working_note' && !transcriptId) {
              return null;
            }
            const response = await csrfFetch(saveRequest.endpoint, {
              method: 'PATCH',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              keepalive,
              body: JSON.stringify(saveRequest.payload),
            });
            if (response.status === 409) {
              noteSaveQueued = false;
              if (!noteSaveConflictShown) {
                noteSaveConflictShown = true;
                showFlash('Note changed elsewhere. Reload note before saving again.', 'error');
              }
              return null;
            }
            if (!response.ok) {
              throw new Error(await parseErrorMessage(response, 'Could not save note edits.'));
            }
            const savedDocument = await response.json();
            if (saveRequest.kind === 'working_note') {
              activeWorkingNote = savedDocument;
              noteSaveConflictShown = false;
              if (requestVersion === noteEditVersion) {
                clearNoteEditorDirty();
              } else if (saveRequest.targetId === dirtyNoteTargetId) {
                dirtyNoteExpectedUpdatedAt = savedDocument.updated_at || '';
              }
              setWorkingNoteStatus(`Saved ${savedDocument.mode || selectedWorkingNoteMode()} working note`);
              if (latestGeneratedOutput && saveRequest.targetId === currentRenderedNoteTargetId()) {
                latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = savedDocument.updated_at || '';
              }
              return savedDocument;
            }
            workspaceNoteDocuments = workspaceNoteDocuments.map((document) => (
              document.id === savedDocument.id ? savedDocument : document
            ));
            if (latestGeneratedOutput && savedDocument.id === (selectedNoteDocumentId || latestGeneratedOutput.dataset.latestGeneratedId || '')) {
              latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = savedDocument.updated_at || '';
              latestGeneratedOutput.dataset.latestGeneratedStatus = savedDocument.status || '';
              latestGeneratedOutput.dataset.latestGeneratedMode = savedDocument.document_mode || '';
              latestGeneratedOutput.dataset.latestGeneratedId = savedDocument.id || '';
            }
            noteSaveConflictShown = false;
            if (requestVersion === noteEditVersion) {
              clearNoteEditorDirty();
            } else if (saveRequest.targetId === dirtyNoteTargetId) {
              dirtyNoteExpectedUpdatedAt = savedDocument.updated_at || '';
            }
            return savedDocument;
          } catch (error) {
            showFlash(error instanceof Error ? error.message : 'Could not save note edits.', 'error');
            return null;
          } finally {
            noteSaveInFlight = null;
            if (noteSaveQueued) {
              noteSaveQueued = false;
              scheduleNoteAutosave({ immediate: true });
            }
          }
        })();
        return noteSaveInFlight;
      };

      const persistFollowupEditsSilently = async ({ keepalive = false } = {}) => {
        if (followupSaveInFlight) {
          followupSaveQueued = true;
          return followupSaveInFlight;
        }
        const saveRequest = buildFollowupSavePayload();
        if (!saveRequest) {
          return null;
        }
        const requestVersion = followupEditVersion;
        followupSaveInFlight = (async () => {
          try {
            const response = await csrfFetch(`/api/v1/generated-documents/${saveRequest.generatedDocumentId}`, {
              method: 'PATCH',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              keepalive,
              body: JSON.stringify(saveRequest.payload),
            });
            if (response.status === 409) {
              followupSaveQueued = false;
              if (!followupSaveConflictShown) {
                followupSaveConflictShown = true;
                showFlash('Follow-up changed elsewhere. Reload before saving again.', 'error');
              }
              return null;
            }
            if (!response.ok) {
              throw new Error(await parseErrorMessage(response, 'Could not save follow-up edits.'));
            }
            const savedDocument = await response.json();
            workspaceFollowupDocuments = workspaceFollowupDocuments.map((document) => (
              document.id === savedDocument.id ? savedDocument : document
            ));
            if (latestFollowupOutput && savedDocument.id === (selectedFollowupDocumentId || latestFollowupOutput.dataset.latestFollowupId || '')) {
              latestFollowupOutput.dataset.latestFollowupUpdatedAt = savedDocument.updated_at || '';
              latestFollowupOutput.dataset.latestFollowupStatus = savedDocument.status || '';
              latestFollowupOutput.dataset.latestFollowupId = savedDocument.id || '';
            }
            followupSaveConflictShown = false;
            if (requestVersion === followupEditVersion) {
              clearFollowupEditorDirty();
            }
            return savedDocument;
          } catch (error) {
            showFlash(error instanceof Error ? error.message : 'Could not save follow-up edits.', 'error');
            return null;
          } finally {
            followupSaveInFlight = null;
            if (followupSaveQueued) {
              followupSaveQueued = false;
              scheduleFollowupAutosave({ immediate: true });
            }
          }
        })();
        return followupSaveInFlight;
      };

      function scheduleNoteAutosave({ immediate = false } = {}) {
        if (noteSaveTimer) {
          window.clearTimeout(noteSaveTimer);
          noteSaveTimer = null;
        }
        if (!noteEditorDirty) {
          return;
        }
        noteSaveTimer = window.setTimeout(() => {
          noteSaveTimer = null;
          void persistNoteEditsSilently();
        }, immediate ? 0 : 700);
      }

      function scheduleFollowupAutosave({ immediate = false } = {}) {
        if (followupSaveTimer) {
          window.clearTimeout(followupSaveTimer);
          followupSaveTimer = null;
        }
        if (!followupEditorDirty) {
          return;
        }
        followupSaveTimer = window.setTimeout(() => {
          followupSaveTimer = null;
          void persistFollowupEditsSilently();
        }, immediate ? 0 : 700);
      }

      const statusPriority = {
        'Transcription failed': 10,
        'Generation failed': 20,
        'Mic not detected': 30,
        'Mic blocked': 40,
        'Mic unavailable': 50,
        'Recording blocked': 60,
        'Speech issue': 70,
        'Generation unavailable': 80,
        'Redaction issue': 90,
        'Clinical NLP issue': 100,
        'Finalizing': 110,
        'Uploading': 120,
        'Sending chunk': 130,
        'Speech detected': 140,
        'Listening': 150,
        'Generating': 160,
        'Transcribing': 170,
        'Ready': 180,
        'Idle': 190,
      };

      const statusPillKind = (label) => {
        if (['Transcription failed', 'Generation failed'].includes(label)) return 'error';
        if (['Mic not detected', 'Mic blocked', 'Mic unavailable', 'Recording blocked'].includes(label)) return 'error';
        if (['Speech issue', 'Generation unavailable', 'Redaction issue', 'Clinical NLP issue'].includes(label)) return 'warning';
        if (['Finalizing', 'Uploading', 'Sending chunk', 'Speech detected', 'Listening', 'Generating', 'Transcribing'].includes(label)) return 'active';
        if (label === 'Ready') return 'ready';
        return 'idle';
      };

      const sentenceCaseStatus = (label) => {
        const value = String(label || 'idle').trim();
        if (!value) return 'Idle';
        return value.charAt(0).toUpperCase() + value.slice(1);
      };

      const ensureStatusDetailsUi = () => {
        if (!activeStatusPill) return null;
        activeStatusPill.tabIndex = 0;
        activeStatusPill.setAttribute('role', 'button');
        activeStatusPill.setAttribute('aria-haspopup', 'dialog');
        let badge = activeStatusPill.querySelector('[data-active-status-count]');
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'status-pill-count';
          badge.dataset.activeStatusCount = '';
          badge.hidden = true;
          activeStatusPill.appendChild(badge);
        }
        let details = activeStatusPill.querySelector('[data-active-status-details]');
        if (!details) {
          details = document.createElement('div');
          details.className = 'status-pill-details';
          details.dataset.activeStatusDetails = '';
          details.hidden = true;
    activeStatusPill.appendChild(details);
    details.addEventListener('mouseenter', () => { clearTimeout(statusDetailsHideTimer); showStatusDetails(true); });
    details.addEventListener('mouseleave', () => { statusDetailsHideTimer = setTimeout(() => showStatusDetails(false), 150); });
    activeStatusPill.addEventListener('mouseenter', () => { clearTimeout(statusDetailsHideTimer); showStatusDetails(true); });
    activeStatusPill.addEventListener('mouseleave', () => { statusDetailsHideTimer = setTimeout(() => showStatusDetails(false), 150); });
    activeStatusPill.addEventListener('focusin', () => { clearTimeout(statusDetailsHideTimer); showStatusDetails(true); });
    activeStatusPill.addEventListener('focusout', (event) => {
      if (!activeStatusPill.contains(event.relatedTarget)) {
        statusDetailsHideTimer = setTimeout(() => showStatusDetails(false), 150);
      }
    });
    activeStatusPill.addEventListener('click', (event) => {
      if (event.target instanceof HTMLElement && event.target.hasAttribute('data-stt-health-recheck')) return;
      clearTimeout(statusDetailsHideTimer);
      statusDetailsVisible = !statusDetailsVisible;
      showStatusDetails(statusDetailsVisible);
    });
    activeStatusPill.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      clearTimeout(statusDetailsHideTimer);
      statusDetailsVisible = !statusDetailsVisible;
      showStatusDetails(statusDetailsVisible);
    });
          document.addEventListener('click', (event) => {
            if (!statusDetailsVisible || !(event.target instanceof Node) || activeStatusPill.contains(event.target)) return;
            statusDetailsVisible = false;
            showStatusDetails(false);
          });
          details.addEventListener('click', (event) => {
            const trigger = event.target instanceof HTMLElement ? event.target.closest('[data-stt-health-recheck]') : null;
            if (!trigger) return;
            event.preventDefault();
            void recheckSttHealth(trigger);
          });
        }
        return { badge, details };
      };

      function showStatusDetails(show) {
        const details = activeStatusPill?.querySelector('[data-active-status-details]');
        if (!details) return;
        details.hidden = !show;
        activeStatusPill?.setAttribute('aria-expanded', show ? 'true' : 'false');
        statusDetailsVisible = show;
      }

      const pushStatusItem = (items, label, detail, severity = null) => {
        if (!label) return;
        items.push({
          label,
          detail: detail || label,
          severity: severity || statusPillKind(label),
          priority: statusPriority[label] || 500,
        });
      };

      const sttHealthNeedsAttention = () => {
        const status = String(sttHealth?.status || '').toLowerCase();
        if (status === 'unknown') return sttHealth?.checked === true;
        return ['warning', 'unavailable'].includes(status);
      };

      const buildStatusItems = () => {
        const items = [];
        if (latestIngestionJobStatus === 'failed' || currentTranscriptStatus === 'failed') {
          pushStatusItem(items, 'Transcription failed', latestIngestionErrorMessage || 'The last transcription attempt failed.');
        }
        if (workspaceNoteDocuments.some((document) => document.status === 'failed') || workspaceFollowupDocuments.some((document) => document.status === 'failed')) {
          pushStatusItem(items, 'Generation failed', 'Latest note or follow-up generation failed.');
        }
        if (micIssue) {
          pushStatusItem(items, micIssue.label, micIssue.detail);
        }
        if (transcriptId && activeIngestionMode && (!hasSttSelection || !sttAvailable)) {
          pushStatusItem(items, 'Recording blocked', sttStatusMessage || 'Speech service is not configured.');
        }
        if (transcriptId && hasSttSelection && sttAvailable && sttHealthNeedsAttention()) {
          pushStatusItem(items, 'Speech issue', sttHealth?.message || 'Speech service health is not reported.');
        }
        if (transcriptId && !hasLlmSelection && (readActiveDraftText().trim() || lastSavedDictationText.trim())) {
          pushStatusItem(items, 'Generation unavailable', 'Note generation is not configured.');
        }
        if (workspaceRedactionStatus?.status === 'failed') {
          const code = workspaceRedactionStatus?.error_code;
          pushStatusItem(items, 'Redaction issue', `Redaction check failed${code ? `: ${code}` : ''}.`);
        }
        if (workspaceClinicalNlpStatus?.status === 'failed') {
          const code = workspaceClinicalNlpStatus?.error_code;
          pushStatusItem(items, 'Clinical NLP issue', `Clinical NLP failed${code ? `: ${code}` : ''}.`);
        }
        const normalizedLocal = String(localStatusLabel || '').toLowerCase();
        const recordingActive = captureController?.isLiveCaptureUiActive?.() || captureController?.isCaptureUiActive?.();
        if (recordingActive === true || ['finalizing', 'stopping', 'uploading', 'sending chunk', 'speech detected', 'listening', 'recording'].includes(normalizedLocal)) {
          if (normalizedLocal.includes('final') || normalizedLocal.includes('stopping')) pushStatusItem(items, 'Finalizing', 'Live capture is finalizing.');
          else if (normalizedLocal.includes('uploading')) pushStatusItem(items, 'Uploading', 'Uploading audio.');
          else if (normalizedLocal.includes('sending')) pushStatusItem(items, 'Sending chunk', 'Sending live audio chunk.');
          else if (normalizedLocal.includes('speech')) pushStatusItem(items, 'Speech detected', 'Speech detected locally.');
          else pushStatusItem(items, 'Listening', 'Listening for speech.');
        }
        if (workspaceNoteDocuments.some((document) => document.status === 'queued' || document.status === 'processing') || workspaceFollowupDocuments.some((document) => document.status === 'queued' || document.status === 'processing')) {
          pushStatusItem(items, 'Generating', 'Generating note or follow-up.');
        }
        if (['queued', 'processing'].includes(latestIngestionJobStatus || '') || ['queued', 'transcribing', 'processing', 'uploading'].includes(currentTranscriptStatus || '')) {
          pushStatusItem(items, currentTranscriptStatus === 'uploading' ? 'Sending chunk' : 'Transcribing', 'Backend is processing audio.');
        }
        if (items.length === 0) {
          if (currentTranscriptStatus === 'ready') {
            pushStatusItem(items, 'Ready', 'All systems healthy.');
          } else {
            pushStatusItem(items, 'Idle', 'Ready when you are.');
          }
        }
        return items.sort((left, right) => left.priority - right.priority);
      };

      const renderStatusDetails = (items) => {
        const ui = ensureStatusDetailsUi();
        if (!ui) return;
        const extraCount = Math.max(0, items.length - 1);
        ui.badge.hidden = extraCount === 0;
        ui.badge.textContent = extraCount > 0 ? `+${extraCount}` : '';
        ui.details.innerHTML = '';
        const list = document.createElement('div');
        list.className = 'status-pill-details__list';
        items.forEach((item) => {
          const row = document.createElement('div');
          row.className = `status-pill-details__item status-pill-details__item--${item.severity}`;
          row.textContent = item.detail;
          list.appendChild(row);
        });
        ui.details.appendChild(list);
        if (sttHealthNeedsAttention()) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'status-pill-details__recheck';
          button.dataset.sttHealthRecheck = '';
          button.textContent = 'Recheck speech service';
          ui.details.appendChild(button);
        }
      };

      const renderStatusPill = () => {
        const items = buildStatusItems();
        const top = items[0] || { label: 'Idle', severity: 'idle' };
        if (activeStatus) activeStatus.textContent = top.label;
        syncStatusPillStyle(top.label);
        renderStatusDetails(items);
        if (transcriptId) {
          const sidebarStatus = document.querySelector(`[data-sidebar-status="${transcriptId}"]`);
          if (sidebarStatus) sidebarStatus.textContent = top.label;
        }
      };

      const recheckSttHealth = async (trigger) => {
        if (trigger) trigger.disabled = true;
        try {
          const response = await csrfFetch('/api/v1/transcribe/stt-health/recheck', {
            method: 'POST',
            credentials: 'include',
          });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not recheck speech service.'));
          }
          sttHealth = await response.json();
          renderStatusPill();
        } catch (error) {
          showFlash(error instanceof Error ? error.message : 'Could not recheck speech service.', 'error');
        } finally {
          if (trigger) trigger.disabled = false;
        }
      };

      const syncStatusPillStyle = (label) => {
        if (!activeStatusPill || !activeStatus) return;
        const dot = activeStatusPill.querySelector('span:first-child');
        activeStatusPill.classList.remove('bg-teal-pale', 'bg-coral/15', 'bg-amber-100', 'bg-white', 'border', 'border-stone', 'bg-recording-red');
        activeStatus.classList.remove('text-teal-deep', 'text-coral', 'text-slate', 'text-amber-800', 'text-recording-red');
        dot?.classList.remove('bg-teal-deep', 'bg-coral', 'bg-slate', 'bg-amber-600', 'bg-dot-recording', 'status-pulse', 'status-pulse-recording', 'dot-recording');
        const isRecording = captureController?.isCaptureUiActive?.() && (label === 'Listening' || label === 'Speech detected');
        if (isRecording) {
          activeStatusPill.classList.add('bg-recording-red');
          activeStatus.classList.add('text-recording-red');
          dot?.classList.remove('w-2', 'h-2');
          dot?.classList.add('bg-dot-recording', 'status-pulse-recording', 'dot-recording');
        } else {
          dot?.classList.add('w-2', 'h-2');
          const kind = statusPillKind(label);
          if (kind === 'error') {
            activeStatusPill.classList.add('bg-coral/15');
            activeStatus.classList.add('text-coral');
            dot?.classList.add('bg-coral');
          } else if (kind === 'warning') {
            activeStatusPill.classList.add('bg-amber-100');
            activeStatus.classList.add('text-amber-800');
            dot?.classList.add('bg-amber-600');
          } else if (kind === 'active') {
            activeStatusPill.classList.add('bg-teal-pale');
            activeStatus.classList.add('text-teal-deep');
            dot?.classList.add('bg-teal-deep', 'status-pulse');
          } else if (kind === 'ready') {
            activeStatusPill.classList.add('bg-teal-pale');
            activeStatus.classList.add('text-teal-deep');
            dot?.classList.add('bg-teal-deep');
          } else {
            activeStatusPill.classList.add('bg-white', 'border', 'border-stone');
            activeStatus.classList.add('text-slate');
            dot?.classList.add('bg-slate');
          }
        }
      };

      const setVisibleStatus = (label) => {
        const nextLabel = label || 'idle';
        localStatusLabel = nextLabel;
        renderStatusPill();
      };

      const statusLabelForRecordingProgress = (message) => {
        const normalized = String(message || '').toLowerCase();
        if (!normalized) return null;
        if (normalized.includes('speech detected')) return 'speech detected';
        if (normalized.includes('listening')) return 'listening';
        if (normalized.includes('thirty second') || normalized.includes('sending live audio') || normalized.includes('live audio part sent') || normalized.includes('live chunk queued')) {
          return 'sending chunk';
        }
        if (normalized.includes('finishing') || normalized.includes('stopping')) return 'stopping';
        if (normalized.includes('uploading')) return 'uploading';
        return null;
      };

      const setSessionProgress = (message) => {
        if (activeProgress) {
          activeProgress.textContent = message;
        }
        const recordingStatus = statusLabelForRecordingProgress(message);
        if (recordingStatus && (captureController?.isLiveCaptureUiActive?.() || activeIngestionMode === 'live_chunked')) {
          setVisibleStatus(recordingStatus);
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

      const reportMicIssue = (error) => {
        const name = String(error?.name || '').toLowerCase();
        if (name === 'notfounderror' || name === 'devicesnotfounderror') {
          micIssue = { label: 'Mic not detected', detail: 'No microphone was detected.' };
        } else if (name === 'notallowederror' || name === 'permissiondeniederror') {
          micIssue = { label: 'Mic blocked', detail: 'Microphone permission is blocked.' };
        } else if (error === null) {
          micIssue = null;
        } else {
          micIssue = { label: 'Mic unavailable', detail: 'Microphone capture could not start.' };
        }
        renderStatusPill();
      };

      const setDictationMicStatus = (message, kind = '') => {
        if (!dictationMicStatus) return;
        dictationMicStatus.textContent = message;
        dictationMicStatus.classList.remove('text-coral', 'text-success');
        if (kind === 'error') {
          dictationMicStatus.classList.add('text-coral');
        } else if (kind === 'success') {
          dictationMicStatus.classList.add('text-success');
        }
      };

      const setDictationSessionProgress = (message) => {
        if (!dictationSessionProgress) return;
        dictationSessionProgress.textContent = message || '';
        dictationSessionProgress.hidden = !message;
      };

      const defaultDictationMicStatusState = () => {
        if (!transcriptId) {
          return { message: 'Create a dictation session to record, or type dictation manually.', kind: '' };
        }
        if (!hasDictationSttSelection) {
          return { message: dictationSttStatusMessage || 'Recording unavailable. Ask your team lead to enable post-consultation dictation.', kind: 'error' };
        }
        if (!dictationSttAvailable) {
          return { message: dictationSttStatusMessage || 'Recording unavailable. Ask your team lead to enable post-consultation dictation.', kind: 'error' };
        }
        return { message: 'Ready for dictation.', kind: '' };
      };

      const dictationUnavailableCopy = () => (dictationSttStatusMessage || 'Recording unavailable. Ask your team lead to enable post-consultation dictation.');

      const isConsultationCaptureActive = () => {
        const label = String(recordToggleLabel?.textContent || '').toLowerCase();
        return Boolean(captureController?.isCaptureUiActive?.() || label.includes('stop') || label.includes('stopping') || label.includes('uploading'));
      };

      const hasDictationContent = (dictation = null) => {
        if (dictation) {
          return Boolean(String(dictation.effective_text || '').trim() || Number(dictation.segment_count || 0) > 0 || dictation.is_combined_text_user_edited);
        }
        return Boolean(String(dictationCombinedInput?.value || lastSavedDictationText || '').trim());
      };

      const hasUnsavedDictationDraft = () => (
        Boolean(dictationDirty)
        || Boolean(dictationPendingAudioBlob)
        || dictationRecordingState === 'recording'
        || dictationRecordingState === 'paused'
      );

      const syncDictationCtaState = () => {
        if (!dictationCta) return;
        const consultationBusy = isConsultationCaptureActive();
        const canUse = !consultationBusy;
        dictationCta.disabled = !canUse;
        if (consultationBusy) {
          dictationCta.title = 'Stop the consultation recording before adding post-consultation dictation.';
        } else if (transcriptId && (!hasDictationSttSelection || !dictationSttAvailable)) {
          dictationCta.title = 'Recording unavailable. You can type dictation manually.';
        } else if (!transcriptId) {
          dictationCta.title = 'Create a dictation-only session.';
        } else {
          dictationCta.title = 'Add post-consultation dictation.';
        }
        if (dictationCtaStatus) {
          dictationCtaStatus.hidden = true;
          dictationCtaStatus.textContent = '';
        }
      };

      const maybeShowDictationNudge = ({ previousStatus, transcript, noteDocuments, dictation }) => {
        if (!transcript?.id || hasDictationContent(dictation) || noteDocuments.length > 0) return;
        if (!['recording', 'uploading', 'queued', 'transcribing', 'processing'].includes(previousStatus || '')) return;
        if (transcript.status !== 'ready') return;
        const storageKey = `${dictationNudgeStoragePrefix}${transcript.id}`;
        try {
          if (window.localStorage.getItem(storageKey) === 'shown') return;
          window.localStorage.setItem(storageKey, 'shown');
        } catch (_) {}
        showFlash('Add a short dictation now to capture your summary and plan.', 'info');
        if (dictationCta) {
          dictationCta.classList.add('dictation-nudge');
          window.setTimeout(() => dictationCta.classList.remove('dictation-nudge'), 3800);
        }
      };

      const syncDictationCompactOverflow = () => {
        if (!dictationCompactBody || !dictationCompactMore) return;
        window.requestAnimationFrame(() => {
          const overflow = dictationCompactBody.scrollHeight > dictationCompactBody.clientHeight + 4;
          dictationCompactBody.classList.toggle('has-overflow', overflow);
          dictationCompactMore.hidden = !overflow;
        });
      };

      const setDictationModalOpen = (isOpen) => {
        if (!dictationModal) return;
        dictationModal.hidden = !isOpen;
        document.body.classList.toggle('modal-open', isOpen);
        if (isOpen) {
          dictationCreatedTranscriptForModal = false;
          dictationCombinedInput?.focus({ preventScroll: true });
          refreshIcons(dictationModal);
        }
      };

      const createDictationOnlySession = async () => {
        const response = await csrfFetch('/api/v1/transcripts/start', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: 'Dictation-only session', ingestion_mode: 'whole_file' }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not create a dictation session.'));
        }
        const transcript = await response.json();
        transcriptId = transcript.id;
        dictationCreatedTranscriptForModal = true;
        const url = new URL(window.location.href);
        url.searchParams.set('transcript_id', transcript.id);
        window.history.pushState({}, '', url.toString());
        await fetchWorkspace(transcript.id);
        return transcript;
      };

      const openDictationModal = async ({ highlightRecord = false } = {}) => {
        if (isConsultationCaptureActive()) {
          showFlash('Stop the consultation recording before adding post-consultation dictation.', 'error');
          syncDictationCtaState();
          return;
        }
        try {
          if (!transcriptId) {
            await createDictationOnlySession();
          }
          dictationDirty = false;
          dictationPendingAudioBlob = null;
          dictationPendingAudioFilename = 'dictation.webm';
          setDictationModalOpen(true);
          syncDictationControls();
          if (highlightRecord && dictationRecordToggleButton && !dictationRecordToggleButton.disabled) {
            dictationRecordToggleButton.focus({ preventScroll: false });
            dictationRecordToggleButton.classList.add('dictation-record-highlight');
            window.clearTimeout(openDictationModal._highlightTimer);
            openDictationModal._highlightTimer = window.setTimeout(() => {
              dictationRecordToggleButton.classList.remove('dictation-record-highlight');
            }, 1600);
          }
        } catch (error) {
          showFlash(error instanceof Error ? error.message : 'Could not open dictation.', 'error');
        }
      };

      const closeDictationModal = async ({ force = false } = {}) => {
        if (!dictationModal || dictationModal.hidden) return true;
        if (!force && hasUnsavedDictationDraft()) {
          if (!window.confirm('Discard unsaved dictation? This will delete the draft text and any unsaved recording.')) {
            return false;
          }
        }
        if (dictationRecordingState === 'recording' || dictationRecordingState === 'paused') {
          stopDictationRecording({ keepAudio: false });
        }
        dictationPendingAudioBlob = null;
        dictationPendingAudioFilename = 'dictation.webm';
        if (dictationCombinedInput) {
          dictationCombinedInput.value = lastSavedDictationText || '';
        }
        dictationDirty = false;
        setDictationModalOpen(false);
        syncDictationControls();
        return true;
      };

      const canUseDictationInput = () => Boolean(transcriptId && hasDictationSttSelection && dictationSttAvailable);

      const setDictationMicButtons = (isRecording) => {
        if (dictationFileInput) {
          dictationFileInput.disabled = !canUseDictationInput() || isRecording;
          dictationFileInput.title = (!canUseDictationInput() && dictationSttStatusMessage) ? dictationSttStatusMessage : '';
        }
        if (dictationAudioActionTrigger) {
          dictationAudioActionTrigger.disabled = !canUseDictationInput() || isRecording;
          dictationAudioActionTrigger.title = (!canUseDictationInput() && dictationSttStatusMessage) ? dictationSttStatusMessage : '';
        }
        if (dictationRecordToggleButton) {
          const busyState = dictationRecordingState === 'stopped' || dictationRecordingState === 'transcribing' || dictationPendingAudioBlob || dictationUiBusy || dictationSaveInFlight !== null;
          dictationRecordToggleButton.disabled = !canUseDictationInput() || busyState;
          dictationRecordToggleButton.title = (!canUseDictationInput() && dictationSttStatusMessage) ? dictationSttStatusMessage : '';
        }
        syncDictationCtaState();
      };

      const formatDictationDuration = (durationMs) => {
        const seconds = Math.max(0, Math.floor(durationMs / 1000));
        return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
      };

      const currentDictationDurationMs = () => (
        dictationRecordedMs + (
          dictationRecordingState === 'recording' && dictationRecordingStartedAt
            ? Math.max(0, Date.now() - dictationRecordingStartedAt)
            : 0
        )
      );

      const renderDictationTimer = () => {
        if (dictationMicTimer) {
          dictationMicTimer.textContent = formatDictationDuration(currentDictationDurationMs());
        }
      };

      const ensureDictationVisualizerBars = () => {
        if (!dictationMicVisualizer || dictationMicVisualizer.childElementCount > 0) return;
        const fragment = document.createDocumentFragment();
        for (let index = 0; index < 16; index += 1) {
          const bar = document.createElement('span');
          bar.className = 'mic-visualizer__bar';
          bar.style.setProperty('--level', '0.14');
          fragment.appendChild(bar);
        }
        dictationMicVisualizer.appendChild(fragment);
      };

      const setDictationVisualizerIdle = () => {
        if (!dictationMicVisualizer) return;
        ensureDictationVisualizerBars();
        dictationMicVisualizer.dataset.vadActive = 'false';
        dictationMicVisualizer.querySelectorAll('.mic-visualizer__bar').forEach((bar) => {
          bar.style.setProperty('--level', '0.14');
        });
      };

      const stopDictationVisualizer = () => {
        if (dictationVisualizerFrameId) {
          window.cancelAnimationFrame(dictationVisualizerFrameId);
          dictationVisualizerFrameId = null;
        }
        try {
          dictationAudioContext?.close?.();
        } catch (_) {}
        dictationAudioContext = null;
        dictationAnalyser = null;
        setDictationVisualizerIdle();
      };

      const startDictationVisualizer = (stream) => {
        stopDictationVisualizer();
        if (!dictationMicVisualizer || !window.AudioContext && !window.webkitAudioContext) return;
        ensureDictationVisualizerBars();
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        dictationAudioContext = new AudioContextCtor();
        const source = dictationAudioContext.createMediaStreamSource(stream);
        dictationAnalyser = dictationAudioContext.createAnalyser();
        dictationAnalyser.fftSize = 256;
        source.connect(dictationAnalyser);
        const data = new Uint8Array(dictationAnalyser.frequencyBinCount);
        const bars = [...dictationMicVisualizer.querySelectorAll('.mic-visualizer__bar')];
        const render = () => {
          if (!dictationAnalyser || dictationRecordingState === 'idle' || dictationRecordingState === 'stopped') return;
          dictationAnalyser.getByteTimeDomainData(data);
          let total = 0;
          for (let index = 0; index < data.length; index += 1) {
            total += Math.abs(data[index] - 128);
          }
          const level = Math.min(1, Math.max(0.14, (total / data.length) / 18));
          dictationMicVisualizer.dataset.vadActive = level > 0.22 ? 'true' : 'false';
          bars.forEach((bar, index) => {
            const wave = 0.78 + (Math.sin(Date.now() / 110 + index) * 0.22);
            bar.style.setProperty('--level', String(Math.max(0.14, Math.min(1, level * wave))));
          });
          dictationVisualizerFrameId = window.requestAnimationFrame(render);
        };
        render();
      };

      const stopDictationStream = () => {
        dictationMediaStream?.getTracks?.().forEach((track) => track.stop());
        dictationMediaStream = null;
        stopDictationVisualizer();
      };

      function syncDictationControls() {
        const isRecording = dictationRecordingState === 'recording';
        const isPaused = dictationRecordingState === 'paused';
        const isStopped = dictationRecordingState === 'stopped';
        const isTranscribing = dictationRecordingState === 'transcribing';
        const isBusy = dictationUiBusy || dictationSaveInFlight !== null;
        const activeCapture = isRecording || isPaused;
        const hasRetryableAudio = Boolean(dictationPendingAudioBlob) && !activeCapture && !isTranscribing;
        const canRecord = canUseDictationInput() && !isBusy && !isTranscribing && !hasRetryableAudio;
        if (dictationRecordToggleButton) {
          dictationRecordToggleButton.disabled = (!canRecord && !activeCapture) || isStopped || isTranscribing;
          dictationRecordToggleButton.dataset.state = isTranscribing ? 'transcribing' : (activeCapture ? 'recording' : 'idle');
        }
        if (dictationRecordToggleLabel) {
          dictationRecordToggleLabel.textContent = isTranscribing ? 'Transcribing...' : (activeCapture ? 'Stop' : 'Record');
        }
        if (dictationRecordToggleIcon) {
          dictationRecordToggleIcon.dataset.lucide = isTranscribing ? 'loader-2' : (activeCapture ? 'square' : 'mic');
        }
        if (dictationPauseRecordingButton) {
          dictationPauseRecordingButton.hidden = !activeCapture;
          dictationPauseRecordingButton.disabled = isBusy || isTranscribing;
          dictationPauseRecordingButton.setAttribute('aria-label', isPaused ? 'Resume dictation' : 'Pause dictation');
          dictationPauseRecordingButton.title = isPaused ? 'Resume' : 'Pause';
        }
        if (dictationPauseRecordingIcon) {
          dictationPauseRecordingIcon.dataset.lucide = isPaused ? 'play' : 'pause';
        }
        if (dictationAudioActionTrigger) {
          dictationAudioActionTrigger.disabled = !canRecord || activeCapture || isStopped || isTranscribing;
        }
        if (dictationRetryTranscriptionButton) {
          dictationRetryTranscriptionButton.hidden = !hasRetryableAudio;
          dictationRetryTranscriptionButton.disabled = !canUseDictationInput() || isBusy || isTranscribing;
        }
        if (dictationFileInput) {
          dictationFileInput.disabled = !canRecord || activeCapture || isStopped || isTranscribing;
        }
        if (dictationCombinedInput) {
          dictationCombinedInput.disabled = isBusy || isTranscribing;
        }
        if (dictationSaveButton) {
          dictationSaveButton.disabled = isBusy || isTranscribing || !transcriptId;
        }
        if (dictationSaveGenerateButton) {
          dictationSaveGenerateButton.disabled = isBusy || isTranscribing || !transcriptId || !hasLlmSelection || !hasSelectableOptions(generateOutputTemplateSelect);
        }
        if (dictationTemplateSelect) {
          dictationTemplateSelect.disabled = isBusy || isTranscribing || !hasLlmSelection || !hasSelectableOptions(dictationTemplateSelect);
        }
        setDictationMicButtons(activeCapture || isTranscribing);
        refreshIcons?.(dictationModal || document);
        renderDictationTimer();
      }

      const startDictationRecording = async () => {
        if (!canUseDictationInput()) {
          setDictationMicStatus(dictationUnavailableCopy(), 'error');
          return;
        }
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
          setDictationMicStatus('Dictation recording is not supported in this browser.', 'error');
          return;
        }
        try {
          dictationAudioChunks = [];
          dictationPendingAudioBlob = null;
          dictationPendingAudioFilename = 'dictation.webm';
          dictationMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          dictationMediaRecorder = new MediaRecorder(dictationMediaStream);
          dictationMediaRecorder.addEventListener('dataavailable', (event) => {
            if (event.data && event.data.size > 0) {
              dictationAudioChunks.push(event.data);
            }
          });
          dictationMediaRecorder.addEventListener('stop', async () => {
            const type = dictationMediaRecorder?.mimeType || 'audio/webm';
            dictationPendingAudioBlob = (!dictationDiscardOnStop && dictationAudioChunks.length > 0) ? new Blob(dictationAudioChunks, { type }) : null;
            dictationDiscardOnStop = false;
            dictationRecordingStartedAt = null;
            stopDictationStream();
            if (!dictationPendingAudioBlob) {
              dictationRecordingState = 'idle';
              setDictationMicStatus('No audio was captured.', 'error');
              syncDictationControls();
              return;
            }
            dictationPendingAudioFilename = dictationPendingAudioBlob.type === 'audio/wav' ? 'dictation.wav' : 'dictation.webm';
            await transcribePendingDictationAudio();
          });
          dictationRecordingState = 'recording';
          dictationRecordingStartedAt = Date.now();
          dictationRecordedMs = 0;
          dictationMediaRecorder.start();
          startDictationVisualizer(dictationMediaStream);
          dictationTimerId = window.setInterval(renderDictationTimer, 250);
          setDictationMicStatus('Recording dictation...', 'success');
          syncDictationControls();
        } catch (_) {
          dictationRecordingState = 'idle';
          stopDictationStream();
          setDictationMicStatus('Microphone access was denied or unavailable.', 'error');
          syncDictationControls();
        }
      };

      const pauseDictationRecording = () => {
        if (dictationRecordingState !== 'recording' || !dictationMediaRecorder) return;
        dictationRecordedMs = currentDictationDurationMs();
        dictationRecordingStartedAt = null;
        try {
          dictationMediaRecorder.pause();
        } catch (_) {}
        dictationRecordingState = 'paused';
        setDictationMicStatus('Dictation paused.', '');
        setDictationVisualizerIdle();
        syncDictationControls();
      };

      const resumeDictationRecording = () => {
        if (dictationRecordingState !== 'paused' || !dictationMediaRecorder) return;
        dictationRecordingStartedAt = Date.now();
        try {
          dictationMediaRecorder.resume();
        } catch (_) {}
        dictationRecordingState = 'recording';
        setDictationMicStatus('Recording dictation...', 'success');
        syncDictationControls();
      };

      function stopDictationRecording({ keepAudio = true } = {}) {
        if (dictationTimerId) {
          window.clearInterval(dictationTimerId);
          dictationTimerId = null;
        }
        if (dictationRecordingState === 'recording') {
          dictationRecordedMs = currentDictationDurationMs();
        }
        dictationRecordingStartedAt = null;
        if (!keepAudio) {
          dictationDiscardOnStop = true;
          dictationPendingAudioBlob = null;
          dictationPendingAudioFilename = 'dictation.webm';
          dictationAudioChunks = [];
        }
        if (dictationMediaRecorder && dictationMediaRecorder.state !== 'inactive') {
          try {
            if (!keepAudio) {
              dictationMediaRecorder.ondataavailable = null;
            }
            dictationMediaRecorder.stop();
          } catch (_) {
            stopDictationStream();
            if (keepAudio) {
              dictationRecordingState = 'idle';
              setDictationMicStatus('Could not stop dictation cleanly. Please try again.', 'error');
              syncDictationControls();
              return;
            }
          }
        } else {
          stopDictationStream();
        }
        dictationRecordingState = keepAudio ? 'transcribing' : 'idle';
        syncDictationControls();
      }

      const appendDictationPreviewText = (text) => {
        if (!dictationCombinedInput) return;
        const existing = dictationCombinedInput.value.trimEnd();
        const next = String(text || '').trim();
        if (!next) return;
        dictationCombinedInput.value = existing ? `${existing}\n\n${next}` : next;
        dictationDirty = dictationCombinedInput.value !== lastSavedDictationText;
        dictationCombinedInput.focus();
      };

      const previewDictationAudio = async (blob, filename = 'dictation.webm') => {
        if (!transcriptId || !blob) return null;
        const formData = new FormData();
        formData.append('audio', blob, filename);
        setDictationMicStatus('Transcribing dictation preview...');
        setDictationSessionProgress('Sending audio for preview. Nothing is saved until you press Save.');
        syncDictationControls();
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/post-consultation-dictation/preview-audio-file`, {
          method: 'POST',
          body: formData,
          credentials: 'include',
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not transcribe dictation.'));
        }
        const payload = await response.json();
        appendDictationPreviewText(payload.text || '');
        dictationPendingAudioBlob = null;
        dictationPendingAudioFilename = 'dictation.webm';
        if (dictationFileInput) {
          dictationFileInput.value = '';
        }
        dictationRecordingState = 'idle';
        setDictationMicStatus('Dictation text ready to review.', 'success');
        setDictationSessionProgress('');
        syncDictationControls();
        return payload;
      };

      const transcribePendingDictationAudio = async () => {
        if (!dictationPendingAudioBlob) return null;
        dictationRecordingState = 'transcribing';
        setDictationMicStatus('Transcribing dictation preview...');
        syncDictationControls();
        try {
          return await previewDictationAudio(dictationPendingAudioBlob, dictationPendingAudioFilename || 'dictation.webm');
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Could not transcribe dictation.';
          dictationRecordingState = 'idle';
          showFlash(message, 'error');
          setDictationMicStatus(message, 'error');
          setDictationSessionProgress('Recorded audio kept locally. Retry transcription or close to discard it.');
          syncDictationControls();
          return null;
        }
      };

      const setDictationBusy = (isBusy, label = 'Save & generate note') => {
        dictationUiBusy = isBusy;
        if (dictationSaveGenerateLabel) {
          dictationSaveGenerateLabel.textContent = isBusy ? label : 'Save & generate note';
        }
        syncDictationControls();
      };

      const saveDictationAndMaybeGenerate = async ({ generate = false } = {}) => {
        if (!transcriptId) return null;
        if (dictationPendingAudioBlob) {
          showFlash('Wait for dictation transcription before saving.', 'warning');
          return null;
        }
        if (dictationRecordingState === 'transcribing') {
          showFlash('Wait for dictation transcription before saving.', 'warning');
          return null;
        }
        if (dictationRecordingState === 'recording' || dictationRecordingState === 'paused') {
          showFlash('Stop dictation before saving.', 'warning');
          return null;
        }
        chooseTemplateFromDictationModal();
        setDictationBusy(true, generate ? 'Queueing...' : 'Saving...');
        const saved = await persistDictationExplicitly();
        if (!saved) {
          setDictationBusy(false);
          return null;
        }
        if (!generate) {
          showFlash('Dictation saved.', 'success');
          await fetchWorkspace();
          setDictationModalOpen(false);
          setDictationBusy(false);
          return saved;
        }
        const templateId = generateOutputTemplateSelect?.value || '';
        if (!templateId) {
          showFlash('Choose a template before generating.', 'error');
          setDictationBusy(false);
          return saved;
        }
        try {
          await saveWorkingNoteBeforeGeneration({ silent: true });
          const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/generate-output`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template_id: templateId }),
          });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not enqueue note generation.'));
          }
          setTab('output');
          showFlash('Queued note generation.', 'success');
          await fetchWorkspace();
          scheduleWorkspaceRefreshBurst();
          setDictationModalOpen(false);
        } catch (error) {
          showFlash(error instanceof Error ? error.message : 'Could not enqueue note generation.', 'error');
        } finally {
          setDictationBusy(false);
        }
        return saved;
      };

      const renderDictation = (dictation) => {
        const nextText = dictation?.effective_text || '';
        if (dictationCombinedInput && document.activeElement !== dictationCombinedInput && !dictationDirty) {
          dictationCombinedInput.value = nextText;
        }
        if (!dictationDirty) {
          lastSavedDictationText = nextText;
        }
        if (dictationProvenance) {
          if (nextText.trim()) {
            dictationProvenance.textContent = 'Saved dictation used for generation.';
          } else {
            dictationProvenance.textContent = 'No dictation yet. Record, upload audio, or type a summary directly.';
          }
        }
        if (dictationCompactBody) {
          dictationCompactBody.textContent = nextText.trim() || 'No post-consultation dictation yet.';
          dictationCompactBody.classList.toggle('has-content', Boolean(nextText.trim()));
          dictationCompactBody.classList.remove('is-expanded');
          syncDictationCompactOverflow();
        }
        if (dictationCta?.querySelector('[data-dictation-cta-label]')) {
          dictationCta.querySelector('[data-dictation-cta-label]').textContent = nextText.trim() ? 'Edit dictation' : 'Add dictation';
        }
        if (dictationCompactEdit) {
          dictationCompactEdit.textContent = nextText.trim() ? 'Edit' : 'Add dictation';
        }
        syncGenerationAvailability(readActiveDraftText().trim());
        syncDictationControls();
      };

      const displayStatusLabel = (statusLabel, ingestionMode) => {
        if (statusLabel === 'recording') {
          if (ingestionMode === 'live_chunked' && captureController?.isLiveCaptureUiActive?.()) {
            return 'listening';
          }
          return 'idle';
        }
        return statusLabel || 'idle';
      };

      const reflectBackendStatus = (statusLabel, errorMessage = null) => {
        currentTranscriptStatus = statusLabel || null;
        const visibleStatus = displayStatusLabel(statusLabel, activeIngestionMode);
        if (!captureController?.isCaptureUiActive?.() && !['queued', 'transcribing', 'processing', 'uploading'].includes(visibleStatus || '')) {
          localStatusLabel = null;
        }
        renderStatusPill();
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
        renderStatusPill();
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

      const readActiveDraftText = () => {
        if (currentDraftText) return currentDraftText;
        if (!activeDraft) return '';
        if (activeDraft instanceof HTMLTextAreaElement || activeDraft instanceof HTMLInputElement) {
          return activeDraft.value || '';
        }
        return activeDraft.textContent || '';
      };

      const uniquePiiEntities = (entities = []) => {
        const seen = new Set();
        return (Array.isArray(entities) ? entities : [])
          .map((entity) => ({
            entity_type: String(entity?.entity_type || 'PII').trim() || 'PII',
            value: String(entity?.value || '').trim(),
            placeholder: String(entity?.placeholder || '').trim(),
            occurrence_count: Number.parseInt(entity?.occurrence_count ?? 1, 10) || 1,
            source: entity?.source || 'detected',
            id: entity?.id || null,
            has_value: Boolean(entity?.has_value),
          }))
          .filter((entity) => entity.value.length > 0 || entity.placeholder.length > 0)
          .filter((entity) => {
            const key = `${entity.source}\u0000${entity.entity_type.toLowerCase()}\u0000${(entity.value || entity.placeholder).toLowerCase()}`;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          });
      };

      const escapeRegExp = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

      const maskedPiiText = (value) => value.replace(/\S/g, '•');

      const renderHighlightedTranscript = (text, entities = [], options = {}) => {
        if (!activeDraft) return;
        const maskPii = Boolean(options.maskPii);
        if (!text.trim()) {
          activeDraft.innerHTML = '<span class="text-slate">No conversation text yet. Upload a recording or use the microphone to begin. The transcript will appear here as the consultation unfolds.</span>';
          return;
        }
        const highlightEntities = uniquePiiEntities(entities)
          .map((entity) => ({ value: entity.value, source: entity.source || 'detected' }))
          .sort((left, right) => right.value.length - left.value.length);
        if (highlightEntities.length === 0) {
          activeDraft.textContent = text;
          return;
        }
        const values = [...new Set(highlightEntities.map((entity) => entity.value))];
        const sourceByValue = new Map(highlightEntities.map((entity) => [entity.value.toLowerCase(), entity.source]));
        const pattern = new RegExp(`(${values.map(escapeRegExp).join('|')})`, 'gi');
        activeDraft.innerHTML = text
          .split(pattern)
          .map((part) => {
            const source = sourceByValue.get(part.toLowerCase());
            if (!source) return escapeHtml(part);
            const className = source === 'clinical' ? 'clinical-highlight' : 'pii-highlight';
            const visibleText = maskPii && source !== 'clinical' ? maskedPiiText(part) : part;
            return `<mark class="${className}" data-real-value="${escapeHtml(part)}">${escapeHtml(visibleText)}</mark>`;
          })
          .join('');
      };

      const renderDraft = (text) => {
        if (!activeDraft) return;
        const nextText = text || '';
        currentDraftText = nextText;
        if (activeDraft instanceof HTMLTextAreaElement || activeDraft instanceof HTMLInputElement) {
          activeDraft.value = nextText;
          activeDraft.placeholder = 'No transcript text yet. Upload a recording or use the microphone to begin.';
          renderTranscriptStats(nextText);
          return;
        }
        renderHighlightedTranscript(nextText, workspaceTranscriptPiiEntities, { maskPii: piiMasked });
        renderTranscriptStats(nextText);
      };

      const canUseWholeFileInput = () => Boolean(transcriptId && hasSttSelection && sttAvailable && activeIngestionMode === 'whole_file');

      const selectedTemplateOption = () => generateOutputTemplateSelect?.selectedOptions?.[0] || null;

      const syncTemplatePickerUi = () => {
        const option = selectedTemplateOption();
        const isEnabled = templatePickerOptions.length > 0;
        if (templatePickerButton) {
          templatePickerButton.disabled = !isEnabled;
          templatePickerButton.title = !isEnabled ? 'Choose a template for this consultation.' : '';
        }
        if (templatePickerLabel) {
          templatePickerLabel.textContent = option?.dataset?.templateName || option?.textContent?.trim() || 'Choose a template';
        }
        if (templatePickerMode) {
          templatePickerMode.textContent = option?.dataset?.templateMode === 'structured' ? 'Sectioned note' : 'Free text note';
        }
        templatePickerOptions.forEach((button) => {
          button.classList.toggle('active', button.dataset.templateId === option?.value);
        });
        syncDictationTemplateSelect();
      };

      const closeTemplatePicker = () => {
        if (!templatePickerModal) return;
        templatePickerModal.hidden = true;
        templatePickerButton?.setAttribute('aria-expanded', 'false');
      };

      const openTemplatePicker = () => {
        if (!templatePickerModal || templatePickerButton?.disabled) return;
        templatePickerModal.hidden = false;
        templatePickerButton?.setAttribute('aria-expanded', 'true');
        const activeOption = templatePickerOptions.find((button) => button.classList.contains('active')) || templatePickerOptions[0] || null;
        window.requestAnimationFrame(() => {
          activeOption?.focus();
        });
      };

      const chooseTemplateFromPicker = (templateId) => {
        if (!generateOutputTemplateSelect || !templateId) return;
        if (generateOutputTemplateSelect.value === templateId) {
          closeTemplatePicker();
          return;
        }
        generateOutputTemplateSelect.value = templateId;
        generateOutputTemplateSelect.dispatchEvent(new Event('change', { bubbles: true }));
        closeTemplatePicker();
      };

      const syncDictationTemplateSelect = () => {
        if (!dictationTemplateSelect || !generateOutputTemplateSelect) return;
        if (dictationTemplateSelect.value !== generateOutputTemplateSelect.value) {
          dictationTemplateSelect.value = generateOutputTemplateSelect.value;
        }
      };

      const chooseTemplateFromDictationModal = () => {
        if (!dictationTemplateSelect || !generateOutputTemplateSelect) return;
        if (generateOutputTemplateSelect.value === dictationTemplateSelect.value) return;
        generateOutputTemplateSelect.value = dictationTemplateSelect.value;
        generateOutputTemplateSelect.dispatchEvent(new Event('change', { bubbles: true }));
      };

      const revertOutputTemplateSelection = () => {
        if (!generateOutputTemplateSelect) return;
        generateOutputTemplateSelect.value = lastSelectedOutputTemplateId || '';
        syncTemplatePickerUi();
        structuredEditor.syncTemplateModeBadge?.();
      };

      const handleOutputTemplateChange = async () => {
        if (!generateOutputTemplateSelect) return true;
        const currentTemplateId = generateOutputTemplateSelect.value || '';
        if (isWorkingNoteTargetId(currentRenderedNoteTargetId())) {
          if (discardEmptyWorkingNoteDraft()) {
            structuredEditor.syncStructuredTemplateUi();
            syncTemplatePickerUi();
            lastSelectedOutputTemplateId = currentTemplateId;
            return true;
          }
          if (noteEditorDirty) {
            const saved = await persistNoteEditsSilently({ keepalive: false });
            if (!saved) {
              showFlash('Save or clear the working note before changing template.', 'error');
              revertOutputTemplateSelection();
              return false;
            }
          }
          if (activeWorkingNote?.mode) {
            structuredEditor.syncTemplateModeBadge?.();
            syncTemplatePickerUi();
            lastSelectedOutputTemplateId = currentTemplateId;
            return true;
          }
        }
        structuredEditor.syncStructuredTemplateUi();
        syncTemplatePickerUi();
        lastSelectedOutputTemplateId = currentTemplateId;
        return true;
      };

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
        return { message: 'Ready to upload a recording or capture voice-only audio from your microphone.', kind: '' };
      };

      const hasSelectableOptions = (select) => {
        if (!select) return false;
        return [...select.options].some((option) => option.value);
      };

      const hasStructuredContextContent = () => {
        const structuredContext = structuredEditor?.collectStructuredContext() || {};
        return Object.values(structuredContext).some((lines) => Array.isArray(lines) && lines.length > 0);
      };

      const selectedWorkingNoteMode = () => {
        const lockedMode = activeWorkingNote?.mode || '';
        if (lockedMode) return lockedMode;
        return selectedTemplateOption()?.dataset?.templateMode === 'structured' ? 'structured' : 'freeform';
      };

      const buildWorkingNotePayload = (serializedEditor, expectedUpdatedAt = null) => {
        if (serializedEditor?.mode === 'structured') {
          const sections = {};
          (serializedEditor.sections || []).forEach((section) => {
            const sectionKey = section.section_key || '';
            const lines = String(section.text || '')
              .split('\n')
              .map((line) => line.trim())
              .filter(Boolean);
            if (sectionKey && lines.length > 0) {
              sections[sectionKey] = lines;
            }
          });
          return {
            mode: 'structured',
            expected_updated_at: expectedUpdatedAt,
            structured_note: { profile: 'emis', sections },
          };
        }
        return {
          mode: 'freeform',
          expected_updated_at: expectedUpdatedAt,
          freeform_text: serializedEditor?.edited_output_text || '',
        };
      };

      const collectWorkingNote = () => {
        const mode = dirtyNoteMode || currentRenderedNoteMode();
        const expectedUpdatedAt = currentNoteUpdatedAt() || activeWorkingNote?.updated_at || null;
        const serializedEditor = structuredEditor?.serializeCurrentNoteEditor?.({
          mode,
        }) || { mode };
        return buildWorkingNotePayload(serializedEditor, expectedUpdatedAt);
      };

      const workingNoteHasContent = () => {
        if (!isWorkingNoteTargetId(currentRenderedNoteTargetId())) {
          if (activeWorkingNote?.mode === 'structured') {
            return Object.values(activeWorkingNote.structured_note?.sections || {}).some((lines) => Array.isArray(lines) && lines.length > 0);
          }
          return Boolean(activeWorkingNote?.freeform_text && activeWorkingNote.freeform_text.trim());
        }
        const note = collectWorkingNote();
        if (note.mode === 'structured') {
          return Object.values(note.structured_note?.sections || {}).some((lines) => Array.isArray(lines) && lines.length > 0);
        }
        return Boolean(note.freeform_text && note.freeform_text.trim());
      };

      const isDiscardableEmptyWorkingNoteDraft = () => (
        isWorkingNoteTargetId(currentRenderedNoteTargetId())
        && noteEditorDirty
        && !workingNoteHasContent()
        && !activeWorkingNote?.mode
      );

      const setWorkingNoteStatus = (message = '') => {
        if (structuredCopyStatus && isWorkingNoteTargetId(currentRenderedNoteTargetId())) {
          structuredCopyStatus.textContent = message || 'Working note. Your own notes used as context for generation.';
        }
      };

      const discardEmptyWorkingNoteDraft = () => {
        if (!isDiscardableEmptyWorkingNoteDraft()) return false;
        clearNoteEditorDirty();
        setWorkingNoteStatus();
        return true;
      };

      const syncGenerationAvailability = (draftText = '') => {
        const hasDraft = Boolean(draftText && draftText.trim());
        const hasDictation = Boolean(lastSavedDictationText && lastSavedDictationText.trim());
        const hasStructuredInput = structuredEditor?.selectedOutputTemplateMode() === 'structured' && hasStructuredContextContent();
        const hasWorkingNote = workingNoteHasContent();
        const hasNoteInput = structuredEditor?.hasNoteInputContent?.() || false;
        const selectedTemplateId = generateOutputTemplateSelect?.value || '';
        const canChooseTemplate = Boolean(transcriptId && hasLlmSelection && hasSelectableOptions(generateOutputTemplateSelect));
        const canGenerateNote = Boolean(transcriptId && hasLlmSelection && selectedTemplateId && (hasDraft || hasStructuredInput || hasWorkingNote || hasDictation));
        const canRunQuickAction = Boolean(transcriptId && hasLlmSelection && (hasDraft || hasNoteInput) && hasSelectableOptions(runQuickActionSelect));
        const canGenerateFollowup = Boolean(transcriptId && hasLlmSelection && (hasDraft || hasNoteInput));

        if (generateOutputTemplateSelect) {
          generateOutputTemplateSelect.disabled = !canChooseTemplate;
        }
        const generateOutputButton = generateOutputForm?.querySelector('button[type="submit"]');
        if (generateOutputButton) {
          generateOutputButton.disabled = !canGenerateNote;
        }

        if (runQuickActionSelect) {
          runQuickActionSelect.disabled = !canRunQuickAction;
        }
        if (runQuickActionTrigger) {
          runQuickActionTrigger.disabled = !canGenerateFollowup;
        }
        if (quickActionContextInput) {
          quickActionContextInput.disabled = !canRunQuickAction;
        }
        if (quickActionContextRecordButton) {
          quickActionContextRecordButton.disabled = !canGenerateFollowup;
        }
        if (recordCustomPromptButton) {
          recordCustomPromptButton.disabled = !canGenerateFollowup;
        }
        quickActionQuickPicks.forEach((button) => {
          button.disabled = !canRunQuickAction;
        });
        quickActionCardRunButtons.forEach((button) => {
          button.disabled = !canRunQuickAction;
        });

        if (generateFollowupPromptInput) {
          generateFollowupPromptInput.disabled = !canGenerateFollowup;
        }
        if (generateFollowupTrigger) {
          generateFollowupTrigger.disabled = !canGenerateFollowup;
        }
      };

      const setMicButtons = (isRecording) => {
        const liveMode = activeIngestionMode === 'live_chunked';
        const canUseRecorder = liveMode ? canUseLiveInput() : canUseWholeFileInput();
        localBusyProtected.forEach((button) => {
          if (newSessionControls.includes(button)) {
            return;
          }
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
        const recordToggleIcon = getRecordToggleIcon();
        if (recordToggleIcon) {
          recordToggleIcon.dataset.lucide = isRecording
            ? 'square'
            : (liveMode ? 'circle-plus' : 'disc');
          refreshIcons(recordToggleIcon.parentElement || recordToggleIcon);
        }
        if (audioActionTrigger) {
          audioActionTrigger.title = (!isRecording && !canUseWholeFileInput() && !sttAvailable && sttStatusMessage) ? sttStatusMessage : '';
        }
        if (fileInput) {
          fileInput.title = (!isRecording && !canUseWholeFileInput() && !sttAvailable && sttStatusMessage) ? sttStatusMessage : '';
        }
        captureController?.syncDisplayedDuration?.();
      };

      const setNewSessionAvailability = (canCreate, message) => {
        newSessionControls.forEach((button) => {
          button.disabled = false;
          if (canCreate || !message) {
            delete button.dataset.newSessionBlockMessage;
          } else {
            button.dataset.newSessionBlockMessage = message;
          }
        });
      };

      const syncTranscriptTitleIfNeeded = async () => {
        if (!transcriptId || !renameTitleInput) return;
        const nextTitle = renameTitleInput.value.trim();
        if (nextTitle === currentTranscriptTitle) return;
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}`, {
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
          micVisualizer,
          recordToggleButton,
          silencePrompt,
          silencePromptDismiss,
          uploadForm,
        },
        config: {
          batchUploadSuccessMessage: 'Recording sent to be turned into text.',
          batchVadPreRollMs,
          batchRolloverConflictRetryMs,
          batchRolloverMaxBytes,
          batchRolloverMaxDurationMs,
          batchVadSilenceThresholdMs,
          batchVadTrailingBufferMs,
          liveChunkOverlapMs,
          liveMaxChunkMs,
          liveMinChunkMs,
          liveChunkRateLimitRetryMs,
          liveChunkUploadMinIntervalMs,
          liveOnnxBasePath: liveVadOnnxBasePath,
          livePostRollTrimMs,
          livePreRollMs,
          liveRestartDelayMs,
          liveSilenceThresholdMs,
          liveVadAssetBasePath,
          liveVadModel,
          liveVadOnnxBasePath,
          liveVadSampleRate,
          vadSilencePromptMs: 30000,
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
        finalizeLiveCapture: async ({ keepalive = false } = {}) => {
          if (!transcriptId) return null;
          const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/finalize-live-capture`, {
            method: 'POST',
            credentials: 'include',
            keepalive,
          });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not finalize live capture.'));
          }
          await fetchWorkspace();
          return response.json();
        },
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
        reportMicIssue,
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
          generatedFreeformPanel,
          generatedFreeformRows,
          structuredNoteEmptyState,
          generatedStructuredPanel,
          freeformNoteEmptyState,
          generatedStructuredSections,
          copyStructuredLinesButton,
          latestGeneratedOutput,
          noteEditorToolbar,
          structuredContextHiddenInputs,
          structuredCopyStatus,
          templateModeBadge,
        },
        structuredSectionDefinitions,
        getTranscriptId: () => transcriptId,
        getDraftText: () => readActiveDraftText().trim(),
        syncGenerationAvailability,
        onNoteEditorChanged: markNoteEditorDirty,
        persistStructuredContextSilently: async () => persistStructuredContextSilently(),
      });
      structuredEditor.bootstrapFromDom();
      attachSmartPhraseExpander({
        smartPhrases: bootstrap.smartPhrases || [],
        onExpanded: ({ phrase }) => {
          if (!phrase?.id) return;
          void csrfFetch(`/api/v1/smart-phrases/personal/${phrase.id}/used`, {
            method: 'POST',
            credentials: 'include',
          }).catch(() => {});
        },
      });
      attachNoteReordering({
        structuredEditor,
        showFlash,
      });
      generatedStructuredPanel?.addEventListener('focusout', (event) => {
        if (event.target instanceof HTMLTextAreaElement && event.target.hasAttribute('data-structured-line-input')) {
          scheduleNoteAutosave({ immediate: true });
        }
      });
      generatedFreeformPanel?.addEventListener('focusout', (event) => {
        if (event.target instanceof HTMLTextAreaElement && event.target.hasAttribute('data-freeform-note-input')) {
          scheduleNoteAutosave({ immediate: true });
        }
      });
      if (followupOutputTitle instanceof HTMLInputElement || followupOutputTitle instanceof HTMLTextAreaElement) {
        followupOutputTitle.addEventListener('input', markFollowupEditorDirty);
        followupOutputTitle.addEventListener('focusout', () => {
          scheduleFollowupAutosave({ immediate: true });
        });
      }
      latestFollowupOutput?.addEventListener('input', (event) => {
        if (event.target instanceof HTMLTextAreaElement && event.target.hasAttribute('data-followup-body-input')) {
          markFollowupEditorDirty();
        }
      });
      latestFollowupOutput?.addEventListener('focusout', (event) => {
        if (event.target instanceof HTMLTextAreaElement && event.target.hasAttribute('data-followup-body-input')) {
          scheduleFollowupAutosave({ immediate: true });
        }
      });

      if (templatePickerButton) {
        templatePickerButton.addEventListener('click', () => {
          openTemplatePicker();
        });
      }
      templatePickerCloseButtons.forEach((button) => {
        button.addEventListener('click', closeTemplatePicker);
      });
      templatePickerOptions.forEach((button) => {
        button.addEventListener('click', () => {
          chooseTemplateFromPicker(button.dataset.templateId || '');
        });
      });
      templatePickerModal?.addEventListener('click', (event) => {
        if (event.target instanceof HTMLElement && event.target.hasAttribute('data-template-picker-close')) {
          closeTemplatePicker();
        }
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && templatePickerModal && !templatePickerModal.hidden) {
          closeTemplatePicker();
        }
      });
      syncTemplatePickerUi();

      const renderFollowupOutput = (document) => {
        if (!latestFollowupOutput) return;
        const setTitleInput = (value, disabled = false) => {
          if (!followupOutputTitle) return;
          if (followupOutputTitle instanceof HTMLInputElement || followupOutputTitle instanceof HTMLTextAreaElement) {
            followupOutputTitle.value = value;
            followupOutputTitle.disabled = disabled;
          } else {
            followupOutputTitle.textContent = value;
          }
        };
        if (!document) {
          latestFollowupOutput.dataset.latestFollowupStatus = '';
          latestFollowupOutput.dataset.latestFollowupId = '';
          latestFollowupOutput.dataset.latestFollowupUpdatedAt = '';
          setTitleInput('Generated follow-up', true);
          if (followupOutputSubtitle) followupOutputSubtitle.textContent = 'Select or generate a follow-up';
          latestFollowupOutput.innerHTML = '<div class="empty-state"><div class="empty-state__text">Select a quick action and generate a follow-up.</div></div>';
          return;
        }
        latestFollowupOutput.dataset.latestFollowupUpdatedAt = document.updated_at || '';
        setTitleInput(
          document.title || document.source_quick_action_name || (document.generator_type === 'quick_action' ? 'Quick action' : 'Follow-up'),
          document.status !== 'ready'
        );
        if (followupOutputSubtitle) {
          const kind = document.generator_type === 'quick_action' ? 'Quick action' : 'Follow-up';
          followupOutputSubtitle.textContent = [kind, document.created_at || ''].filter(Boolean).join(', ');
        }
        if (document.status === 'ready') {
          latestFollowupOutput.innerHTML = `
            <textarea class="followup-output-card-v2__content followup-output-body-input-v2" aria-label="Follow-up text" data-followup-copy-body data-followup-body-input>${escapeHtml(document.edited_output_text || '')}</textarea>
          `;
          return;
        }
        if (document.status === 'queued') {
          latestFollowupOutput.innerHTML = '<span class="text-slate">Your follow-up is waiting to be written.</span>';
          return;
        }
        if (document.status === 'processing') {
          latestFollowupOutput.innerHTML = '<span class="text-slate">Your follow-up is being written.</span>';
          return;
        }
        if (document.status === 'failed') {
          latestFollowupOutput.innerHTML = `<span class="text-slate">The latest follow-up could not be created${document.error_message ? `: ${escapeHtml(document.error_message)}` : ''}.</span>`;
          return;
        }
        latestFollowupOutput.innerHTML = '<span class="text-slate">No follow-up content yet.</span>';
      };

      const renderRedactionDebugPanel = (slot, document) => {
        if (!slot) return;
        slot.innerHTML = '';
        if (!showRedactionDebug || !document) return;
        const wrapper = window.document.createElement('details');
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

      const renderPiiEntities = (entities = [], options = {}) => {
        if (!piiCount || !piiTableWrap) return;
        const rawBaseEntities = Array.isArray(entities) ? entities : [];
        const allowReveal = options.allowReveal !== false;
        const updateTranscriptHighlights = options.updateTranscriptHighlights !== false;
        const baseEntities = rawBaseEntities.length === 0 && options.useWorkspaceWhenEmpty
          ? workspaceTranscriptPiiEntities
          : rawBaseEntities;
        const manualWorkspaceEntities = options.includeWorkspaceManual === false
          ? []
          : workspaceTranscriptPiiEntities.filter((entity) => entity.source === 'manual');
        const rows = uniquePiiEntities([...baseEntities, ...manualWorkspaceEntities]);
        const displayRows = allowReveal
          ? rows
          : rows.map((entity) => ({ ...entity, value: '' }));
        currentPiiEntities = displayRows;
        if (updateTranscriptHighlights) {
          renderHighlightedTranscript(currentDraftText || readActiveDraftText(), workspaceTranscriptPiiEntities, { maskPii: piiMasked });
        }
        if (piiVisibilityToggle) {
          piiVisibilityToggle.textContent = piiMasked ? 'Show PII' : 'Hide PII';
          piiVisibilityToggle.setAttribute('aria-pressed', piiMasked ? 'true' : 'false');
        }
        piiCount.textContent = String(displayRows.length);
        const redactionStatus = workspaceRedactionStatus?.status || 'not_run';
        if (piiStatus) {
          piiStatus.classList.toggle('pii-status--error', redactionStatus === 'failed');
          if (redactionStatus === 'succeeded') {
            piiStatus.textContent = 'Redaction check complete.';
          } else if (redactionStatus === 'failed') {
            const errorCode = workspaceRedactionStatus?.error_code;
            piiStatus.textContent = `Redaction check failed${errorCode ? `: ${errorCode}` : ''}.`;
          } else {
            piiStatus.textContent = 'Redaction check has not run for this transcript yet.';
          }
        }
        const clinicalStatus = workspaceClinicalNlpStatus?.status || 'not_run';
        if (clinicalNlpStatus) {
          clinicalNlpStatus.classList.toggle('pii-status--error', clinicalStatus === 'failed');
          if (clinicalStatus === 'succeeded') {
            const count = Number(workspaceClinicalNlpStatus?.entity_count || 0);
            clinicalNlpStatus.textContent = `Clinical NLP complete: ${count} item${count === 1 ? '' : 's'}.`;
          } else if (clinicalStatus === 'failed') {
            const errorCode = workspaceClinicalNlpStatus?.error_code;
            clinicalNlpStatus.textContent = `Clinical NLP failed${errorCode ? `: ${errorCode}` : ''}.`;
          } else {
            clinicalNlpStatus.textContent = 'Clinical NLP has not run for this transcript yet.';
          }
        }
        if (displayRows.length === 0) {
          const emptyText = redactionStatus === 'succeeded'
            ? 'No PII identified in the latest redaction check.'
            : (redactionStatus === 'failed'
              ? 'Redaction check failed. You can add missed PII manually before generating notes.'
              : 'No PII identified yet. Finish capture or save the transcript to run redaction.');
          piiTableWrap.innerHTML = `<div class="pii-empty" data-pii-empty>${escapeHtml(emptyText)}</div>`;
          return;
        }
        piiTableWrap.innerHTML = `
          <table class="pii-table">
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Value</th>
                <th scope="col">Count</th>
              </tr>
            </thead>
            <tbody data-pii-table-body>
              ${displayRows.map((entity) => `
                <tr data-pii-source="${escapeHtml(entity.source || '')}" data-pii-entity-id="${escapeHtml(entity.id || '')}">
                  <td><span class="pii-type ${entity.source === 'clinical' ? 'pii-type--clinical' : ''}">${escapeHtml(String(entity.entity_type || '').replaceAll('_', ' '))}</span></td>
                  <td>
                    <span class="pii-value" data-real-value="${escapeHtml(entity.value || '')}">${escapeHtml(piiMasked && entity.source !== 'clinical' ? maskedPiiText(entity.value || '') : (entity.value || ''))}</span>
                  </td>
                  <td class="pii-count-cell">
                    <span>${escapeHtml(entity.occurrence_count ?? 0)}</span>
                    ${entity.source === 'manual' && entity.id ? `
                      <button type="button" class="pii-row-delete" data-pii-delete="${escapeHtml(entity.id)}" aria-label="Remove manual PII">
                        <i class="w-3.5 h-3.5" data-lucide="trash-2"></i>
                      </button>
                    ` : ''}
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
        refreshIcons(piiTableWrap);
      };

      piiVisibilityToggle?.addEventListener('click', () => {
        piiMasked = !piiMasked;
        renderPiiEntities(currentPiiEntities, { includeWorkspaceManual: false });
      });

      piiAddForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (!transcriptId) {
          showFlash('Open consultation before adding PII.', 'error');
          return;
        }
        const value = String(piiAddValueInput?.value || '').trim();
        if (!value) return;
        const submitButton = piiAddForm.querySelector('button[type="submit"]');
        if (submitButton) submitButton.disabled = true;
        try {
          const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/manual-pii`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              entity_type: String(piiAddTypeInput?.value || 'PII').trim() || 'PII',
              value,
              occurrence_count: 1,
            }),
          });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not add PII.'));
          }
          const savedEntity = await response.json();
          workspaceTranscriptPiiEntities = uniquePiiEntities([...workspaceTranscriptPiiEntities, savedEntity]);
          if (piiAddValueInput) piiAddValueInput.value = '';
          renderPiiEntities(currentPiiEntities);
          void fetchWorkspace(transcriptId);
        } catch (error) {
          showFlash(error instanceof Error ? error.message : 'Could not add PII.', 'error');
        } finally {
          if (submitButton) submitButton.disabled = false;
        }
      });

      piiTableWrap?.addEventListener('click', async (event) => {
        const revealTrigger = event.target instanceof Element ? event.target.closest('[data-pii-reveal]') : null;
        if (revealTrigger && transcriptId) {
          revealTrigger.disabled = true;
          try {
            const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/pii-entities/reveal`, {
              method: 'POST',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
            });
            if (!response.ok) {
              throw new Error(await parseErrorMessage(response, 'Could not reveal PII values.'));
            }
            workspaceTranscriptPiiEntities = uniquePiiEntities(await response.json());
            renderPiiEntities(workspaceTranscriptPiiEntities, { includeWorkspaceManual: false });
          } catch (error) {
            showFlash(error instanceof Error ? error.message : 'Could not reveal PII values.', 'error');
            revealTrigger.disabled = false;
          }
          return;
        }
        const trigger = event.target instanceof Element ? event.target.closest('[data-pii-delete]') : null;
        if (!trigger || !transcriptId) return;
        const entityId = trigger.dataset.piiDelete || '';
        if (!entityId) return;
        trigger.disabled = true;
        try {
          const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/manual-pii/${entityId}`, {
            method: 'DELETE',
            credentials: 'include',
          });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not remove PII.'));
          }
          workspaceTranscriptPiiEntities = workspaceTranscriptPiiEntities.filter((entity) => entity.id !== entityId);
          renderPiiEntities(currentPiiEntities.filter((entity) => entity.id !== entityId));
          void fetchWorkspace(transcriptId);
        } catch (error) {
          showFlash(error instanceof Error ? error.message : 'Could not remove PII.', 'error');
          trigger.disabled = false;
        }
      });

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
          outputLlmRequestSlot,
          followupLlmRequestSlot,
        },
        helpers: {
          escapeHtml,
          renderGeneratedOutput: (...args) => structuredEditor.renderGeneratedOutput(...args),
          renderFollowupOutput,
          renderPiiEntities,
          renderRedactionDebugPanel,
          refreshIcons,
          setTab,
        },
        getState: () => ({
          workspaceNoteDocuments,
          workspaceFollowupDocuments,
          workspaceStructuredContext,
          activeWorkingNote,
          activeTranscriptId: transcriptId,
          hasActiveTranscript: Boolean(transcriptId),
          selectedTemplateMode: selectedWorkingNoteMode(),
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
        clearNoteEditorDirty,
        persistNoteEditsSilently,
        hasPendingGeneratedNoteEdits,
        shouldPreserveNoteEditorRender,
        clearFollowupEditorDirty,
        persistFollowupEditsSilently,
        hasPendingGeneratedFollowupEdits,
        shouldPreserveFollowupEditorRender,
      });

      const persistStructuredContextSilently = async () => activeWorkingNote;

      const saveWorkingNoteBeforeGeneration = async ({ silent = false } = {}) => {
        if (!transcriptId) return null;
        if (!isWorkingNoteTargetId(currentRenderedNoteTargetId())) {
          return activeWorkingNote;
        }
        if (!noteEditorDirty) {
          return activeWorkingNote;
        }
        if (!workingNoteHasContent()) {
          if (discardEmptyWorkingNoteDraft()) {
            setWorkingNoteStatus('Empty working-note draft ignored.');
            return { kind: 'working_note_empty_draft_discarded' };
          }
          throw new Error('Clear the working note before generating.');
        }
        setWorkingNoteStatus('Saving working note...');
        const saved = await persistNoteEditsSilently({ keepalive: false });
        if (!saved) {
          setWorkingNoteStatus('Working note save failed');
          throw new Error('Save the working note before generating.');
        }
        return saved;
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
        const previousTranscriptStatus = currentTranscriptStatus;
        const dictation = workspace.post_consultation_dictation || null;
        const workingNote = workspace.active_working_note || null;
        const generatedDocuments = Array.isArray(workspace.generated_documents) ? workspace.generated_documents : [];
        const noteDocuments = generatedDocuments.filter((document) => document.generator_type === 'template');
        const followupDocuments = generatedDocuments.filter((document) => document.generator_type === 'followup' || document.generator_type === 'quick_action');
        const sidebarTranscripts = Array.isArray(workspace.recent_transcripts) ? workspace.recent_transcripts : [];
        const nextTranscriptId = transcript?.id || null;
        if (nextTranscriptId !== lastRenderedTranscriptId) {
          micIssue = null;
          localStatusLabel = null;
          lastRenderedTranscriptId = nextTranscriptId;
        }
        transcriptId = transcript?.id || null;
        workspaceTranscriptPiiEntities = uniquePiiEntities(workspace.active_transcript_pii_entities || []);
        workspaceRedactionStatus = workspace.active_transcript_redaction_status || { status: 'not_run', entity_count: 0, error_code: null };
        workspaceClinicalNlpStatus = workspace.active_transcript_clinical_nlp_status || { status: 'not_run', entity_count: 0, error_code: null };
        currentTranscriptStatus = transcript?.status || null;
        activeIngestionMode = transcript?.ingestion_mode || null;
        nextLiveChunkSequenceNo = transcript?.next_live_chunk_sequence_no_upload || 1;
        hasSttSelection = Boolean(workspace.stt_selected);
        hasDictationSttSelection = Boolean(workspace.dictation_stt_selected);
        sttAvailable = Boolean(workspace.stt_available);
        sttHealth = workspace.stt_health || null;
        dictationSttAvailable = Boolean(workspace.dictation_stt_available);
        sttStatusMessage = workspace.stt_status_message || null;
        dictationSttStatusMessage = workspace.dictation_stt_status_message || null;
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
          const checkbox = selectionBoxes.find((input) => input.value === item.id);
          if (checkbox) checkbox.dataset.hasTranscriptContent = item.has_transcript_content ? 'true' : 'false';
        });

        if (transcript) {
          reflectBackendStatus(transcript.status, transcript.latest_ingestion_error_message || null);
          const draftText = transcript.current_draft_text || '';
          renderDraft(draftText);
          renderPiiEntities(workspaceTranscriptPiiEntities);
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
          renderPiiEntities([]);
          syncGenerationAvailability('');
          if (sessionTitleDisplay) sessionTitleDisplay.value = '';
          if (renameTitleInput) renameTitleInput.value = '';
          if (activeIngestionModeChip) activeIngestionModeChip.textContent = '';
          syncRecordingModeControl(userAppPreferences.preferred_recording_mode || 'whole_file');
          latestIngestionJobStatus = null;
          latestIngestionErrorMessage = null;
          setRetryAvailability(false);
          setSessionProgress('Create or open a consultation to begin.');
        }
        renderDictation(dictation);
        activeWorkingNote = workingNote || null;
        maybeShowDictationNudge({
          previousStatus: previousTranscriptStatus,
          transcript,
          noteDocuments,
          dictation,
        });
        syncDictationCtaState();
        if (!(shouldPreserveLiveMicStatus() && !(latestIngestionJobStatus === 'failed' && latestIngestionErrorMessage))) {
          const micStatusState = defaultMicStatusState();
          setMicStatus(micStatusState.message, micStatusState.kind);
        }
        const dictationMicStatusState = defaultDictationMicStatusState();
        setDictationMicStatus(dictationMicStatusState.message, dictationMicStatusState.kind);

        const structuredContext = workspace.active_structured_context || {};
        workspaceNoteDocuments = noteDocuments;
        workspaceFollowupDocuments = followupDocuments;
        workspaceStructuredContext = structuredContext;
        const validNoteTargets = [...(transcriptId ? [{ id: workingNoteTargetId(transcriptId) }] : []), ...noteDocuments];
        selectedNoteDocumentId = validNoteTargets.some((document) => document.id === selectedNoteDocumentId)
          ? selectedNoteDocumentId
          : (selectedDocumentFromList(noteDocuments, null)?.id || (transcriptId ? workingNoteTargetId(transcriptId) : null));
        selectedFollowupDocumentId = selectedDocumentFromList(followupDocuments, selectedFollowupDocumentId)?.id || null;
        const preserveDirtyFollowupEditor = shouldPreserveFollowupEditorRender(selectedFollowupDocumentId || '');
        const noteRenderState = renderSelectedNote();
        const preserveDirtyNoteEditor = Boolean(noteRenderState?.preservedEditor);
        renderSelectedFollowup({ preserveEditor: preserveDirtyFollowupEditor });
        structuredEditor.setLastSavedStructuredContext(JSON.stringify(structuredContext));
        structuredEditor.syncStructuredContextHiddenInputs();
        structuredEditor.syncStructuredEditorAvailability();
        setMicButtons(isCaptureUiActive());
        setDictationMicButtons(false);
        syncDictationControls();
        if (!preserveDirtyNoteEditor) {
          structuredEditor.syncStructuredTemplateUi();
        }
        syncTemplatePickerUi();
        renderStatusPill();
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

      const isCaptureUiActive = () => {
        return captureController.isCaptureUiActive();
      };

      const shouldPreserveLiveMicStatus = () => {
        return captureController.shouldPreserveLiveMicStatus();
      };

      async function pollWorkspace() {
        if (!shouldUseWorkspacePollingFallback()) return;
        if (!transcriptId) return;
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
      renderDraft(readActiveDraftText().trim());
      lastSavedDictationText = dictationCombinedInput?.value || '';
      syncGenerationAvailability(readActiveDraftText().trim());
      structuredEditor.syncStructuredTemplateUi();
      syncTemplatePickerUi();
      syncDictationCompactOverflow();
      syncDictationControls();
      dictationCombinedInput?.addEventListener('input', () => {
        const nextValue = dictationCombinedInput.value;
        dictationDirty = nextValue !== lastSavedDictationText;
        syncDictationControls();
      });
      dictationCta?.addEventListener('click', () => {
        if (isConsultationCaptureActive()) {
          showFlash('Stop the consultation recording before adding post-consultation dictation.', 'error');
          syncDictationCtaState();
          return;
        }
        void openDictationModal({ highlightRecord: true });
      });
      dictationCompactEdit?.addEventListener('click', () => void openDictationModal());
      dictationCompactMore?.addEventListener('click', () => {
        if (!dictationCompactBody || !dictationCompactMore) return;
        const expanded = dictationCompactBody.classList.toggle('is-expanded');
        dictationCompactMore.textContent = expanded ? 'Show less' : 'Show more';
      });
      dictationModalCloseButtons.forEach((button) => {
        button.addEventListener('click', () => void closeDictationModal());
      });
      dictationCancelButton?.addEventListener('click', () => void closeDictationModal());
      dictationRecordToggleButton?.addEventListener('click', () => {
        if (dictationRecordingState === 'recording') {
          stopDictationRecording({ keepAudio: true });
        } else if (dictationRecordingState === 'paused') {
          stopDictationRecording({ keepAudio: true });
        } else {
          void startDictationRecording();
        }
      });
      dictationPauseRecordingButton?.addEventListener('click', () => {
        if (dictationRecordingState === 'recording') {
          pauseDictationRecording();
        } else if (dictationRecordingState === 'paused') {
          resumeDictationRecording();
        }
      });
      dictationRetryTranscriptionButton?.addEventListener('click', () => {
        void transcribePendingDictationAudio();
      });
      dictationAudioActionTrigger?.addEventListener('click', () => {
        if (!canUseDictationInput()) {
          setDictationMicStatus(dictationUnavailableCopy(), 'error');
          return;
        }
        dictationFileInput?.click();
      });
      dictationFileInput?.addEventListener('change', async () => {
        const file = dictationFileInput.files?.[0] || null;
        if (!file) return;
        dictationPendingAudioBlob = file;
        dictationPendingAudioFilename = file.name || 'dictation-upload.webm';
        const payload = await transcribePendingDictationAudio();
        if (payload) {
          dictationFileInput.value = '';
        }
      });
      dictationSaveButton?.addEventListener('click', () => void saveDictationAndMaybeGenerate({ generate: false }));
      dictationSaveGenerateButton?.addEventListener('click', () => void saveDictationAndMaybeGenerate({ generate: true }));
      dictationTemplateSelect?.addEventListener('change', chooseTemplateFromDictationModal);
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && dictationModal && !dictationModal.hidden) {
          event.preventDefault();
          if (dictationRecordingState === 'recording' || dictationRecordingState === 'paused') {
            showFlash('Stop dictation before closing.', 'warning');
            return;
          }
          void closeDictationModal();
        }
      });
      document.addEventListener('openscribe:legacy-structured-context-changed', () => {
        syncGenerationAvailability(readActiveDraftText().trim());
      });
      layoutController.attach();
      setMicButtons(false);
      {
        if (!(shouldPreserveLiveMicStatus() && !(latestIngestionJobStatus === 'failed' && latestIngestionErrorMessage))) {
          const micStatusState = defaultMicStatusState();
          setMicStatus(micStatusState.message, micStatusState.kind);
        }
        const dictationMicStatusState = defaultDictationMicStatusState();
        setDictationMicStatus(dictationMicStatusState.message, dictationMicStatusState.kind);
      }
      syncWorkspaceRealtimeConnection();
      if (transcriptId) {
        window.setTimeout(() => {
          fetchWorkspace();
        }, 0);
      }

      syncRecordingModeControl(activeIngestionMode || userAppPreferences.preferred_recording_mode || 'whole_file');

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
          followupHistory,
          followupOutputTitle,
          followupOutputSubtitle,
          latestFollowupOutput,
          generateFollowupForm,
          generateFollowupPromptInput,
          generateFollowupTrigger,
          generatedStructuredPanel,
          generateOutputForm,
          generateOutputTemplateSelect,
          latestGeneratedOutput,
          newSessionForm,
          noteDeleteButton,
          noteSelector,
          quickActionContextRecordButton,
          quickActionContextRecordLabel,
          quickActionContextInput,
          quickActionSearchInput,
          quickActionQuickPicks,
          quickActionCardRunButtons,
          quickActionContextStatus,
          followupClearButton,
          contextCharCount,
          customPromptCharCount,
          recordCustomPromptButton,
          recordCustomPromptLabel,
          followupPromptPreviewTitle,
          followupPromptPreviewBody,
          followupPromptPreviewNote,
          clearQuickActionButton,
          copyLatestFollowupButton,
          deleteLatestFollowupButton,
          followupLlmRequestToggles,
          recordingModeSelect,
          renameTitleInput,
          runQuickActionForm,
          runQuickActionSelect,
          runQuickActionTrigger,
          selectStructuredSelectionButton,
          selectionBoxes,
          sessionLinks,
          structuredCopyStatus,
          titleForm,
          uploadForm,
        },
        routeBase,
        getTranscriptId: () => transcriptId,
        getTranscriptText: () => currentDraftText,
        getActiveIngestionMode: () => activeIngestionMode,
        getIsLiveCaptureUiActive: () => isLiveCaptureUiActive(),
        getIsRecordingSwitchBlocked: () => Boolean(captureController?.isLiveCaptureUiActive?.()) || currentTranscriptStatus === 'recording',
        selectDocumentFromUi,
        showFlash,
        showCopyToast,
        parseErrorMessage,
        fetchWorkspace,
        pollWorkspace,
        scheduleWorkspaceRefreshBurst,
        syncTranscriptTitleIfNeeded,
        saveWorkingNoteBeforeGeneration,
        setVisibleStatus,
        setSessionProgress,
        setRetryAvailability,
        reflectBackendStatus,
        persistUserAppPreferences,
        handleOutputTemplateChange,
        setMicButtons,
        setTab,
          structuredEditor,
          onNoteGenerationQueued: () => {
            selectedNoteDocumentId = null;
          },
          clearWorkingNote: async () => {
            if (!transcriptId) return;
            if (!workingNoteHasContent() && !activeWorkingNote?.mode) {
              if (discardEmptyWorkingNoteDraft()) {
                showFlash('Working note draft cleared.', 'success');
              }
              return;
            }
            if (!window.confirm('Clear this working note? This cannot be undone.')) return;
            const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/working-note`, {
              method: 'DELETE',
              credentials: 'include',
            });
            if (!response.ok) {
              showFlash(await parseErrorMessage(response, 'Could not clear working note.'), 'error');
              return;
            }
            activeWorkingNote = null;
            clearNoteEditorDirty();
            selectedNoteDocumentId = workingNoteTargetId(transcriptId || '');
            renderSelectedNote();
            showFlash('Working note cleared.', 'success');
            void fetchWorkspace();
          },
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
            target: '[data-tour-target="followups-panel"]',
            title: 'Review follow-ups',
            body: 'Follow-ups and quick action outputs appear here. You can switch between earlier versions any time.',
          },
        ],
      }).attach();

      window.addEventListener('pagehide', () => {
        captureController?.handlePageLifecycleExit?.();
        clearWorkspaceRefreshBurst();
        closeWorkspaceEventSource();
        if (noteSaveTimer) {
          window.clearTimeout(noteSaveTimer);
          noteSaveTimer = null;
        }
        if (followupSaveTimer) {
          window.clearTimeout(followupSaveTimer);
          followupSaveTimer = null;
        }
        stopDictationRecording({ keepAudio: false });
        void persistNoteEditsSilently({ keepalive: true });
        void persistFollowupEditsSilently({ keepalive: true });
      });

      window.setTimeout(fetchWorkspace, 250);
      window.setTimeout(pollWorkspace, 1200);
