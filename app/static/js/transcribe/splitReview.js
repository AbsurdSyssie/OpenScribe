import { csrfFetch } from '../csrf.js';
import { CHECKER_UNAVAILABLE_MESSAGE, generationFailureDetailsHtml, generationFailureMessage } from './documents.js?v=20261014-generation-status';

const VALID_DISPOSITIONS = new Set([
  'separate_note',
  'include_in_primary',
  'exclude_from_notes',
]);
const ACTIVE_STATUS = 'active';
const TERMINAL_STATUSES = new Set(['confirmed', 'bypassed']);
const EDITABLE_BATCH_STATUSES = new Set(['ready', 'completed_partial', 'failed']);
const IN_FLIGHT_ANALYSIS_STATUSES = new Set(['queued', 'processing']);
const IN_FLIGHT_BATCH_STATUSES = new Set(['generation_queued', 'generating', 'verifying']);
const RESTORATION_BACKOFF_MS = Object.freeze([1500, 3000, 6000, 12000, 15000]);
const NO_NOTE_ANALYSIS_STATUSES = new Set(['not_required', 'failed', 'stale', 'incomplete']);
const SAFE_SPLIT_ERROR_CODES = new Set([
  'consultation_split_analysis_candidates_invalid',
  'consultation_split_analysis_input_invalid',
  'consultation_split_analysis_input_too_large',
  'consultation_split_analysis_invalid',
  'consultation_split_analysis_invalid_output',
  'consultation_split_analysis_provider_invalid',
  'consultation_split_analysis_request_invalid',
  'consultation_split_analysis_unavailable',
  'consultation_split_credential_unavailable',
  'consultation_split_proposal_unavailable',
  'consultation_split_provider_failed',
  'consultation_split_provider_binding_invalid',
  'consultation_split_provider_config_invalid',
  'consultation_split_reservation_expired',
  'consultation_split_source_expired',
  'consultation_split_source_stale',
  'consultation_split_template_unavailable',
  'consultation_split_generation_unavailable',
  'consultation_split_draft_unavailable',
  'consultation_split_draft_invalid',
  'consultation_split_intent_unavailable',
  'consultation_split_batch_unavailable',
  'consultation_split_llm_model_unavailable',
  'not_found',
]);

const asString = (value) => (value === null || value === undefined ? '' : String(value));
const normalizeTitle = (value) => asString(value).trim().replace(/\s+/g, ' ');
const safeSplitErrorCode = (value) => (typeof value === 'string' && SAFE_SPLIT_ERROR_CODES.has(value) ? value : null);

// Error text is deliberately selected here, rather than copied from an API
// response. Split work can involve clinical/provider data, which is never safe
// to interpolate into the owner UI.
export function splitDiagnosticMessage({ stage, operationId = null, errorCode = null, reusedAnalysis = false, analysisStatus = null, canContinue = false, failurePresentation = null } = {}) {
  const code = safeSplitErrorCode(errorCode);
  let message;
  if (stage === 'analysis') {
    if (code === 'consultation_split_source_stale' || analysisStatus === 'stale') {
      message = 'No note generated. The consultation changed before the note split was prepared. Save the current consultation and select Create again.';
    } else if (code === 'consultation_split_source_expired') {
      message = 'No note generated. This consultation is no longer available for note generation.';
    } else if (code === 'consultation_split_analysis_invalid_output') {
      message = reusedAnalysis && canContinue
        ? 'No note generated. The saved note-split analysis could not be validated. Create reuses this failure while the consultation is unchanged. Select Continue as one note to generate one note.'
        : 'No note generated. The proposed note split could not be validated. Select Create again.';
    } else if (code === 'consultation_split_provider_failed' || code === 'consultation_split_llm_model_unavailable') {
      message = reusedAnalysis
        ? (canContinue
          ? 'No note generated. The note-split analysis failed because the AI provider could not complete the request. Create reuses this failed analysis while the consultation is unchanged. Select Continue as one note to generate one note.'
          : 'No note generated. The note-split analysis failed because the AI provider could not complete the request. Create reuses this failed analysis while the consultation is unchanged.')
        : 'No note generated. The note-split analysis could not be prepared. Select Create again.';
    } else {
      message = canContinue
        ? 'No note generated. The note split could not be prepared. Select Continue as one note to generate one note, or select Create again.'
        : 'No note generated. The note split could not be prepared. Select Create again.';
    }
  } else if (stage === 'draft') {
    message = code === 'consultation_split_source_stale'
      ? 'No note generated. The consultation changed before the note split could be reviewed. Save the current consultation and select Create again.'
      : 'No note generated. The note split could not be opened for review. Select Create to retry opening the review.';
  } else if (stage === 'confirmation') {
    if (code === 'consultation_split_source_stale') message = 'The consultation changed before the note split was confirmed. Save the current consultation and select Create again.';
    else if (code === 'consultation_split_template_unavailable') message = 'A selected template is no longer available. Choose an available template and select Create again.';
    else message = 'The note split could not be confirmed. No notes were generated. Review the split and try again.';
  } else if (stage === 'continuation') {
    message = code === 'not_found' || code === 'consultation_split_template_unavailable'
      ? 'The saved template is unavailable. Choose an available template and select Create to start a new request.'
      : 'One note could not be started. Your saved split request was not consumed.';
  } else if (stage === 'regeneration') {
    message = code === 'consultation_split_template_unavailable'
      ? 'A template for these split notes is unavailable. Choose an available template and select Create again.'
      : 'Split-note regeneration could not be started. Please try again.';
  } else if (stage === 'generation') {
    message = generationFailureMessage(failurePresentation);
  } else if (stage === 'submission') {
    message = 'The note split could not be started. Review the consultation and select Create again.';
  } else {
    message = 'The note split could not be started. Review the consultation and select Create again.';
  }
  const reference = typeof operationId === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(operationId) ? operationId : null;
  return reference ? `${message} Reference: ${reference}.` : message;
}

// This is deliberately conservative rather than a full Unicode case-fold.
// The server remains authoritative for the final title uniqueness check.
const titleComparisonKey = (value) => {
  let normalized = normalizeTitle(value);
  try { normalized = normalized.normalize('NFKC'); } catch (_) {}
  return normalized.replace(/[ßẞ]/g, 'ss').toLowerCase();
};

export function createSplitAnalysisRestorationPoller({
  getState = () => ({}),
  refreshWorkspace = async () => null,
  setTimeoutFn = (...args) => globalThis.setTimeout(...args),
  clearTimeoutFn = (...args) => globalThis.clearTimeout(...args),
  backoffMs = RESTORATION_BACKOFF_MS,
  maxUnchangedCycles = 30,
} = {}) {
  let timeoutId = null;
  let inFlight = false;
  let stopped = false;
  let realtimeConnected = false;
  let analysisSignature = null;
  let unchangedCycles = 0;

  const currentState = () => getState() || {};
  const eligibleState = () => {
    const state = currentState();
    const analysis = state.analysis;
    if (!state.capabilityEnabled || !state.transcriptId || !analysis?.analysis_id) return null;
    const awaitingManualReviewDraft = Boolean(
      state.intent?.manual_review_requested
      && state.intent?.status === 'analysis_pending'
      && state.intent?.analysis_id === analysis.analysis_id
      && ['ready', 'not_required'].includes(analysis.status)
      && !(state.draft?.status === ACTIVE_STATUS && state.draft?.analysis_id === analysis.analysis_id)
    );
    if (!IN_FLIGHT_ANALYSIS_STATUSES.has(analysis.status) && !awaitingManualReviewDraft) return null;
    return {
      transcriptId: String(state.transcriptId),
      signature: [analysis.analysis_id, analysis.status, analysis.updated_at || '', awaitingManualReviewDraft ? 'awaiting-review-draft' : ''].join('|'),
    };
  };
  const clearScheduled = () => {
    if (timeoutId !== null) clearTimeoutFn(timeoutId);
    timeoutId = null;
  };
  const schedule = () => {
    const state = eligibleState();
    if (!state || stopped || realtimeConnected || inFlight || timeoutId !== null) return;
    if (unchangedCycles >= maxUnchangedCycles) return;
    const delay = backoffMs[Math.min(unchangedCycles, backoffMs.length - 1)];
    timeoutId = setTimeoutFn(() => {
      timeoutId = null;
      const requestState = eligibleState();
      if (!requestState || requestState.signature !== analysisSignature || stopped || realtimeConnected || inFlight) return;
      inFlight = true;
      Promise.resolve(refreshWorkspace(requestState.transcriptId))
        .catch(() => null)
        .finally(() => {
          inFlight = false;
          const nextState = eligibleState();
          if (!nextState || stopped || realtimeConnected) return;
          if (nextState.signature === requestState.signature) unchangedCycles += 1;
          schedule();
        });
    }, delay);
  };
  const updateWorkspace = () => {
    const state = eligibleState();
    if (!state) {
      analysisSignature = null;
      unchangedCycles = 0;
      clearScheduled();
      return;
    }
    if (state.signature !== analysisSignature) {
      analysisSignature = state.signature;
      unchangedCycles = 0;
      clearScheduled();
    }
    schedule();
  };

  return {
    updateWorkspace,
    setRealtimeConnected: (connected) => {
      realtimeConnected = Boolean(connected);
      if (realtimeConnected) clearScheduled();
      else updateWorkspace();
    },
    stop: () => {
      stopped = true;
      clearScheduled();
    },
    getState: () => ({ inFlight, unchangedCycles, scheduled: timeoutId !== null }),
  };
}

export function createWorkspaceFetchCoordinator({
  fetcher,
  endpointForTranscript,
  applyWorkspacePayload,
  getActiveTranscriptId,
  requestsByEndpoint = new Map(),
} = {}) {
  let latestSwitchIntent = 0;

  const fetchPayload = (targetTranscriptId) => {
    const endpoint = endpointForTranscript?.(targetTranscriptId);
    if (!endpoint) return Promise.resolve(null);
    if (requestsByEndpoint.has(endpoint)) return requestsByEndpoint.get(endpoint);
    const request = Promise.resolve()
      .then(() => fetcher(endpoint))
      .then(async (response) => (response?.ok ? response.json() : null))
      .catch(() => null)
      .finally(() => requestsByEndpoint.delete(endpoint));
    requestsByEndpoint.set(endpoint, request);
    return request;
  };

  const matchesActiveTranscript = (workspace, expectedTranscriptId) => (
    getActiveTranscriptId?.() === expectedTranscriptId
    && (!expectedTranscriptId || workspace?.active_transcript?.id === expectedTranscriptId)
  );

  return {
    fetchWorkspace: async (targetTranscriptId = getActiveTranscriptId?.(), {
      guardTranscriptId = null,
      allowTranscriptSwitch = false,
    } = {}) => {
      const activeTranscriptAtRequest = getActiveTranscriptId?.() || null;
      const switchIntent = allowTranscriptSwitch ? ++latestSwitchIntent : null;
      const workspace = await fetchPayload(targetTranscriptId);
      if (!workspace) return null;
      if (guardTranscriptId) {
        if (!matchesActiveTranscript(workspace, guardTranscriptId)) return null;
      } else if (allowTranscriptSwitch) {
        if (
          switchIntent !== latestSwitchIntent
          || (targetTranscriptId && workspace?.active_transcript?.id !== targetTranscriptId)
        ) return null;
      } else if (!matchesActiveTranscript(workspace, activeTranscriptAtRequest)) {
        return null;
      }
      applyWorkspacePayload?.(workspace);
      return workspace;
    },
    fetchPayload,
  };
}

