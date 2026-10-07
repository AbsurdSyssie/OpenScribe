import { csrfFetch } from '../csrf.js';

const VALID_DISPOSITIONS = new Set([
  'separate_note',
  'include_in_primary',
  'exclude_from_notes',
]);
const ACTIVE_STATUS = 'active';
const TERMINAL_STATUSES = new Set(['confirmed', 'bypassed']);
const EDITABLE_BATCH_STATUSES = new Set(['ready', 'completed_partial', 'failed']);
const IN_FLIGHT_ANALYSIS_STATUSES = new Set(['queued', 'processing']);
const RESTORATION_BACKOFF_MS = Object.freeze([1500, 3000, 6000, 12000, 15000]);
const NO_NOTE_ANALYSIS_STATUSES = new Set(['not_required', 'failed', 'stale', 'incomplete']);

const asString = (value) => (value === null || value === undefined ? '' : String(value));
const normalizeTitle = (value) => asString(value).trim().replace(/\s+/g, ' ');

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
    if (!IN_FLIGHT_ANALYSIS_STATUSES.has(analysis.status)) return null;
    return {
      transcriptId: String(state.transcriptId),
      signature: [analysis.analysis_id, analysis.status, analysis.updated_at || ''].join('|'),
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
  const render = () => {
    const canAct = Boolean(batch?.can_retry_missing || batch?.can_keep_available);
    const completedPartial = batch?.status === 'completed_partial';
    const verifying = batch?.status === 'verifying' || batch?.verification_status === 'verifying';
    const primaryFailed = Boolean(batch?.primary_failed);
    // Controls themselves remain server-flag-only. The completed state stays
    // visible as a safe clinical acknowledgement after both flags turn off.
    if (actionsRoot) actionsRoot.hidden = !batch?.batch_id || (!canAct && !completedPartial && !verifying);
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
    if (!inFlight) {
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
// analysis or generation; Create remains the only operation that can do that.
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
  const failDraftInitialization = (candidate) => {
    if (!current(candidate)) return;
    candidate.draftState = 'failed';
    candidate.pending = false;
    setBusy(false);
    candidate.continueEligible = false;
    updateContinueAvailability(candidate);
    setOperationStatus('No note generated. Review the consultation and select Create to retry the note split.', 'warning');
  };
  const analysisStatusMessage = (status) => {
    if (status === 'queued') return 'Preparing your note split. This may take a moment.';
    if (status === 'processing') return 'Preparing your note split.';
    return 'No note generated. Review the consultation and select Create again if needed.';
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
          failDraftInitialization(candidate);
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
      setOperationStatus(analysisStatusMessage(status));
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
      setOperationStatus(analysisStatusMessage(status), 'warning');
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
  } = {}) => {
    if (!getCapabilityEnabled()) {
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
    if (operation && !operation.retryable) return true;
    const retry = operation
      && operation.retryable
      && same(operation.transcriptId, transcriptId)
      && same(operation.templateId, templateId);
    const candidate = retry ? operation : {
      token: ++generation,
      transcriptId,
      templateId,
      key: createKey(),
      pending: true,
      retryable: false,
      requestStarted: false,
      draftState: 'idle',
      analysisId: null,
      intentId: null,
      consumePending: false,
      consumed: false,
      continueEligible: false,
      manualReviewRequested: false,
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
        body: JSON.stringify({ client_idempotency_key: candidate.key, selected_template_id: candidate.templateId }),
      });
      if (!current(candidate)) return true;
      if (!same(getTranscriptId(), candidate.transcriptId)) {
        abandonForTranscriptChange(candidate);
        return true;
      }
      if (!response.ok) {
        candidate.retryable = response.status >= 500;
        candidate.pending = false;
        setOperationStatus(candidate.retryable
          ? 'Could not confirm the note split. Select Create to retry safely.'
          : 'No note generated. Review the consultation and select Create again if needed.', 'warning');
        if (!candidate.retryable) reset(); else setBusy(false);
        return true;
      }
      const payload = await response.json();
      if (!current(candidate)) return true;
      if (!same(getTranscriptId(), candidate.transcriptId)) {
        abandonForTranscriptChange(candidate);
        return true;
      }
      candidate.analysisId = payload?.analysis?.analysis_id || null;
      candidate.intentId = payload?.intent_id || null;
      candidate.manualReviewRequested = Boolean(payload?.manual_review_requested);
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
          setOperationStatus('Could not confirm the note split. Select Create to retry safely.', 'warning');
        } else {
          setOperationStatus('No note generated. Review the consultation and select Create again if needed.', 'warning');
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
          error.code === 'not_found' || error.code === 'consultation_split_template_unavailable'
            ? 'The saved template is unavailable. Choose a currently available template and select Create to start a new request.'
            : 'Could not start one note. Your saved request was not consumed.',
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
        setOperationStatus('Could not start one note. Your saved request was not consumed.', 'warning');
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
        onSplitBatchStarted({ draft: getConfirmedDraft(), batchId, phase: 'failed' });
        setOperationStatus('Could not regenerate split notes. Please try again.', 'warning');
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
      if (current(candidate) && same(getTranscriptId(), transcriptId)) setOperationStatus('Could not regenerate split notes. Please try again.', 'warning');
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
      message: payload?.error?.message || payload?.message || null,
    };
  } catch (_) {
    return { code: null, message: null };
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
  continueButton = modal?.querySelector('[data-split-review-continue]'),
  confirmButton = modal?.querySelector('[data-split-review-confirm]'),
  closeButtons = modal ? [...modal.querySelectorAll('[data-split-review-close]')] : [],
  fetcher = csrfFetch,
  refreshWorkspace = async () => null,
  getTranscriptId = () => null,
  showMessage = () => {},
  continueAsOneNote = async () => false,
  beginEdit = async () => false,
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

  const updateControlState = () => {
    const readOnly = isReadOnly();
    const controlsDisabled = readOnly || saving || continuing || confirming;
    setDisabled(topicList, controlsDisabled);
    closeButtons.forEach((button) => { button.disabled = saving || continuing; });
    if (modal) modal.setAttribute('aria-busy', String(saving || continuing));
    const validation = readOnly ? { valid: false } : validateSplitDraft(localDraft);
    const confirmationValidation = readOnly ? { valid: false } : validateSplitDraftForConfirmation(localDraft);
    const shouldContinueAsOneNote = continueAvailable && isAllMergedOneNoteDraft(localDraft);
    if (saveButton) {
      saveButton.textContent = shouldContinueAsOneNote ? 'Continue as one note' : 'Save split';
      saveButton.disabled = controlsDisabled
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
      continueButton.disabled = !continueAvailable || shouldContinueAsOneNote || controlsDisabled;
    }
    if (createNotesButton) {
      const confirmable = !readOnly && !dirty && confirmationValidation.valid && Boolean(getConfirmIntentId());
      createNotesButton.hidden = !confirmable && !confirming;
      createNotesButton.disabled = controlsDisabled || !confirmable || confirming;
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
    topicList.replaceChildren();
    const readOnly = isReadOnly();
    const topics = localDraft?.topics || [];
    if (localDraft?.status === 'stale') {
      setStatus('This split is stale and read-only because the consultation changed.', 'warning');
    } else if (localDraft?.status === 'confirmed' && EDITABLE_BATCH_STATUSES.has(latestBatch?.status)) {
      setStatus('Preparing an editable split review.', 'progress');
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
    } else if (!dirty) {
      setStatus('');
    }
    if (!topics.length) {
      const empty = document.createElement('p');
      empty.className = 'split-review-modal__empty';
      empty.textContent = readOnly ? 'No reviewable note topics are available.' : 'No topics were proposed.';
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
    if (!force && (saving || continuing)) return false;
    if (!force && dirty && !confirmDiscard()) return false;
    if (force) {
      saveGeneration += 1;
      saving = false;
    }
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
    if (!localDraft) return false;
    opener = document.activeElement;
    opened = true;
    setOpenState(true);
    if (typeof document !== 'undefined') document.body?.classList.add('modal-open');
    render();
    const first = focusableElements(modal)[0];
    if (typeof window !== 'undefined') window.requestAnimationFrame?.(() => safeFocus(first));
    else safeFocus(first);
    return true;
  };

  const applyWorkspaceState = ({ draft, availableTemplates = [], nextTranscriptId = null, batch, manualReview } = {}) => {
    const incoming = normalizeSplitDraft(draft);
    const nextTranscriptIdValue = nextTranscriptId || null;
    const sameDraft = Boolean(incoming && remoteDraft && incoming.draft_id === remoteDraft.draft_id);
    const sameTranscript = transcriptId === nextTranscriptIdValue;
    templates = Array.isArray(availableTemplates) ? availableTemplates : [];
    transcriptId = nextTranscriptIdValue;
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
    const reviewAvailable = Boolean(
      incoming
      && (incoming.status === ACTIVE_STATUS
        || (incoming.status === 'confirmed' && EDITABLE_BATCH_STATUSES.has(latestBatch?.status))),
    );
    if (trigger) trigger.hidden = !reviewAvailable;
    if (!incoming && opened) close({ force: true });
    if (!incoming) setOpenState(false);
    updateControlState();
    return incoming;
  };

  const save = async () => {
    if (saving) return false;
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
    if (!continueAvailable || continuing) return false;
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
    if (continuing || confirming || saving) return false;
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
    if (!activeTranscriptId || !intentId || !expectedUpdatedAt || !validation.valid) return false;
    const draftId = localDraft.draft_id;
    const confirmationTranscriptId = activeTranscriptId;
    confirming = true;
    updateControlState();
    try {
      const response = await fetcher(`/api/v1/transcripts/${confirmationTranscriptId}/consultation-split-draft/confirm`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intent_id: intentId, expected_updated_at: expectedUpdatedAt }),
      });
      if (transcriptId !== confirmationTranscriptId || getTranscriptId() !== confirmationTranscriptId || localDraft?.draft_id !== draftId) return false;
      if (!response.ok) {
        const error = await readSafeError(response);
        if (response.status === 409) {
          localDraft = { ...localDraft, status: error.code === 'consultation_split_source_stale' ? 'stale' : 'unavailable' };
          remoteDraft = localDraft;
          render();
        }
        setStatus('Could not confirm this note split. No notes were generated.', 'error');
        showMessage('Could not confirm this note split. No notes were generated.', 'error');
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
        setStatus('Could not confirm this note split. No notes were generated.', 'error');
      }
      return false;
    } finally {
      confirming = false;
      updateControlState();
    }
  };

  trigger?.addEventListener('click', async () => {
    if (remoteDraft?.status === 'confirmed' && EDITABLE_BATCH_STATUSES.has(latestBatch?.status)) {
      const key = `${remoteDraft.draft_id || ''}:${latestBatch?.batch_id || ''}:${latestBatch?.status || ''}`;
      if (editPreparationKey === key || editPreparation) return;
      editPreparationKey = key;
      editPreparation = Promise.resolve(beginEdit({
        transcriptId,
        batchId: latestBatch?.batch_id || null,
      })).then((started) => {
        if (!started) setStatus('Could not prepare an editable split review.', 'error');
        return Boolean(started);
      }).catch(() => {
        setStatus('Could not prepare an editable split review.', 'error');
        return false;
      }).finally(() => {
        editPreparation = null;
        updateControlState();
      });
      return;
    }
    open();
  });
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
    close,
    open,
    render,
    addProblem,
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
