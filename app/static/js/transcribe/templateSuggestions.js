import { csrfFetch } from '../csrf.js';

export const TEMPLATE_SUGGESTION_MIN_CHARS = 1200;
const IN_PROGRESS_STATUSES = new Set(['pending', 'running', 'queued', 'processing']);

function trace(event, metadata = {}) {
  // Keep browser diagnostics content-free: IDs, counts, and workflow status only.
  if (typeof console?.info === 'function') console.info(event, metadata);
}

export function createTemplateSuggestionController({ transcriptText, templateSelect, suggestionRegion, suggestionMessage, useButton, dismissButton, getTranscriptId, isEnabled = () => true, chooseTemplate, fetcher = csrfFetch, pollMs = 1500, maxPolls = 40, schedule = (callback, delay) => window.setTimeout(callback, delay) }) {
  const requestedTranscriptIds = new Set();
  const resumableTranscriptIds = new Set();
  const dismissedTranscriptIds = new Set();
  let activeSuggestion = null;
  let applyingSuggestion = false;
  let observer = null;
  let templatePickerButton = null;
  let originalSuggestionParent = null;
  let originalSuggestionNextSibling = null;

  const positionSuggestion = () => {
    if (!suggestionRegion || suggestionRegion.hidden || typeof window === 'undefined') return;
    const pickerRect = templatePickerButton?.getBoundingClientRect() || { left: 0, top: 0, width: 0, height: 0 };
    const popoverRect = suggestionRegion.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    const edge = 12;
    const gap = 11;
    const hasVisiblePicker = pickerRect.width > 0 && pickerRect.height > 0;
    const maxLeft = Math.max(edge, viewportWidth - popoverRect.width - edge);
    const left = hasVisiblePicker
      ? Math.min(Math.max(pickerRect.left, edge), maxLeft)
      : Math.max(edge, Math.round((viewportWidth - popoverRect.width) / 2));
    const top = hasVisiblePicker ? Math.max(edge, pickerRect.top - popoverRect.height - gap) : edge;
    const preferredArrowX = pickerRect.left + Math.min(46, Math.max(28, pickerRect.width * 0.25));
    const arrowLeft = Math.min(Math.max(preferredArrowX - left, 22), Math.max(22, popoverRect.width - 22));

    suggestionRegion.style.left = `${Math.round(left)}px`;
    suggestionRegion.style.top = `${Math.round(top)}px`;
    suggestionRegion.style.setProperty('--template-suggestion-arrow-left', `${Math.round(arrowLeft)}px`);
    suggestionRegion.dataset.templateSuggestionPlacement = hasVisiblePicker ? 'picker' : 'viewport';
  };

  const hide = () => {
    activeSuggestion = null;
    templatePickerButton?.classList.remove('template-picker-button--suggested');
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
    const templateName = suggestion.template_name || option.dataset.templateName || option.textContent.trim();
    if (suggestionMessage) suggestionMessage.textContent = `Use ${templateName} instead?`;
    if (suggestionRegion) suggestionRegion.hidden = false;
    templatePickerButton?.classList.remove('template-picker-button--suggested');
    // Force a style flush so repeated suggestions restart the finite pulse animation.
    if (templatePickerButton) void templatePickerButton.offsetWidth;
    templatePickerButton?.classList.add('template-picker-button--suggested');
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(positionSuggestion);
    } else {
      positionSuggestion();
    }
    trace('template_suggestion_browser_shown', { transcriptId });
  };
  const readResponse = async (response) => {
    if (!response?.ok) return null;
    try { return await response.json(); } catch (_) { return null; }
  };
  const poll = async (transcriptId, remaining) => {
    if (!isEnabled()) return;
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
    if (!isEnabled()) {
      hide();
      return;
    }
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
  const onDocumentPointerDown = (event) => {
    if (!activeSuggestion || !suggestionRegion || suggestionRegion.hidden) return;
    const target = event.target;
    if (suggestionRegion.contains(target) || templatePickerButton?.contains(target)) return;
    dismiss();
  };
  const onDocumentKeyDown = (event) => {
    if (event.key === 'Escape' && activeSuggestion && !suggestionRegion?.hidden) dismiss();
  };
  const preparePopover = () => {
    if (!suggestionRegion || typeof document === 'undefined') return;
    originalSuggestionParent = suggestionRegion.parentNode;
    originalSuggestionNextSibling = suggestionRegion.nextSibling;
    templatePickerButton = suggestionRegion.closest('.note-header-row')?.querySelector('[data-template-picker-button]')
      || document.querySelector('[data-template-picker-button]');

    if (!suggestionRegion.querySelector('[data-template-suggestion-spark]')) {
      const spark = document.createElement('span');
      spark.className = 'template-suggestion__spark';
      spark.dataset.templateSuggestionSpark = '';
      spark.setAttribute('aria-hidden', 'true');
      spark.textContent = '✦';
      suggestionRegion.insertBefore(spark, suggestionMessage || suggestionRegion.firstChild);
    }

    suggestionRegion.classList.add('template-suggestion--popover');
    suggestionRegion.setAttribute('role', 'status');
    suggestionRegion.setAttribute('aria-live', 'polite');
    if (dismissButton) {
      dismissButton.textContent = '×';
      dismissButton.setAttribute('aria-label', 'Dismiss template suggestion');
      dismissButton.setAttribute('title', 'Dismiss');
    }
    document.body.appendChild(suggestionRegion);
  };
  const restorePopover = () => {
    if (!suggestionRegion || !originalSuggestionParent) return;
    suggestionRegion.classList.remove('template-suggestion--popover');
    suggestionRegion.style.removeProperty('left');
    suggestionRegion.style.removeProperty('top');
    suggestionRegion.style.removeProperty('--template-suggestion-arrow-left');
    delete suggestionRegion.dataset.templateSuggestionPlacement;
    if (originalSuggestionNextSibling && originalSuggestionNextSibling.parentNode === originalSuggestionParent) {
      originalSuggestionParent.insertBefore(suggestionRegion, originalSuggestionNextSibling);
    } else {
      originalSuggestionParent.appendChild(suggestionRegion);
    }
  };
  return {
    attach() {
      preparePopover();
      templateSelect?.addEventListener('change', onTemplateChange);
      useButton?.addEventListener('click', useSuggestion);
      dismissButton?.addEventListener('click', dismiss);
      if (typeof document !== 'undefined') document.addEventListener('pointerdown', onDocumentPointerDown, true);
      if (typeof document !== 'undefined') document.addEventListener('keydown', onDocumentKeyDown);
      if (typeof window !== 'undefined') {
        window.addEventListener('resize', positionSuggestion);
        window.addEventListener('scroll', positionSuggestion, true);
      }
      if (transcriptText && typeof window !== 'undefined' && 'MutationObserver' in window) {
        observer = new MutationObserver(() => void requestIfEligible());
        observer.observe(transcriptText, { childList: true, characterData: true, subtree: true });
      }
      void requestIfEligible();
    },
    detach() {
      hide();
      observer?.disconnect();
      templateSelect?.removeEventListener('change', onTemplateChange);
      useButton?.removeEventListener('click', useSuggestion);
      dismissButton?.removeEventListener('click', dismiss);
      if (typeof document !== 'undefined') document.removeEventListener('pointerdown', onDocumentPointerDown, true);
      if (typeof document !== 'undefined') document.removeEventListener('keydown', onDocumentKeyDown);
      if (typeof window !== 'undefined') {
        window.removeEventListener('resize', positionSuggestion);
        window.removeEventListener('scroll', positionSuggestion, true);
      }
      restorePopover();
    },
    dismiss,
    onTranscriptChanged() {
      if (activeSuggestion && activeSuggestion.transcriptId !== getTranscriptId()) hide();
    },
    onPreferenceChanged() {
      if (!isEnabled() && !activeSuggestion) hide();
    },
    requestIfEligible,
  };
}
