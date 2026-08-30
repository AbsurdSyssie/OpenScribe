import { csrfFetch } from '../csrf.js';

export const TEMPLATE_SUGGESTION_MIN_CHARS = 1200;
const IN_PROGRESS_STATUSES = new Set(['pending', 'running', 'queued', 'processing']);

function trace(event, metadata = {}) {
  // Keep browser diagnostics content-free: IDs, counts, and workflow status only.
  if (typeof console?.info === 'function') console.info(event, metadata);
}

export function createTemplateSuggestionController({ transcriptText, templateSelect, suggestionRegion, suggestionMessage, useButton, dismissButton, getTranscriptId, chooseTemplate, fetcher = csrfFetch, pollMs = 1500, maxPolls = 40, schedule = (callback, delay) => window.setTimeout(callback, delay) }) {
  const requestedTranscriptIds = new Set();
  const resumableTranscriptIds = new Set();
  const dismissedTranscriptIds = new Set();
  let activeSuggestion = null;
  let applyingSuggestion = false;
  let observer = null;

  const hide = () => {
    activeSuggestion = null;
    if (suggestionRegion) suggestionRegion.hidden = true;
  };
  const dismiss = () => {
    const transcriptId = getTranscriptId();
    if (transcriptId) dismissedTranscriptIds.add(transcriptId);
    if (transcriptId) trace('template_suggestion_browser_dismissed', { transcriptId });
    hide();
  };
  const show = (transcriptId, suggestion) => {
    if (!suggestion || getTranscriptId() !== transcriptId || dismissedTranscriptIds.has(transcriptId)) return;
    if (String(suggestion.template_id || '') === String(templateSelect?.value || '')) return;
    const option = [...(templateSelect?.options || [])].find((item) => item.value === String(suggestion.template_id || ''));
    if (!option) return;
    activeSuggestion = { ...suggestion, transcriptId };
    if (suggestionMessage) suggestionMessage.textContent = `${suggestion.template_name || option.dataset.templateName || option.textContent.trim()} may be a better fit.`;
    if (suggestionRegion) suggestionRegion.hidden = false;
    trace('template_suggestion_browser_shown', { transcriptId });
  };
  const readResponse = async (response) => {
    if (!response?.ok) return null;
    try { return await response.json(); } catch (_) { return null; }
  };
  const poll = async (transcriptId, remaining) => {
    if (remaining <= 0) {
      trace('template_suggestion_browser_poll_stopped', { transcriptId, reasonCode: 'poll_limit_reached' });
      return;
    }
    if (dismissedTranscriptIds.has(transcriptId)) return;
    if (getTranscriptId() !== transcriptId) {
      resumableTranscriptIds.add(transcriptId);
      trace('template_suggestion_browser_poll_paused', { transcriptId, reasonCode: 'transcript_changed' });
      return;
    }
    let payload = null;
    try { payload = await readResponse(await fetcher(`/api/v1/transcripts/${transcriptId}/template-suggestion`, { method: 'GET', credentials: 'include' })); } catch (_) {
      resumableTranscriptIds.add(transcriptId);
      trace('template_suggestion_browser_poll_failed', { transcriptId, reasonCode: 'request_failed' });
      return;
    }
    if (!payload) {
      resumableTranscriptIds.add(transcriptId);
      trace('template_suggestion_browser_poll_failed', { transcriptId, reasonCode: 'invalid_response' });
      return;
    }
    trace('template_suggestion_browser_poll_received', { transcriptId, status: payload.status, remainingPolls: remaining });
    if (getTranscriptId() !== transcriptId) { resumableTranscriptIds.add(transcriptId); return; }
    if (IN_PROGRESS_STATUSES.has(payload.status)) {
      schedule(() => void poll(transcriptId, remaining - 1), pollMs);
    } else if (payload.status === 'completed') {
      show(transcriptId, payload.suggestion);
    }
  };
  const requestIfEligible = async () => {
    const transcriptId = getTranscriptId();
    const transcriptCharCount = String(transcriptText?.textContent || '').length;
    if (!transcriptId || transcriptCharCount < TEMPLATE_SUGGESTION_MIN_CHARS) return;
    if (requestedTranscriptIds.has(transcriptId)) {
      if (resumableTranscriptIds.delete(transcriptId)) {
        trace('template_suggestion_browser_poll_resumed', { transcriptId });
        void poll(transcriptId, maxPolls);
      }
      return;
    }
    requestedTranscriptIds.add(transcriptId);
    trace('template_suggestion_browser_request_started', { transcriptId, transcriptCharCount });
    let payload = null;
    try { payload = await readResponse(await fetcher(`/api/v1/transcripts/${transcriptId}/template-suggestion`, { method: 'POST', credentials: 'include' })); } catch (_) {
      requestedTranscriptIds.delete(transcriptId);
      trace('template_suggestion_browser_request_failed', { transcriptId, reasonCode: 'request_failed' });
      return;
    }
    if (!payload) {
      requestedTranscriptIds.delete(transcriptId);
      trace('template_suggestion_browser_request_failed', { transcriptId, reasonCode: 'invalid_response' });
      return;
    }
    trace('template_suggestion_browser_request_received', { transcriptId, status: payload.status });
    if (getTranscriptId() !== transcriptId) { resumableTranscriptIds.add(transcriptId); return; }
    if (IN_PROGRESS_STATUSES.has(payload.status)) {
      schedule(() => void poll(transcriptId, maxPolls), pollMs);
    } else if (payload.status === 'completed') {
      show(transcriptId, payload.suggestion);
    }
  };
  const onTemplateChange = () => {
    if (applyingSuggestion) return;
    if (activeSuggestion) dismiss();
  };
  const useSuggestion = () => {
    if (!activeSuggestion || activeSuggestion.transcriptId !== getTranscriptId()) return;
    const templateId = String(activeSuggestion.template_id || '');
    dismissedTranscriptIds.add(activeSuggestion.transcriptId);
    trace('template_suggestion_browser_accepted', { transcriptId: activeSuggestion.transcriptId });
    hide();
    applyingSuggestion = true;
    try { chooseTemplate(templateId); } finally { applyingSuggestion = false; }
  };
  return {
    attach() {
      templateSelect?.addEventListener('change', onTemplateChange);
      useButton?.addEventListener('click', useSuggestion);
      dismissButton?.addEventListener('click', dismiss);
      if (transcriptText && 'MutationObserver' in window) {
        observer = new MutationObserver(() => void requestIfEligible());
        observer.observe(transcriptText, { childList: true, characterData: true, subtree: true });
      }
      void requestIfEligible();
    },
    detach() {
      observer?.disconnect();
      templateSelect?.removeEventListener('change', onTemplateChange);
      useButton?.removeEventListener('click', useSuggestion);
      dismissButton?.removeEventListener('click', dismiss);
    },
    dismiss,
    requestIfEligible,
  };
}