// These controls are driven only by server action flags. The browser sees
// counts and document ids, never topic output, provider, quota, or recovery data.
export function createSplitPartialActionsController({
  fetcher = csrfFetch,
  getTranscriptId = () => null,
  refreshWorkspace = async () => null,
  selectDocument = () => {},
  actionsRoot = typeof document !== 'undefined' ? document.querySelector('[data-split-partial-actions]') : null,
  status = actionsRoot?.querySelector('[data-split-partial-status]'),
  retryButton = actionsRoot?.querySelector('[data-split-retry-missing]'),
  keepButton = actionsRoot?.querySelector('[data-split-keep-available]'),
} = {}) {
  let batch = null;
  let inFlight = false;
  const same = (left, right) => String(left || '') === String(right || '');
  const setStatus = (message, kind = '') => {
    if (!status) return;
    status.textContent = message;
    status.dataset.statusKind = kind;
  };
  const setCheckerUnavailableStatus = (presentation) => {
    if (!status) return;
    status.innerHTML = `${CHECKER_UNAVAILABLE_MESSAGE}${generationFailureDetailsHtml(presentation)}`;
    status.dataset.statusKind = 'warning';
  };
  const render = () => {
    const canAct = Boolean(batch?.can_retry_missing || batch?.can_keep_available);
    const completedPartial = batch?.status === 'completed_partial';
    const verifying = batch?.status === 'verifying' || batch?.verification_status === 'verifying';
    const primaryFailed = Boolean(batch?.primary_failed);
    // Controls themselves remain server-flag-only. The completed state stays
    // visible as a safe clinical acknowledgement after both flags turn off.
    const checkerUnavailable = Boolean(batch?.checker_unavailable);
    if (actionsRoot) actionsRoot.hidden = !batch?.batch_id || (!canAct && !completedPartial && !verifying && !checkerUnavailable);
    if (retryButton) { retryButton.hidden = !batch?.can_retry_missing; retryButton.disabled = inFlight || !batch?.can_retry_missing; }
    if (keepButton) {
      keepButton.hidden = !batch?.can_keep_available;
      keepButton.disabled = inFlight || !batch?.can_keep_available;
      // The status is a server-projected, content-free warning. Referencing it
      // from Keep makes the primary failure available to assistive technology
      // at the decision point, not only through a preceding live announcement.
      if (primaryFailed && batch?.can_keep_available) keepButton.setAttribute('aria-describedby', 'split-partial-status');
      else keepButton.removeAttribute('aria-describedby');
    }
    if (checkerUnavailable) {
      setCheckerUnavailableStatus(batch?.checker_failure_presentation);
    } else if (!inFlight) {
      if (verifying) {
        setStatus('Checking generated notes before they are available.', 'progress');
      } else if (completedPartial && primaryFailed) {
        setStatus('The primary note failed. A surviving secondary note was selected.', 'warning');
      } else if (completedPartial) {
        setStatus('Available notes were kept.', 'success');
      } else if (primaryFailed) {
        setStatus('The primary note failed. Keep available notes will select a surviving secondary note.', 'warning');
      } else if (!canAct) {
        setStatus('');
      }
    }
  };
  const applyWorkspaceState = (nextBatch, transcriptId) => {
    batch = nextBatch?.batch_id && same(transcriptId, getTranscriptId()) ? { ...nextBatch, transcriptId } : null;
    render();
  };
  const run = async (action) => {
    const candidate = batch;
    const allowed = action === 'retry' ? candidate?.can_retry_missing : candidate?.can_keep_available;
    if (inFlight || !candidate?.batch_id || !allowed || !same(candidate.transcriptId, getTranscriptId())) return false;
    inFlight = true;
    render();
    setStatus(action === 'retry' ? 'Retrying missing notes.' : 'Keeping available notes.');
    try {
      const suffix = action === 'retry' ? 'retry-missing-notes' : 'keep-available-notes';
      const response = await fetcher(`/api/v1/transcripts/${candidate.transcriptId}/consultation-split-batches/${candidate.batch_id}/${suffix}`, {
        method: 'POST', credentials: 'include',
      });
      if (!same(candidate.transcriptId, getTranscriptId())) return false;
      if (!response.ok) {
        setStatus(action === 'retry' ? 'Could not retry missing notes.' : 'Could not keep available notes.', 'warning');
        return false;
      }
      const result = await response.json();
      if (!same(candidate.transcriptId, getTranscriptId())) return false;
      const workspace = await refreshWorkspace(candidate.transcriptId, { guardTranscriptId: candidate.transcriptId });
      if (!same(candidate.transcriptId, getTranscriptId())) return false;
      const preferred = workspace?.consultation_split_batch?.preferred_document_id
        || (action === 'keep' ? result?.document_ids?.[0] : null);
      if (preferred) selectDocument(String(preferred));
      setStatus(action === 'retry' ? 'Missing notes are being retried.' : 'Available notes kept.', 'success');
      return true;
    } catch (_) {
      if (same(candidate.transcriptId, getTranscriptId())) setStatus(action === 'retry' ? 'Could not retry missing notes.' : 'Could not keep available notes.', 'warning');
      return false;
    } finally {
      inFlight = false;
      render();
    }
  };
  retryButton?.addEventListener('click', () => { void run('retry'); });
  keepButton?.addEventListener('click', () => { void run('keep'); });
  render();
  return { applyWorkspaceState, retry: () => run('retry'), keep: () => run('keep'), getState: () => ({ inFlight, batch }) };
}

// Keep the DOM submit binding thin and make the gate decision testable without
// loading the workspace application shell.
export async function dispatchTemplateGeneration({
  capabilityEnabled = false,
  forceNewReview = false,
  splitController = null,
  transcriptId = null,
  templateId = null,
  confirmedBatchId = null,
  onAccepted = null,
  ordinary = async () => false,
} = {}) {
  if (capabilityEnabled && splitController) {
    if (confirmedBatchId && !forceNewReview) return splitController.regenerateConfirmedBatch({ transcriptId, batchId: confirmedBatchId, onAccepted });
    return splitController.start({ transcriptId, templateId, onAccepted });
  }
  return ordinary();
}

// The marker is usable only after its PATCH succeeds and only while the
// server-derived deployment/owner capability remains available. The intent
// endpoint still decides whether the request is accepted.
export function isConsultationSplitCreateEnabled({
  workspaceEnabled = false,
  capabilityAvailable = false,
  multipleProblemsMarked = false,
} = {}) {
  return Boolean(workspaceEnabled || (capabilityAvailable && multipleProblemsMarked));
}

// This is deliberately limited to the consultation marker. It cannot request
// work itself; Review problems and Create own their explicit request paths.
export function createMultipleProblemsController({
  button = typeof document !== 'undefined' ? document.querySelector('[data-multiple-problems-toggle]') : null,
  fetcher = csrfFetch,
  getTranscriptId = () => null,
  onPendingChange = () => {},
  onPersisted = async () => {},
  showMessage = () => {},
} = {}) {
  let available = false;
  let transcriptId = null;
  let marked = false;
  let pending = false;
  let requestGeneration = 0;
  const render = () => {
    if (!button) return;
    button.hidden = !available || !transcriptId;
    button.disabled = !available || !transcriptId || pending;
    button.setAttribute('aria-pressed', String(marked));
    button.setAttribute('aria-busy', String(pending));
    button.classList.toggle('is-active', marked);
  };
  const applyWorkspaceState = ({ capabilityAvailable, nextTranscriptId, multipleProblems } = {}) => {
    const changedTranscript = transcriptId !== (nextTranscriptId || null);
    available = Boolean(capabilityAvailable);
    transcriptId = nextTranscriptId || null;
    if (changedTranscript && pending) {
      requestGeneration += 1;
      pending = false;
      onPendingChange(false);
    }
    if (!pending) marked = Boolean(multipleProblems);
    render();
  };
  const toggle = async () => {
    if (!available || !transcriptId || pending || transcriptId !== getTranscriptId()) return false;
    const targetTranscriptId = transcriptId;
    const previous = marked;
    const next = !previous;
    const generation = ++requestGeneration;
    marked = next;
    pending = true;
    onPendingChange(true);
    render();
    try {
      const response = await fetcher(`/api/v1/transcripts/${targetTranscriptId}`, {
        method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ multiple_problems: next }),
      });
      if (generation !== requestGeneration || transcriptId !== targetTranscriptId || getTranscriptId() !== targetTranscriptId) return false;
      if (!response.ok) throw new Error('Could not save the consultation setting.');
      const payload = await response.json();
      if (generation !== requestGeneration || transcriptId !== targetTranscriptId || getTranscriptId() !== targetTranscriptId) return false;
      marked = Boolean(payload?.multiple_problems ?? next);
      try {
        await onPersisted({ transcriptId: targetTranscriptId, multipleProblems: marked });
      } catch (_) {
        // The marker is already committed. A refresh is presentation-only and
        // must not turn a successful PATCH into an apparent failed save.
      }
      return true;
    } catch (_) {
      if (generation === requestGeneration && transcriptId === targetTranscriptId && getTranscriptId() === targetTranscriptId) {
        marked = previous;
        showMessage('Could not save Multiple problems. Please try again.', 'error');
      }
      return false;
    } finally {
      if (generation === requestGeneration) {
        pending = false;
        onPendingChange(false);
        render();
      }
    }
  };
  button?.addEventListener('click', () => { void toggle(); });
  render();
  return { applyWorkspaceState, toggle, isPending: () => pending, isMarked: () => marked };
}

// Own the small browser-side part of a deliberate Generate action.  The server
// still owns source validation, idempotency, analysis, and draft validity.
export function createSplitGenerateController({
  fetcher = csrfFetch,
  getCapabilityEnabled = () => false,
  getCapabilityAvailable = getCapabilityEnabled,
  getTranscriptId = () => null,
  getTemplateId = () => null,
  saveSources = async () => {},
  refreshWorkspace = async () => null,
  reviewController = null,
  setBusy = () => {},
  setStatus = () => {},
  setContinueAvailable = () => {},
  getConfirmedDraft = () => null,
  onSplitBatchStarted = () => {},
  onGeneratedDocument = async () => {},
  createKey = () => globalThis.crypto?.randomUUID?.(),
} = {}) {
  let operation = null;
  let generation = 0;
  const draftRequests = new Map();
  const openedDrafts = new Set();

  const same = (left, right) => String(left || '') === String(right || '');
  const current = (candidate) => candidate && candidate.token === generation && operation === candidate;
  const setOperationStatus = (message, kind = 'info') => setStatus(message, kind);
  const reset = () => {
    if (operation) operation.consumePending = false;
    operation = null;
    setBusy(false);
    setContinueAvailable(false);
  };
  const updateContinueAvailability = (candidate = operation) => {
    setContinueAvailable(Boolean(
      current(candidate)
      && candidate.intentId
      && candidate.continueEligible
      && !candidate.consumed
      && !candidate.consumePending
      && same(getTranscriptId(), candidate.transcriptId),
    ));
  };
  const abandonForTranscriptChange = (candidate) => {
    if (!current(candidate)) return;
    candidate.consumePending = false;
    operation = null;
    setBusy(false);
    setOperationStatus('');
    setContinueAvailable(false);
  };
  const failDraftInitialization = (candidate, errorCode = null) => {
    if (!current(candidate)) return;
    candidate.draftState = 'failed';
    candidate.pending = false;
    setBusy(false);
    candidate.continueEligible = false;
    updateContinueAvailability(candidate);
    setOperationStatus(splitDiagnosticMessage({
      stage: 'draft', errorCode, operationId: candidate.analysisId,
    }), 'warning');
  };
  const analysisStatusMessage = (analysis = {}) => {
    const status = analysis?.status || '';
    if (status === 'queued') return 'Preparing your note split. This may take a moment.';
    if (status === 'processing') return 'Preparing your note split.';
    return splitDiagnosticMessage({
      stage: 'analysis',
      errorCode: analysis?.error_code,
      operationId: analysis?.analysis_id,
      reusedAnalysis: status === 'failed',
      analysisStatus: status,
      canContinue: Boolean(operation?.intentId && !operation?.consumed),
    });
  };
  const canUseCurrentOperation = (transcriptId, analysis) => (
    current(operation)
    && same(operation.transcriptId, transcriptId)
    && same(operation.analysisId, analysis?.analysis_id)
  );
  const initializeDraft = async (candidate, analysis) => {
    const key = `${candidate.transcriptId}:${analysis.analysis_id}`;
    if (!current(candidate) || candidate.draftState === 'failed' || candidate.draftState === 'complete') return null;
    if (draftRequests.has(key)) return draftRequests.get(key);
    const request = (async () => {
      try {
        candidate.draftState = 'inflight';
        const response = await fetcher(`/api/v1/transcripts/${candidate.transcriptId}/consultation-split-draft`, {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        });
        if (!current(candidate)) return null;
        if (!same(getTranscriptId(), candidate.transcriptId)) {
          abandonForTranscriptChange(candidate);
          return null;
        }
        if (!response.ok) {
          const error = await readSafeError(response);
          if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId)) {
            if (current(candidate)) abandonForTranscriptChange(candidate);
            return null;
          }
          failDraftInitialization(candidate, error.code);
          return null;
        }
        const draft = await response.json();
        if (!draft || typeof draft !== 'object') {
          failDraftInitialization(candidate);
          return null;
        }
        if (!current(candidate)) return null;
        if (!same(getTranscriptId(), candidate.transcriptId)) {
          abandonForTranscriptChange(candidate);
          return null;
        }
        const workspace = await refreshWorkspace(candidate.transcriptId, { guardTranscriptId: candidate.transcriptId });
        if (current(candidate) && !same(getTranscriptId(), candidate.transcriptId)) {
          abandonForTranscriptChange(candidate);
          return null;
        }
        if (
          !current(candidate)
          || !workspace
          || !same(workspace.consultation_split_draft?.analysis_id, analysis.analysis_id)
        ) {
          failDraftInitialization(candidate);
          return null;
        }
        candidate.draftState = 'complete';
        // The app coordinator normally applies this payload. Apply it here as
        // well so a successful guarded refresh always settles this operation.
        applyWorkspaceState({
          capabilityEnabled: true,
          transcriptId: candidate.transcriptId,
          analysis,
          draft: workspace.consultation_split_draft,
          availableTemplates: workspace.available_templates || [],
        });
        return workspace;
      } catch (_) {
        if (current(candidate) && !same(getTranscriptId(), candidate.transcriptId)) {
          abandonForTranscriptChange(candidate);
          return null;
        }
        failDraftInitialization(candidate);
        return null;
      } finally {
        draftRequests.delete(key);
      }
    })();
    draftRequests.set(key, request);
    return request;
  };
  const applyWorkspaceState = ({
    capabilityEnabled,
    capabilityAvailable = capabilityEnabled,
    transcriptId,
    analysis,
    draft,
    availableTemplates = [],
  } = {}) => {
    // Workspace callbacks can arrive while a session switch is in progress.
    // Never leave the old Generate operation owning the new workspace's busy UI.
    if (current(operation) && (!same(operation.transcriptId, transcriptId) || !same(getTranscriptId(), operation.transcriptId))) {
      abandonForTranscriptChange(operation);
      return;
    }
    // A read arriving before Create's response cannot establish this
    // submission's analysis binding or clear its acceptance callback.
    if (current(operation) && operation.pending && !operation.analysisId) return;
    if (
      (!capabilityEnabled && !(capabilityAvailable && operation?.manualReviewRequested))
      || !canUseCurrentOperation(transcriptId, analysis)
    ) {
      // Workspace state without the browser's matching operation is passive.
      // It must never retain an earlier actionable control.
      setContinueAvailable(false);
      return;
    }
    const status = analysis?.status || '';
    operation.continueEligible = false;
    if (IN_FLIGHT_ANALYSIS_STATUSES.has(status)) {
      updateContinueAvailability(operation);
      setOperationStatus(operation.submissionAccepted && operation.intentId
        ? (operation.manualReviewRequested
          ? 'Suggested problems are being prepared. This request is saved and will continue if you leave this page.'
          : 'Your note split is being prepared. This request is saved and will continue if you leave this page.')
        : analysisStatusMessage(analysis), operation.submissionAccepted ? 'progress' : 'info');
      return;
    }
    if (status === 'not_required' && operation.manualReviewRequested && operation.intentId && !operation.consumed) {
      if (!draft || !same(draft.analysis_id, analysis.analysis_id)) {
        operation.pending = false;
        operation.retryable = false;
        updateContinueAvailability(operation);
        setOperationStatus('Preparing note split review.');
        void initializeDraft(operation, analysis);
        return;
      }
      const draftKey = `${transcriptId}:${analysis.analysis_id}:${draft.draft_id || ''}`;
      operation.draftState = 'complete';
      operation.pending = false;
      setBusy(false);
      operation.continueEligible = draft.status === ACTIVE_STATUS;
      updateContinueAvailability(operation);
      setOperationStatus('Review the proposed note split.');
      if (!openedDrafts.has(draftKey)) {
        openedDrafts.add(draftKey);
        reviewController?.applyWorkspaceState?.({ draft, availableTemplates, nextTranscriptId: transcriptId, manualReview: true });
        reviewController?.open?.();
      }
      return;
    }
    // The document callback refreshes workspace state before this successful
    // continuation publishes its final status. That projection still reports
    // the same `not_required` analysis, but the browser-owned intent is
    // already consumed, so it cannot mean that no note was generated.
    if (status === 'not_required' && operation.consumed) return;
    if (NO_NOTE_ANALYSIS_STATUSES.has(status) || !analysis?.analysis_id) {
      if (status === 'not_required' && operation.intentId && !operation.consumed) {
        // The analysis has established that this consultation needs one note.
        // Consume the browser-owned intent immediately with the template that
        // was frozen when Generate was pressed; no second confirmation is
        // needed when there is no split to review.
        operation.pending = false;
        operation.retryable = false;
        operation.continueEligible = false;
        updateContinueAvailability(operation);
        void continueAsOneNote();
        return;
      }
      setOperationStatus(analysisStatusMessage(analysis), 'warning');
      // Keep this browser-owned durable intent available for the explicit
      // one-note action. It remains retryable so a clinician can instead
      // start a new Generate with a currently available template.
      if (operation.intentId && !operation.consumed) {
        operation.pending = false;
        operation.retryable = true;
        operation.continueEligible = true;
        setBusy(false);
        updateContinueAvailability(operation);
      } else {
        reset();
      }
      return;
    }
    if (status !== 'ready') {
      updateContinueAvailability(operation);
      return;
    }
    if (
      draft
      && TERMINAL_STATUSES.has(draft.status)
      && operation.draftState === 'complete'
      && !operation.pending
    ) {
      // Confirmation consumed this browser-owned Generate action. Its batch
      // remains immutable, but a later main Regenerate must create a new
      // intent rather than being mistaken for a duplicate click.
      reset();
      return;
    }
    if (!draft || !same(draft.analysis_id, analysis.analysis_id)) {
      updateContinueAvailability(operation);
      setOperationStatus('Preparing note split review.');
      void initializeDraft(operation, analysis);
      return;
    }
    const draftKey = `${transcriptId}:${analysis.analysis_id}:${draft.draft_id || ''}`;
    setOperationStatus('Review the proposed note split.');
    operation.draftState = 'complete';
    setBusy(false);
    // A saved intent is also explicitly consumable while the clinician reviews
    // it. It is never exposed while analysis is still queued or processing,
    // nor for a passive/restored draft without this browser operation.
    operation.continueEligible = draft.status === ACTIVE_STATUS;
    updateContinueAvailability(operation);
    if (!openedDrafts.has(draftKey)) {
      openedDrafts.add(draftKey);
      reviewController?.applyWorkspaceState?.({ draft, availableTemplates, nextTranscriptId: transcriptId, manualReview: operation.manualReviewRequested });
      reviewController?.open?.();
    }
  };
  const notifyAccepted = (candidate, onAccepted) => {
    if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId) || typeof onAccepted !== 'function') return;
    try {
      onAccepted({ transcriptId: candidate.transcriptId });
    } catch (_) {
      // The request is already accepted. A caller's cosmetic follow-up must
      // not change its durable operation state or retry semantics.
    }
  };
  const start = async ({
    transcriptId: requestedTranscriptId = getTranscriptId(),
    templateId: requestedTemplateId = getTemplateId(),
    onAccepted = null,
    manualReviewRequested = false,
    retryFailedAnalysis = false,
    forceNewReview = false,
  } = {}) => {
    if (!(manualReviewRequested ? getCapabilityAvailable() : getCapabilityEnabled())) {
      setContinueAvailable(false);
      return false;
    }
    const transcriptId = requestedTranscriptId;
    const templateId = requestedTemplateId;
    if (!transcriptId || !templateId) return true;
    if (operation && operation.pending) return true;
    if (operation && operation.draftState === 'failed' && same(operation.transcriptId, transcriptId) && same(operation.templateId, templateId)) {
      operation.draftState = 'idle';
      operation.pending = true;
      setBusy(true);
      setOperationStatus('Retrying note split review.');
      void initializeDraft(operation, { analysis_id: operation.analysisId });
      return true;
    }
    if (operation?.draftState === 'failed') {
      // A changed selection is a new deliberate Generate action, not a retry.
      operation = null;
      generation += 1;
    }
    const requestedManualReview = Boolean(manualReviewRequested);
    const requestedFailedAnalysisRetry = Boolean(retryFailedAnalysis);
    const hasAmbiguousSubmission = operation
      && operation.retryable
      && !operation.submissionAccepted
      && same(operation.transcriptId, transcriptId)
      && same(operation.templateId, templateId);
    const reuseAmbiguousSubmission = hasAmbiguousSubmission
      && operation.manualReviewRequested === requestedManualReview
      && operation.retryFailedAnalysis === requestedFailedAnalysisRetry;
    if (hasAmbiguousSubmission && !reuseAmbiguousSubmission) {
      // The server may have accepted the previous request. It must be replayed
      // with its original meaning before any different action can begin.
      setBusy(false);
      setOperationStatus('The previous request has not been confirmed. Retry the original action before starting a new review.', 'warning');
      return false;
    }
    if (operation && !operation.retryable && !retryFailedAnalysis && !forceNewReview) return true;
    if ((retryFailedAnalysis || forceNewReview) && !reuseAmbiguousSubmission) {
      // Retrying a terminal analysis is an explicit fresh action. Reusing the
      // old browser key would replay its terminal response rather than queue
      // the server-authorized retry attempt.
      operation = null;
      generation += 1;
    }
    // A transport failure leaves the server outcome unknown. Keep the exact
    // candidate for a same-source retry, including an explicit analysis retry.
    const retry = reuseAmbiguousSubmission;
    const candidate = retry ? operation : {
      token: ++generation,
      transcriptId,
      templateId,
      key: createKey(),
      pending: true,
      retryable: false,
      requestStarted: false,
      submissionAccepted: false,
      draftState: 'idle',
      analysisId: null,
      intentId: null,
      consumePending: false,
      consumed: false,
      continueEligible: false,
      manualReviewRequested: Boolean(manualReviewRequested),
      retryFailedAnalysis: Boolean(retryFailedAnalysis),
    };
    if (!candidate.key) throw new Error('Could not start note split.');
    operation = candidate;
    candidate.pending = true;
    candidate.retryable = false;
    candidate.continueEligible = false;
    updateContinueAvailability(candidate);
    setBusy(true);
    setOperationStatus('Saving consultation notes before preparing a note split.');
    try {
      await saveSources({ transcriptId: candidate.transcriptId });
      if (!current(candidate)) return true;
      if (!same(getTranscriptId(), candidate.transcriptId)) {
        abandonForTranscriptChange(candidate);
        return true;
      }
      candidate.requestStarted = true;
      const response = await fetcher(`/api/v1/transcripts/${candidate.transcriptId}/consultation-split-intents`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_idempotency_key: candidate.key,
          selected_template_id: candidate.templateId,
          ...(candidate.manualReviewRequested ? { manual_review_requested: true } : {}),
          ...(candidate.retryFailedAnalysis ? { retry_failed_analysis: true } : {}),
        }),
      });
      if (!current(candidate)) return true;
      if (!same(getTranscriptId(), candidate.transcriptId)) {
        abandonForTranscriptChange(candidate);
        return true;
      }
      if (!response.ok) {
        const error = await readSafeError(response);
        if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId)) {
          if (current(candidate)) abandonForTranscriptChange(candidate);
          return true;
        }
        candidate.retryable = response.status >= 500;
        candidate.pending = false;
        setOperationStatus(splitDiagnosticMessage({ stage: 'submission', errorCode: error.code }), 'warning');
        if (!candidate.retryable) reset(); else setBusy(false);
        return true;
      }
      const payload = await response.json();
      if (!current(candidate)) return true;
      if (!same(getTranscriptId(), candidate.transcriptId)) {
        abandonForTranscriptChange(candidate);
        return true;
      }
      // A valid response establishes a server-known operation. A later
      // terminal analysis retry needs a fresh key; only an ambiguous transport
      // failure is safe to replay with this key.
      candidate.submissionAccepted = true;
      candidate.analysisId = payload?.analysis?.analysis_id || null;
      candidate.intentId = payload?.intent_id || null;
      candidate.manualReviewRequested = Boolean(payload?.manual_review_requested ?? candidate.manualReviewRequested);
      candidate.pending = false;
      notifyAccepted(candidate, onAccepted);
      applyWorkspaceState({ capabilityEnabled: true, transcriptId: candidate.transcriptId, analysis: payload?.analysis || null });
      // An accepted intent commonly starts with queued analysis. The local
      // response above is enough to keep this controller coherent, but the
      // workspace-level restoration poller reads the server projection. Refresh
      // it now so that a later ready/not-required transition is observed even
      // when SSE is unavailable and the user has not pressed Create again.
      if (IN_FLIGHT_ANALYSIS_STATUSES.has(payload?.analysis?.status || '')) {
        await refreshWorkspace(candidate.transcriptId, { guardTranscriptId: candidate.transcriptId });
      }
      return true;
    } catch (_) {
      if (current(candidate)) {
        if (!same(getTranscriptId(), candidate.transcriptId)) {
          abandonForTranscriptChange(candidate);
          return true;
        }
        candidate.pending = false;
        candidate.retryable = Boolean(candidate.requestStarted);
        if (candidate.retryable) {
          setBusy(false);
          setOperationStatus(splitDiagnosticMessage({ stage: 'submission' }), 'warning');
        } else {
          setOperationStatus(splitDiagnosticMessage({ stage: 'submission' }), 'warning');
          reset();
        }
      }
      return true;
    }
  };
  const continueAsOneNote = async ({
    transcriptId: restoredTranscriptId = null,
    intentId: restoredIntentId = null,
  } = {}) => {
    let candidate = operation;
    if (!candidate && restoredTranscriptId && restoredIntentId && same(getTranscriptId(), restoredTranscriptId)) {
      candidate = {
        token: ++generation,
        transcriptId: restoredTranscriptId,
        intentId: restoredIntentId,
        pending: false,
        retryable: true,
        consumePending: false,
        consumed: false,
        continueEligible: true,
        restored: true,
      };
      operation = candidate;
    }
    if (
      restoredTranscriptId
      && (!same(candidate?.transcriptId, restoredTranscriptId) || !same(candidate?.intentId, restoredIntentId))
    ) return false;
    if (!candidate?.intentId || candidate.consumed || candidate.consumePending || !current(candidate)) return false;
    if (!same(getTranscriptId(), candidate.transcriptId)) {
      abandonForTranscriptChange(candidate);
      return false;
    }
    candidate.consumePending = true;
    updateContinueAvailability(candidate);
    setBusy(true);
    setOperationStatus('Starting one note.');
    try {
      const response = await fetcher(
        `/api/v1/transcripts/${candidate.transcriptId}/consultation-split-intents/${candidate.intentId}/continue-as-one-note`,
        { method: 'POST', credentials: 'include' },
      );
      if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId)) {
        if (current(candidate)) abandonForTranscriptChange(candidate);
        return false;
      }
      const payload = response.ok ? await response.json() : null;
      if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId)) {
        if (current(candidate)) abandonForTranscriptChange(candidate);
        return false;
      }
      if (!response.ok) {
        const error = await readSafeError(response);
        if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId)) return false;
        setOperationStatus(
          splitDiagnosticMessage({
            stage: 'continuation', errorCode: error.code, operationId: candidate.intentId,
          }),
          'warning',
        );
        return false;
      }
      if (payload?.consumed_document_deleted) {
        candidate.consumed = true;
        updateContinueAvailability(candidate);
        setOperationStatus('This request was already consumed. Its generated note was deleted and will not be recreated.', 'warning');
        return true;
      }
      if (!payload?.document) {
        setOperationStatus('Could not confirm the generated note. Refresh the workspace before trying again.', 'warning');
        return false;
      }
      candidate.consumed = true;
      updateContinueAvailability(candidate);
      await onGeneratedDocument(payload.document, {
        replayed: Boolean(payload.idempotency_replayed),
        transcriptId: candidate.transcriptId,
      });
      if (!current(candidate) || !same(getTranscriptId(), candidate.transcriptId)) return true;
      setOperationStatus(payload.idempotency_replayed ? 'Opened the existing one-note request.' : 'Queued one note.', 'success');
      reset();
      return true;
    } catch (_) {
      if (current(candidate) && same(getTranscriptId(), candidate.transcriptId)) {
        setOperationStatus(splitDiagnosticMessage({
          stage: 'continuation', operationId: candidate.intentId,
        }), 'warning');
      }
      return false;
    } finally {
      if (current(candidate)) {
        candidate.consumePending = false;
        setBusy(false);
        updateContinueAvailability(candidate);
      }
    }
  };
  const regenerateConfirmedBatch = async ({
    transcriptId: requestedTranscriptId = getTranscriptId(),
    batchId,
    onAccepted = null,
  } = {}) => {
    const transcriptId = requestedTranscriptId;
    if (!getCapabilityEnabled() || !transcriptId || !batchId || operation?.pending) return false;
    const candidate = { token: ++generation, transcriptId, pending: true };
    operation = candidate;
    setBusy(true);
    setContinueAvailable(false);
    setOperationStatus('Regenerating split notes from the previous instructions.');
    onSplitBatchStarted({
      draft: getConfirmedDraft(),
      batchId,
      phase: 'generation_queued',
      pendingBatchId: true,
    });
    try {
      const key = createKey();
      if (!key) throw new Error('Could not regenerate split notes.');
      const response = await fetcher(`/api/v1/transcripts/${transcriptId}/consultation-split-batches/${batchId}/regenerate`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_idempotency_key: key }),
      });
      if (!current(candidate) || !same(getTranscriptId(), transcriptId)) return false;
      if (!response.ok) {
        const error = await readSafeError(response);
        if (!current(candidate) || !same(getTranscriptId(), transcriptId)) {
          if (current(candidate)) abandonForTranscriptChange(candidate);
          return false;
        }
        onSplitBatchStarted({ draft: getConfirmedDraft(), batchId, phase: 'failed' });
        setOperationStatus(splitDiagnosticMessage({
          stage: 'regeneration', errorCode: error.code, operationId: batchId,
        }), 'warning');
        return false;
      }
      const result = await response.json();
      notifyAccepted(candidate, onAccepted);
      onSplitBatchStarted({
        draft: getConfirmedDraft(),
        batchId: result?.batch_id || batchId,
        phase: 'generation_queued',
      });
      await refreshWorkspace(transcriptId, { guardTranscriptId: transcriptId });
      if (!current(candidate) || !same(getTranscriptId(), transcriptId)) return false;
      setOperationStatus('Split notes are being regenerated.', 'success');
      return true;
    } catch (_) {
      if (current(candidate) && same(getTranscriptId(), transcriptId)) {
        onSplitBatchStarted({ draft: getConfirmedDraft(), batchId, phase: 'failed' });
      }
      if (current(candidate) && same(getTranscriptId(), transcriptId)) {
        setOperationStatus(splitDiagnosticMessage({ stage: 'regeneration', operationId: batchId }), 'warning');
      }
      return false;
    } finally {
      if (current(candidate)) reset();
    }
  };
  return { start, regenerateConfirmedBatch, continueAsOneNote, applyWorkspaceState, getOperation: () => operation };
}

export function enforcePrimaryDisposition(topic) {
  if (!topic || typeof topic !== 'object') return topic;
  return {
    ...topic,
    is_primary: Boolean(topic.is_primary),
    disposition: topic.is_primary ? 'separate_note' : (VALID_DISPOSITIONS.has(topic.disposition)
      ? topic.disposition
      : 'separate_note'),
  };
}

export function normalizeSplitDraft(draft) {
  if (!draft || typeof draft !== 'object') return null;
  const malformed = !Array.isArray(draft.topics) || draft.topics.length > 6;
  const topics = !malformed
    ? draft.topics.map((topic, index) => enforcePrimaryDisposition({
      topic_uuid: topic?.topic_uuid ?? null,
      // Browser-only key for unsaved clinician additions. It is used only for
      // DOM identity and is deliberately omitted from the replacement payload.
      client_key: topic?.client_key ?? null,
      title: asString(topic?.title),
      order: Number.isInteger(topic?.order) ? topic.order : index,
      is_primary: Boolean(topic?.is_primary),
      disposition: topic?.disposition,
      template_id: topic?.template_id ?? null,
      template_version_id: topic?.template_version_id ?? null,
    }))
    : [];
  return {
    draft_id: draft.draft_id ?? null,
    analysis_id: draft.analysis_id ?? null,
    status: malformed ? 'unavailable' : asString(draft.status),
    created_at: draft.created_at ?? null,
    updated_at: draft.updated_at ?? null,
    topics,
    malformed,
  };
}

export function validateSplitDraft(draft) {
  if (!draft || draft.status !== ACTIVE_STATUS || draft.malformed) {
    return { valid: false, message: 'This split is not available for editing.' };
  }
  const topics = Array.isArray(draft.topics) ? draft.topics : [];
  const titles = new Set();
  for (const topic of topics) {
    const title = normalizeTitle(topic?.title);
    if (!title) return { valid: false, message: 'Each topic needs a title.' };
    if (topic?.disposition === 'separate_note' && !asString(topic?.template_id)) {
      return { valid: false, message: 'Choose a template for each separate note.' };
    }
    const key = titleComparisonKey(title);
    if (titles.has(key)) return { valid: false, message: 'Topic titles must be distinct.' };
    titles.add(key);
  }
  if (!topics.length) return { valid: true, message: '' };
  const primaries = topics.filter((topic) => topic?.is_primary);
  if (primaries.length !== 1) return { valid: false, message: 'Choose exactly one primary topic.' };
  if (primaries[0].disposition !== 'separate_note') {
    return { valid: false, message: 'The primary topic must be a separate note.' };
  }
  return { valid: true, message: '' };
}

export function validateSplitDraftForConfirmation(draft) {
  const validation = validateSplitDraft(draft);
  if (!validation.valid) return validation;
  const separateNoteCount = (draft?.topics || []).filter(
    (topic) => topic?.disposition === 'separate_note',
  ).length;
  if (separateNoteCount < 2 || separateNoteCount > 6) {
    return {
      valid: false,
      message: 'Add another separate note to create a note split.',
    };
  }
  return { valid: true, message: '' };
}

export function isAllMergedOneNoteDraft(draft) {
  const topics = draft?.topics;
  if (!Array.isArray(topics) || topics.length < 2) return false;
  const primary = topics.filter((topic) => topic?.is_primary);
  return primary.length === 1
    && primary[0].disposition === 'separate_note'
    && topics.every((topic) => topic?.is_primary || topic?.disposition === 'include_in_primary');
}

export function serializeSplitDraftTopics(topics) {
  return (Array.isArray(topics) ? topics : []).slice(0, 6).map((topic) => {
    const normalized = enforcePrimaryDisposition(topic || {});
    return {
      ...(normalized.topic_uuid ? { topic_uuid: normalized.topic_uuid } : {}),
      title: normalizeTitle(normalized.title),
      is_primary: Boolean(normalized.is_primary),
      disposition: normalized.disposition,
      template_id: normalized.template_id || null,
    };
  });
}

export function serializeSplitDraft(draft, expectedUpdatedAt = draft?.updated_at) {
  return {
    expected_updated_at: expectedUpdatedAt,
    topics: serializeSplitDraftTopics(draft?.topics),
  };
}

const templateId = (template) => asString(template?.id || template?.template_id);
const templateName = (template) => asString(template?.name) || 'Untitled template';

function latestVersion(template) {
  if (template?.latest_version) return template.latest_version;
  if (Array.isArray(template?.versions) && template.versions.length) {
    return [...template.versions].sort((a, b) => (b.version_no || 0) - (a.version_no || 0))[0];
  }
  return null;
}

function focusableElements(root) {
  if (!root) return [];
  return [...root.querySelectorAll('button, input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((element) => !element.disabled && !element.hidden && !element.closest('[hidden]'));
}

function safeFocus(element) {
  if (!element || typeof element.focus !== 'function') return;
  try { element.focus({ preventScroll: true }); } catch (_) { element.focus(); }
}

function isUsableFocusTarget(element) {
  if (!element || typeof element.focus !== 'function' || element.hidden || element.disabled) return false;
  if (element.getAttribute?.('aria-hidden') === 'true' || element.getAttribute?.('aria-disabled') === 'true') return false;
  return !element.closest?.('[hidden], [inert]');
}

function setDisabled(root, disabled) {
  root?.querySelectorAll('input, select, textarea, button').forEach((element) => {
    element.disabled = disabled;
  });
}

async function readSafeError(response) {
  try {
    const payload = await response.json();
    return {
      code: payload?.error?.code || payload?.code || null,
    };
  } catch (_) {
    return { code: null };
  }
}

export function createSplitReviewController({
  trigger = typeof document !== 'undefined' ? document.querySelector('[data-split-review-trigger]') : null,
  modal = typeof document !== 'undefined' ? document.querySelector('[data-split-review-modal]') : null,
  topicList = modal?.querySelector('[data-split-review-topic-list]'),
  status = modal?.querySelector('[data-split-review-status]'),
  problemCount = modal?.querySelector('[data-split-review-problem-count]'),
  saveButton = modal?.querySelector('[data-split-review-save]'),
  createButton = modal?.querySelector('[data-split-review-create]'),
  addButton = modal?.querySelector('[data-split-review-add]'),
  suggestButton = modal?.querySelector('[data-split-review-suggest]'),
  continueButton = modal?.querySelector('[data-split-review-continue]'),
  confirmButton = modal?.querySelector('[data-split-review-confirm]'),
  closeButtons = modal ? [...modal.querySelectorAll('[data-split-review-close]')] : [],
  fetcher = csrfFetch,
  refreshWorkspace = async () => null,
  getTranscriptId = () => null,
  showMessage = () => {},
  continueAsOneNote = async () => false,
  beginEdit = async () => false,
  suggestProblems = async () => false,
  getConfirmIntentId = () => null,
  onSplitBatchStarted = () => {},
  confirmDiscard = () => (typeof window !== 'undefined' && typeof window.confirm === 'function'
    ? window.confirm('Discard unsaved changes and review this split later?')
    : true),
} = {}) {
  let remoteDraft = null;
  let localDraft = null;
  let templates = [];
  let transcriptId = null;
  let expectedUpdatedAt = null;
  let dirty = false;
  let opened = false;
  let opener = null;
  let saving = false;
  let saveGeneration = 0;
  let continuing = false;
  let confirming = false;
  let continueAvailable = false;
  let latestBatch = null;
  let editPreparationKey = null;
  let editPreparation = null;
  let nextClientTopicKey = 0;
  let manualReviewRequested = false;
  let capabilityAvailable = false;
  let capabilityKnown = false;
  let latestAnalysis = null;
  let latestIntent = null;
  let sourceReady = false;
  let sourceReadinessMessage = '';
  let suggesting = false;
  let templateAvailabilityFresh = false;
  let templateAvailabilityRefreshing = false;
  let templateRefreshGeneration = 0;
  let suggestGeneration = 0;
  const createNotesButton = createButton || confirmButton;

  const setOpenState = (isOpen) => {
    if (modal) modal.hidden = !isOpen;
    if (trigger) trigger.setAttribute('aria-expanded', String(Boolean(isOpen)));
  };
  setOpenState(false);

  const isReadOnly = () => !localDraft || localDraft.status !== ACTIVE_STATUS;
  const hasPrimary = () => {
    const topics = localDraft?.topics || [];
    return topics.length === 0 || topics.filter((topic) => topic.is_primary).length === 1;
  };

  const setStatus = (message, kind = '') => {
    if (!status) return;
    status.textContent = message || '';
    status.dataset.statusKind = kind;
  };

  const analysisStatus = () => latestAnalysis?.status
    || (latestIntent?.status === 'analysis_pending' ? 'queued' : '');
  const needsFreshManualIntent = () => Boolean(localDraft && !getConfirmIntentId());
  const batchGenerationActive = () => IN_FLIGHT_BATCH_STATUSES.has(latestBatch?.status);
  const suggestionAction = () => {
    if (analysisStatus() === 'failed') return { label: 'Retry analysis', retry: true };
    if (analysisStatus() === 'stale' || localDraft?.status === 'stale') return { label: 'Update suggestions', retry: false };
    if (needsFreshManualIntent()) return { label: 'Use these problems', retry: false };
    if (localDraft?.status === 'confirmed') return { label: 'Suggest problems', retry: false };
    return { label: 'Suggest problems', retry: false };
  };
  const suggestionUnavailableMessage = () => {
    if (!transcriptId) return 'Open a consultation before reviewing problems.';
    if (!sourceReady) return sourceReadinessMessage || 'Save consultation content before suggesting problems.';
    if (IN_FLIGHT_ANALYSIS_STATUSES.has(analysisStatus()) || suggesting) return 'Preparing suggested problems. This may take a moment.';
    if (analysisStatus() === 'failed') return splitDiagnosticMessage({
      stage: 'analysis', errorCode: latestAnalysis?.error_code, operationId: latestAnalysis?.analysis_id, reusedAnalysis: true,
    });
    if (analysisStatus() === 'stale' || localDraft?.status === 'stale') return 'The consultation changed after these suggestions. Update suggestions to use the current consultation.';
    return 'Reviewing problems does not generate notes. Suggest problems saves the current consultation and prepares an editable review.';
  };

  const updateControlState = () => {
    const readOnly = isReadOnly();
    const controlsDisabled = readOnly || saving || continuing || confirming;
    setDisabled(topicList, controlsDisabled);
    closeButtons.forEach((button) => { button.disabled = saving || continuing || confirming; });
    if (modal) modal.setAttribute('aria-busy', String(saving || continuing || confirming));
    const validation = readOnly ? { valid: false } : validateSplitDraft(localDraft);
    const confirmationValidation = readOnly ? { valid: false } : validateSplitDraftForConfirmation(localDraft);
    const availableTemplateIds = new Set(templates.map(templateId).filter(Boolean));
    const missingSelectedTemplate = (localDraft?.topics || []).some((topic) => (
      topic.disposition === 'separate_note'
      && topic.template_id
      && !availableTemplateIds.has(asString(topic.template_id))
    ));
    const shouldContinueAsOneNote = continueAvailable && isAllMergedOneNoteDraft(localDraft);
    if (saveButton) {
      saveButton.textContent = shouldContinueAsOneNote ? 'Continue as one note' : 'Save split';
      saveButton.disabled = controlsDisabled || templateAvailabilityRefreshing
        || !validation.valid
        || (!dirty && !shouldContinueAsOneNote);
    }
    if (addButton) {
      const atTopicLimit = (localDraft?.topics?.length || 0) >= 6;
      addButton.hidden = readOnly;
      addButton.disabled = controlsDisabled || atTopicLimit;
    }
    if (continueButton) {
      continueButton.hidden = !continueAvailable || shouldContinueAsOneNote;
      continueButton.disabled = !continueAvailable || shouldContinueAsOneNote || controlsDisabled || templateAvailabilityRefreshing;
    }
    if (suggestButton) {
      const action = suggestionAction();
      const analysisPending = IN_FLIGHT_ANALYSIS_STATUSES.has(analysisStatus());
      const canSuggest = capabilityAvailable
        && Boolean(transcriptId)
        && !saving
        && !continuing
        && !confirming
        && !templateAvailabilityRefreshing
        && !batchGenerationActive()
        && !analysisPending
        && !suggesting
        && (!localDraft || readOnly || needsFreshManualIntent());
      suggestButton.hidden = !capabilityAvailable || (!readOnly && Boolean(localDraft) && !needsFreshManualIntent());
      suggestButton.textContent = action.label;
      suggestButton.disabled = !canSuggest || !sourceReady;
    }
    if (createNotesButton) {
      const confirmable = !readOnly
        && !dirty
        && confirmationValidation.valid
        && templateAvailabilityFresh
        && !missingSelectedTemplate
        && Boolean(getConfirmIntentId());
      createNotesButton.hidden = !confirmable && !confirming;
      createNotesButton.disabled = controlsDisabled || templateAvailabilityRefreshing || !confirmable || confirming;
    }
    if (!readOnly && !validation.valid && dirty) {
      setStatus(validation.message, 'warning');
    } else if (!readOnly && !confirmationValidation.valid && !status?.textContent) {
      setStatus(confirmationValidation.message, 'one-note-guidance');
    } else if (
      !readOnly
      && confirmationValidation.valid
      && status?.dataset.statusKind === 'one-note-guidance'
    ) {
      setStatus('');
    }
  };

  const getPrimaryTopic = () => (localDraft?.topics || []).find((topic) => topic.is_primary);

  const updateSplitReviewCounts = () => {
    const topics = localDraft?.topics || [];
    const noteCount = topics.filter((topic) => topic.disposition === 'separate_note').length;
    if (problemCount) {
      problemCount.textContent = `${topics.length} problem${topics.length === 1 ? '' : 's'} in review`;
    }
    if (createNotesButton) {
      createNotesButton.textContent = `Create ${noteCount} note${noteCount === 1 ? '' : 's'}`;
    }
  };

  const topicTemplateOptions = (selectedId) => {
    const options = [{ value: '', label: 'Choose a template' }];
    templates.forEach((template) => {
      const id = templateId(template);
      if (!id) return;
      const version = latestVersion(template);
      options.push({
        value: id,
        label: `${templateName(template)}${version?.mode ? ` (${version.mode})` : ''}`,
      });
    });
    if (selectedId && !options.some((option) => option.value === selectedId)) {
      options.push({ value: selectedId, label: 'Unavailable template' });
    }
    return options;
  };

  const render = () => {
    if (!topicList) return;
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    const topicKey = active?.dataset?.topicKey;
    const focusAttribute = active?.hasAttribute?.('data-split-review-title')
      ? 'data-split-review-title'
      : active?.hasAttribute?.('data-split-review-template')
        ? 'data-split-review-template'
        : null;
    const selection = focusAttribute === 'data-split-review-title' && Number.isInteger(active.selectionStart)
      ? [active.selectionStart, active.selectionEnd, active.selectionDirection]
      : null;
    topicList.replaceChildren();
    const readOnly = isReadOnly();
    const topics = localDraft?.topics || [];
    if (!localDraft) {
      setStatus(suggestionUnavailableMessage(), IN_FLIGHT_ANALYSIS_STATUSES.has(analysisStatus()) || suggesting ? 'progress' : 'warning');
    } else if (localDraft?.status === 'stale') {
      setStatus('This split is stale and read-only because the consultation changed.', 'warning');
    } else if (localDraft?.status === 'confirmed' && EDITABLE_BATCH_STATUSES.has(latestBatch?.status)) {
      setStatus('This completed split is read-only. Select Suggest problems to prepare a new review.', 'warning');
    } else if (localDraft?.status === 'confirmed') {
      setStatus('This split is queued and locked until generation finishes.', 'warning');
    } else if (localDraft && (TERMINAL_STATUSES.has(localDraft.status) || localDraft.status === 'unavailable')) {
      setStatus('This split is no longer available for editing.', 'warning');
    } else if (manualReviewRequested && topics.length < 2) {
      setStatus(
        topics.length === 0
          ? 'No separate problems were detected. Add a problem or continue as one note.'
          : 'One problem was detected. Add another problem or continue as one note.',
        'one-note-guidance',
      );
    } else if (needsFreshManualIntent() && !dirty) {
      setStatus('These saved problems are read-only until you select Use these problems.', 'warning');
    } else if (!dirty) {
      setStatus('');
    }
    if (!topics.length) {
      const empty = document.createElement('p');
      empty.className = 'split-review-modal__empty';
      empty.textContent = localDraft
        ? (readOnly ? 'No reviewable note topics are available.' : 'No topics were proposed.')
        : 'Suggestions appear here after you explicitly request them.';
      topicList.append(empty);
    }
    topics.forEach((topic) => {
      const fieldset = document.createElement('fieldset');
      fieldset.className = 'split-review-topic';
      fieldset.dataset.topicUuid = asString(topic.topic_uuid);
      fieldset.dataset.topicKey = asString(topic.client_key || topic.topic_uuid);

      if (topic.disposition === 'include_in_primary' || topic.disposition === 'exclude_from_notes') {
        const merged = topic.disposition === 'include_in_primary';
        fieldset.classList.add(merged ? 'is-merged' : 'is-skipped');

        const header = document.createElement('div');
        header.className = 'split-review-topic__header';
        const heading = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'split-review-topic__name';
        name.textContent = topic.title;
        const description = document.createElement('div');
        description.className = 'split-review-topic__description';
        description.textContent = topic.client_key ? 'Added problem' : 'Detected problem';
        heading.append(name, description);
        header.append(heading);
        fieldset.append(header);

        const state = document.createElement('div');
        state.className = 'split-review-topic__state';
        const stateText = document.createElement('span');
        stateText.textContent = merged
          ? `Merged into ${getPrimaryTopic()?.title || 'primary problem'}`
          : 'Skipped — no note will be created';
        const undo = document.createElement('button');
        undo.type = 'button';
        undo.className = 'split-review-topic__text-action';
        undo.dataset.splitReviewUndo = '';
        undo.textContent = 'Undo';
        state.append(stateText, undo);
        fieldset.append(state);
        topicList.append(fieldset);
        return;
      }

      const header = document.createElement('div');
      header.className = 'split-review-topic__header';

      const heading = document.createElement('div');
      const name = document.createElement('div');
      name.className = 'split-review-topic__name';
      name.textContent = topic.title;
      const description = document.createElement('div');
      description.className = 'split-review-topic__description';
      description.textContent = topic.is_primary ? 'Main problem' : 'Will create a separate note';
      heading.append(name, description);
      header.append(heading);

      if (topic.is_primary) {
        fieldset.classList.add('is-primary');
        const primaryLabel = document.createElement('span');
        primaryLabel.className = 'split-review-topic__primary-label';
        primaryLabel.textContent = 'Primary';
        header.append(primaryLabel);
      } else {
        const makePrimary = document.createElement('button');
        makePrimary.type = 'button';
        makePrimary.className = 'split-review-topic__text-action';
        makePrimary.dataset.splitReviewMakePrimary = '';
        makePrimary.textContent = 'Make primary';
        header.append(makePrimary);
      }
      fieldset.append(header);

      if (!readOnly) {
        const titleLabel = document.createElement('label');
        titleLabel.className = 'field-label split-review-topic__title';
        const titleText = document.createElement('span');
        titleText.textContent = 'Problem title';
        const titleInput = document.createElement('input');
        titleInput.type = 'text';
        titleInput.value = topic.title;
        titleInput.maxLength = 255;
        titleInput.required = true;
        titleInput.dataset.splitReviewTitle = 'true';
        titleInput.dataset.topicKey = asString(topic.client_key || topic.topic_uuid);
        titleInput.setAttribute('aria-label', `Problem title for ${topic.title || 'new problem'}`);
        titleLabel.append(titleText, titleInput);
        fieldset.append(titleLabel);
      }

      const templateLabel = document.createElement('label');
      templateLabel.className = 'field-label split-review-topic__template';
      const templateText = document.createElement('span');
      templateText.textContent = 'Template';
      const selectWrap = document.createElement('span');
      selectWrap.className = 'select-wrap';
      const templateSelect = document.createElement('select');
      templateSelect.dataset.splitReviewTemplate = 'true';
      templateSelect.dataset.topicKey = asString(topic.client_key || topic.topic_uuid);
      templateSelect.setAttribute('aria-label', `Template for ${topic.title || 'problem'}`);
      topicTemplateOptions(asString(topic.template_id)).forEach((optionData) => {
        const option = document.createElement('option');
        option.value = optionData.value;
        option.textContent = optionData.label;
        option.selected = optionData.value === asString(topic.template_id);
        templateSelect.append(option);
      });
      selectWrap.append(templateSelect);
      templateLabel.append(templateText, selectWrap);
      fieldset.append(templateLabel);

      if (!topic.is_primary || topic.client_key) {
        const actions = document.createElement('div');
        actions.className = 'split-review-topic__actions';

        if (!topic.is_primary) {
          const merge = document.createElement('button');
          merge.type = 'button';
          merge.className = 'split-review-topic__text-action';
          merge.dataset.splitReviewMerge = '';
          merge.textContent = 'Merge into primary';
          actions.append(merge);

          const separator = document.createElement('span');
          separator.setAttribute('aria-hidden', 'true');
          separator.textContent = '·';
          actions.append(separator);

          const skip = document.createElement('button');
          skip.type = 'button';
          skip.className = 'split-review-topic__text-action split-review-topic__text-action--danger';
          skip.dataset.splitReviewSkip = '';
          skip.textContent = 'Skip';
          actions.append(skip);
        }
        if (topic.client_key) {
          if (actions.children.length) {
            const separator = document.createElement('span');
            separator.setAttribute('aria-hidden', 'true');
            separator.textContent = '·';
            actions.append(separator);
          }
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'split-review-topic__text-action split-review-topic__text-action--danger';
          remove.dataset.splitReviewRemove = '';
          remove.textContent = 'Remove';
          actions.append(remove);
        }
        fieldset.append(actions);
      }

      topicList.append(fieldset);
    });
    updateSplitReviewCounts();
    updateControlState();
    if (!focusAttribute || !topicKey) return;
    const replacement = topicList?.querySelector(`[${focusAttribute}][data-topic-key="${topicKey}"]`);
    if (!replacement) return;
    safeFocus(replacement);
    if (selection && typeof replacement.setSelectionRange === 'function') {
      try { replacement.setSelectionRange(...selection); } catch (_) {}
    }
  };

  const topicKey = (topic) => asString(topic?.client_key || topic?.topic_uuid);
  const findTopic = (key) => localDraft?.topics?.find((topic) => topicKey(topic) === asString(key));

  const addProblem = () => {
    if (isReadOnly() || saving || continuing || confirming || (localDraft?.topics?.length || 0) >= 6) return false;
    const topics = localDraft.topics;
    const clientKey = `new-topic-${++nextClientTopicKey}`;
    topics.push(enforcePrimaryDisposition({
      topic_uuid: null,
      client_key: clientKey,
      title: '',
      is_primary: topics.length === 0,
      disposition: 'separate_note',
      template_id: null,
    }));
    dirty = true;
    render();
    const titleInput = topicList?.querySelector(`[data-split-review-title][data-topic-key="${clientKey}"]`);
    safeFocus(titleInput);
    return true;
  };

  const markDirty = () => {
    if (isReadOnly() || saving || continuing) return;
    dirty = true;
    updateControlState();
  };

  const close = ({ force = false } = {}) => {
    if (!opened) return true;
    if (!force && (saving || continuing || confirming)) return false;
    if (!force && dirty && !confirmDiscard()) return false;
    if (force) {
      saveGeneration += 1;
      saving = false;
    }
    templateRefreshGeneration += 1;
    templateAvailabilityRefreshing = false;
    opened = false;
    dirty = false;
    localDraft = remoteDraft ? normalizeSplitDraft(remoteDraft) : null;
    if (modal) modal.hidden = true;
    setOpenState(false);
    if (typeof document !== 'undefined') document.body?.classList.remove('modal-open');
    const previousOpener = opener;
    opener = null;
    if (isUsableFocusTarget(previousOpener)) {
      safeFocus(previousOpener);
    } else if (isUsableFocusTarget(trigger)) {
      safeFocus(trigger);
    } else if (typeof document !== 'undefined') {
      const fallback = document.querySelector?.(
        'button:not([hidden]):not([disabled]), a[href]:not([hidden]), [tabindex]:not([tabindex="-1"]):not([hidden])',
      );
      if (isUsableFocusTarget(fallback)) safeFocus(fallback);
      else if (isUsableFocusTarget(document.body)) safeFocus(document.body);
    }
    updateControlState();
    return true;
  };

  const open = () => {
    if (!transcriptId || (!capabilityAvailable && capabilityKnown)) return false;
    opener = document.activeElement;
    opened = true;
    setOpenState(true);
    if (typeof document !== 'undefined') document.body?.classList.add('modal-open');
    render();
    templateAvailabilityFresh = false;
    templateAvailabilityRefreshing = true;
    const refreshGeneration = ++templateRefreshGeneration;
    updateControlState();
    const transcriptAtOpen = transcriptId;
    Promise.resolve()
      .then(() => refreshWorkspace(transcriptAtOpen))
      .then((workspace) => {
        if (refreshGeneration !== templateRefreshGeneration
          || !opened
          || transcriptId !== transcriptAtOpen) return;
        if (workspace && Array.isArray(workspace.available_templates)) {
          templates = workspace.available_templates;
          templateAvailabilityFresh = true;
        }
        templateAvailabilityRefreshing = false;
        render();
        if (!templateAvailabilityFresh) {
          setStatus('Could not refresh available templates. Close and reopen this review before creating notes.', 'error');
        } else if ((localDraft?.topics || []).some((topic) => (
          topic.disposition === 'separate_note'
          && topic.template_id
          && !templates.some((template) => templateId(template) === asString(topic.template_id))
        ))) {
          setStatus('A selected template is no longer available. Choose an available template, save the split, and select Create again.', 'warning');
        }
      })
      .catch(() => {
        if (refreshGeneration !== templateRefreshGeneration
          || !opened
          || transcriptId !== transcriptAtOpen) return;
        templateAvailabilityRefreshing = false;
        updateControlState();
        setStatus('Could not refresh available templates. Close and reopen this review before creating notes.', 'error');
      });
    const first = focusableElements(modal)[0];
    if (typeof window !== 'undefined') window.requestAnimationFrame?.(() => safeFocus(first));
    else safeFocus(first);
    return true;
  };

  const applyWorkspaceState = ({
    draft,
    availableTemplates = [],
    nextTranscriptId = undefined,
    batch,
    manualReview,
    analysis,
    intent,
    nextCapabilityAvailable,
    nextSourceReady,
    nextSourceReadinessMessage,
  } = {}) => {
    const incoming = normalizeSplitDraft(draft);
    const nextTranscriptIdValue = nextTranscriptId === undefined ? transcriptId : (nextTranscriptId || null);
    const sameDraft = Boolean(incoming && remoteDraft && incoming.draft_id === remoteDraft.draft_id);
    const sameTranscript = transcriptId === nextTranscriptIdValue;
    templates = Array.isArray(availableTemplates) ? availableTemplates : [];
    if (!opened) templateAvailabilityFresh = true;
    transcriptId = nextTranscriptIdValue;
    if (typeof nextCapabilityAvailable === 'boolean') {
      capabilityAvailable = nextCapabilityAvailable;
      capabilityKnown = true;
    }
    if (analysis !== undefined) latestAnalysis = analysis || null;
    if (intent !== undefined) latestIntent = intent || null;
    if (typeof nextSourceReady === 'boolean') sourceReady = nextSourceReady;
    if (typeof nextSourceReadinessMessage === 'string') sourceReadinessMessage = nextSourceReadinessMessage;
    if (!sameTranscript) {
      templateRefreshGeneration += 1;
      templateAvailabilityRefreshing = false;
      templateAvailabilityFresh = false;
      suggesting = false;
      suggestGeneration += 1;
    }
    if (typeof manualReview === 'boolean') {
      manualReviewRequested = manualReview;
    } else if (!sameDraft || !sameTranscript) {
      manualReviewRequested = false;
    }
    if (batch !== undefined) latestBatch = batch;
    if (incoming?.status === ACTIVE_STATUS) {
      editPreparationKey = null;
      editPreparation = null;
    }
    remoteDraft = incoming;
    if (!dirty) {
      localDraft = incoming ? normalizeSplitDraft(incoming) : null;
      expectedUpdatedAt = incoming?.updated_at || null;
      if (opened) render();
    } else if (sameDraft && incoming && incoming.status !== ACTIVE_STATUS) {
      // A stale or terminal server state must take the editor out of edit mode,
      // even if the browser still has an unsaved local copy.
      localDraft = { ...localDraft, status: incoming.status };
      render();
    } else if (!sameDraft) {
      // A different draft (including a transcript switch) must not replace
      // unsaved clinician edits. Keep them visible but fail closed until the
      // clinician explicitly closes and reopens the review.
      localDraft = localDraft ? { ...localDraft, status: 'unavailable' } : null;
      if (opened) render();
    }
    const reviewAvailable = Boolean(transcriptId && (capabilityAvailable || (!capabilityKnown && incoming)));
    if (trigger) {
      trigger.hidden = !reviewAvailable;
      trigger.disabled = !reviewAvailable;
    }
    if (!sameTranscript && opened) close({ force: true });
    updateControlState();
    return incoming;
  };

  const requestSuggestions = async () => {
    if (
      suggesting
      || saving
      || continuing
      || confirming
      || templateAvailabilityRefreshing
      || !capabilityAvailable
      || !transcriptId
      || !sourceReady
      || batchGenerationActive()
      || IN_FLIGHT_ANALYSIS_STATUSES.has(analysisStatus())
      || (!isReadOnly() && Boolean(localDraft) && !needsFreshManualIntent())
    ) return false;
    if (!isReadOnly() && dirty) {
      if (!confirmDiscard()) return false;
      dirty = false;
      localDraft = remoteDraft ? normalizeSplitDraft(remoteDraft) : null;
      if (opened) render();
    }
    const action = suggestionAction();
    const targetTranscriptId = transcriptId;
    const generation = ++suggestGeneration;
    suggesting = true;
    if (opened) render();
    try {
      const started = await suggestProblems({
        transcriptId: targetTranscriptId,
        retryFailedAnalysis: action.retry,
        forceNewReview: true,
      });
      if (generation !== suggestGeneration || transcriptId !== targetTranscriptId) return false;
      if (!started) setStatus('Could not start suggested-problem review. Please try again.', 'error');
      return Boolean(started);
    } catch (_) {
      if (generation === suggestGeneration && transcriptId === targetTranscriptId) {
        setStatus('Could not start suggested-problem review. Please try again.', 'error');
      }
      return false;
    } finally {
      if (generation === suggestGeneration && transcriptId === targetTranscriptId) {
        suggesting = false;
        if (opened) render(); else updateControlState();
      }
    }
  };

  const save = async () => {
    if (saving || templateAvailabilityRefreshing) return false;
    const activeTranscriptId = transcriptId || getTranscriptId();
    const validation = validateSplitDraft(localDraft);
    if (!localDraft || isReadOnly() || !activeTranscriptId || !validation.valid) {
      if (localDraft && !validation.valid) setStatus(validation.message, 'warning');
      return false;
    }
    const draftId = localDraft.draft_id;
    const saveTranscriptId = activeTranscriptId;
    const generation = ++saveGeneration;
    const isCurrentSave = () => (
      generation === saveGeneration
      && transcriptId === saveTranscriptId
      && localDraft?.draft_id === draftId
      && localDraft?.status === ACTIVE_STATUS
      && remoteDraft?.draft_id === draftId
      && remoteDraft?.status === ACTIVE_STATUS
    );
    saving = true;
    updateControlState();
    try {
      const response = await fetcher(`/api/v1/transcripts/${activeTranscriptId}/consultation-split-draft`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(serializeSplitDraft(localDraft, expectedUpdatedAt)),
      });
      if (!isCurrentSave()) return false;
      if (response.status === 409) {
        const error = await readSafeError(response);
        if (!isCurrentSave()) return false;
        dirty = false;
        const isConflict = error.code === 'consultation_split_draft_conflict';
        const isStale = error.code === 'consultation_split_source_stale';
        const failClosedStatus = isConflict || isStale ? 'stale' : 'unavailable';
        const failClosed = remoteDraft || localDraft;
        remoteDraft = failClosed ? { ...normalizeSplitDraft(failClosed), status: failClosedStatus } : null;
        localDraft = remoteDraft ? normalizeSplitDraft(remoteDraft) : null;
        expectedUpdatedAt = null;
        render();
        const conflictMessage = 'This split changed elsewhere. Reloading the latest version; your edits were not merged.';
        const staleMessage = isStale
          ? 'This split is stale because the consultation changed. Your edits were not saved.'
          : 'This split is no longer available. Your edits were not saved.';
        setStatus(isConflict ? conflictMessage : staleMessage, 'error');
        showMessage(isConflict ? 'This note split changed elsewhere. The latest version was reloaded; your edits were not merged.' : staleMessage, 'error');
        let refreshed = null;
        try { refreshed = await refreshWorkspace(); } catch (_) {}
        if (!isCurrentSave()) return false;
        const refreshedDraft = refreshed?.consultation_split_draft;
        if (isConflict && refreshedDraft?.status !== 'stale' && refreshedDraft?.status !== 'confirmed' && refreshedDraft?.status !== 'bypassed') {
          setStatus(conflictMessage, 'error');
        } else if (isStale || failClosedStatus === 'unavailable' || refreshedDraft?.status === 'stale') {
          setStatus(refreshedDraft?.status === 'stale'
            ? 'This split is stale because the consultation changed. Your edits were not saved.'
            : staleMessage, 'error');
        }
        return false;
      }
      if (!response.ok) {
        setStatus('Could not save this note split.', 'error');
        showMessage('Could not save this note split.', 'error');
        return false;
      }
      const saved = normalizeSplitDraft(await response.json());
      if (!isCurrentSave()) return false;
      remoteDraft = saved;
      localDraft = saved ? normalizeSplitDraft(saved) : null;
      expectedUpdatedAt = saved?.updated_at || null;
      dirty = false;
      render();
      const confirmationValidation = validateSplitDraftForConfirmation(localDraft);
      setStatus(
        confirmationValidation.valid
          ? 'Saved.'
          : (continueAvailable
            ? 'Saved. Continue as one note uses the template selected when you started Create.'
            : confirmationValidation.message),
        confirmationValidation.valid ? 'success' : 'one-note-guidance',
      );
      showMessage('Note split saved.', 'success');
      return true;
    } catch (_) {
      if (!isCurrentSave()) return false;
      setStatus('Could not save this note split.', 'error');
      showMessage('Could not save this note split.', 'error');
      return false;
    } finally {
      if (generation === saveGeneration) {
        saving = false;
        updateControlState();
      }
    }
  };

  const consumeOneNote = async (expectedIntentId = null) => {
    if (!continueAvailable || continuing || templateAvailabilityRefreshing) return false;
    if (expectedIntentId && getConfirmIntentId() !== expectedIntentId) return false;
    continuing = true;
    updateControlState();
    try {
      if (expectedIntentId && getConfirmIntentId() !== expectedIntentId) return false;
      const started = await continueAsOneNote();
      if (started) close({ force: true });
      return started;
    } finally {
      continuing = false;
      updateControlState();
    }
  };

  const saveOrContinueOneNote = async () => {
    if (continuing || confirming || saving || templateAvailabilityRefreshing) return false;
    if (!continueAvailable || !isAllMergedOneNoteDraft(localDraft)) {
      return save();
    }
    // An intent can be replaced by a later Create on the same transcript while
    // this PUT is in flight. Only consume the one that was available when this
    // all-merged action began.
    const intentId = getConfirmIntentId();
    if (!intentId) return false;
    continuing = true;
    updateControlState();
    try {
      if (dirty && !await save()) return false;
      if (
        isReadOnly()
        || getConfirmIntentId() !== intentId
        || !continueAvailable
        || !isAllMergedOneNoteDraft(localDraft)
      ) return false;
      // save() releases its own lock, while this outer guard keeps edits and
      // duplicate clicks disabled across the save-and-consume sequence.
      continuing = false;
      return await consumeOneNote(intentId);
    } finally {
      if (continuing) {
        continuing = false;
        updateControlState();
      }
    }
  };

  const confirm = async () => {
    if (confirming || saving || continuing || dirty || isReadOnly()) return false;
    const activeTranscriptId = transcriptId || getTranscriptId();
    const intentId = getConfirmIntentId();
    const validation = validateSplitDraftForConfirmation(localDraft);
    const availableTemplateIds = new Set(templates.map(templateId).filter(Boolean));
    const missingSelectedTemplate = (localDraft?.topics || []).some((topic) => (
      topic.disposition === 'separate_note'
      && topic.template_id
      && !availableTemplateIds.has(asString(topic.template_id))
    ));
    if (!activeTranscriptId || !intentId || !expectedUpdatedAt || !validation.valid
      || !templateAvailabilityFresh || missingSelectedTemplate) return false;
    const draftId = localDraft.draft_id;
    const confirmationTranscriptId = activeTranscriptId;
    confirming = true;
    setStatus('Starting note creation from the reviewed problems. This may take a moment.', 'progress');
    updateControlState();
    try {
      const response = await fetcher(`/api/v1/transcripts/${confirmationTranscriptId}/consultation-split-draft/confirm`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intent_id: intentId, expected_updated_at: expectedUpdatedAt }),
      });
      if (transcriptId !== confirmationTranscriptId || getTranscriptId() !== confirmationTranscriptId || localDraft?.draft_id !== draftId) return false;
      if (!response.ok) {
        const error = await readSafeError(response);
        if (transcriptId !== confirmationTranscriptId || getTranscriptId() !== confirmationTranscriptId || localDraft?.draft_id !== draftId) return false;
        if (response.status === 409) {
          localDraft = { ...localDraft, status: error.code === 'consultation_split_source_stale' ? 'stale' : 'unavailable' };
          remoteDraft = localDraft;
          render();
        }
        const message = splitDiagnosticMessage({
          stage: 'confirmation', errorCode: error.code, operationId: intentId,
        });
        setStatus(message, 'error');
        showMessage(message, 'error');
        return false;
      }
      const result = await response.json();
      if (transcriptId !== confirmationTranscriptId || getTranscriptId() !== confirmationTranscriptId || localDraft?.draft_id !== draftId) return false;
      localDraft = { ...localDraft, status: 'confirmed' };
      remoteDraft = localDraft;
      dirty = false;
      render();
      setStatus(result?.idempotency_replayed ? 'This note split is already queued for the next stage.' : 'Note split queued for the next stage.', 'success');
      showMessage('Note split queued for the next stage.', 'success');
      onSplitBatchStarted({
        draft: localDraft,
        batchId: result?.batch_id || null,
        phase: result?.status || 'generation_queued',
      });
      return true;
    } catch (_) {
      if (transcriptId === confirmationTranscriptId && getTranscriptId() === confirmationTranscriptId) {
        setStatus(splitDiagnosticMessage({ stage: 'confirmation', operationId: intentId }), 'error');
      }
      return false;
    } finally {
      confirming = false;
      updateControlState();
    }
  };

  trigger?.addEventListener('click', () => { open(); });
  closeButtons.forEach((button) => button.addEventListener('click', () => close()));
  modal?.addEventListener('click', (event) => {
    if (event.target instanceof Element && event.target.hasAttribute('data-split-review-close')) close();
  });
  topicList?.addEventListener('click', (event) => {
    if (saving || continuing) return;
    const target = event.target instanceof Element ? event.target : null;
    const fieldset = target?.closest('[data-topic-key]');
    if (!fieldset) return;
    const topic = findTopic(fieldset.dataset.topicKey);
    if (!topic) return;

    if (target.closest('[data-split-review-make-primary]')) {
      if (topic.disposition !== 'separate_note') return;
      localDraft.topics.forEach((item) => { item.is_primary = item === topic; });
      render();
      markDirty();
    } else if (target.closest('[data-split-review-merge]')) {
      if (topic.is_primary || topic.disposition !== 'separate_note') return;
      topic.disposition = 'include_in_primary';
      render();
      markDirty();
    } else if (target.closest('[data-split-review-skip]')) {
      if (topic.is_primary || topic.disposition !== 'separate_note') return;
      topic.disposition = 'exclude_from_notes';
      render();
      markDirty();
    } else if (target.closest('[data-split-review-undo]')) {
      topic.disposition = 'separate_note';
      render();
      markDirty();
    } else if (target.closest('[data-split-review-remove]') && topic.client_key) {
      localDraft.topics = localDraft.topics.filter((item) => item !== topic);
      if (localDraft.topics.length && !localDraft.topics.some((item) => item.is_primary)) {
        localDraft.topics[0].is_primary = true;
        localDraft.topics[0].disposition = 'separate_note';
      }
      render();
      markDirty();
    }
  });
  const editTopic = (event) => {
    if (saving || continuing) return;
    const input = event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement ? event.target : null;
    if (!input) return;
    const topic = findTopic(input.dataset.topicKey);
    if (!topic) return;
    if (input.hasAttribute('data-split-review-template')) {
      topic.template_id = input.value || null;
      markDirty();
    } else if (input.hasAttribute('data-split-review-title')) {
      topic.title = input.value;
      markDirty();
    }
  };
  topicList?.addEventListener('input', editTopic);
  topicList?.addEventListener('change', editTopic);
  addButton?.addEventListener('click', () => { addProblem(); });
  suggestButton?.addEventListener('click', () => { void requestSuggestions(); });
  saveButton?.addEventListener('click', () => { void saveOrContinueOneNote(); });
  continueButton?.addEventListener('click', () => { void consumeOneNote(); });
  createNotesButton?.addEventListener('click', () => { void confirm(); });
  modal?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const elements = focusableElements(modal);
    if (!elements.length) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      safeFocus(last);
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      safeFocus(first);
    }
  });

  return {
    applyWorkspaceState,
    updateSuggestionReadiness: ({
      nextCapabilityAvailable,
      nextSourceReady,
      nextSourceReadinessMessage,
    } = {}) => {
      if (typeof nextCapabilityAvailable === 'boolean') {
        capabilityAvailable = nextCapabilityAvailable;
        capabilityKnown = true;
      }
      if (typeof nextSourceReady === 'boolean') sourceReady = nextSourceReady;
      if (typeof nextSourceReadinessMessage === 'string') sourceReadinessMessage = nextSourceReadinessMessage;
      if (opened) render(); else updateControlState();
    },
    close,
    open,
    render,
    addProblem,
    requestSuggestions,
    save,
    continueAsOneNote: consumeOneNote,
    confirm,
    setContinueAvailable: (available) => {
      continueAvailable = Boolean(available);
      const validation = validateSplitDraft(localDraft);
      const confirmationValidation = validateSplitDraftForConfirmation(localDraft);
      if (continueAvailable && !manualReviewRequested && validation.valid && !confirmationValidation.valid) {
        setStatus('Choose at least two separate notes to create a note split. Continue as one note uses the template selected when you started Create.', 'one-note-guidance');
      }
      updateControlState();
    },
    isDirty: () => dirty,
    isOpen: () => opened,
    getDraft: () => localDraft,
    getRemoteDraft: () => remoteDraft,
    serialize: () => serializeSplitDraft(localDraft, expectedUpdatedAt),
  };
}
