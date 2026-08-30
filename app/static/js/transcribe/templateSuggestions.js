import { csrfFetch } from '../csrf.js';

export const TEMPLATE_SUGGESTION_MIN_CHARS = 1200;
const IN_PROGRESS_STATUSES = new Set(['pending', 'running', 'queued', 'processing']);
const POPOVER_STYLE_ID = 'template-suggestion-popover-styles';

function trace(event, metadata = {}) {
  // Keep browser diagnostics content-free: IDs, counts, and workflow status only.
  if (typeof console?.info === 'function') console.info(event, metadata);
}

function installPopoverStyles() {
  if (typeof document === 'undefined' || document.getElementById(POPOVER_STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = POPOVER_STYLE_ID;
  style.textContent = `
    @keyframes template-suggestion-selector-pulse {
      0%, 100% {
        border-color: rgba(112, 184, 214, 0.82);
        box-shadow: 0 0 0 2px rgba(112, 184, 214, 0.14), 0 0 0 0 rgba(112, 184, 214, 0.18);
      }
      50% {
        border-color: rgba(112, 184, 214, 1);
        box-shadow: 0 0 0 3px rgba(112, 184, 214, 0.18), 0 0 0 8px rgba(112, 184, 214, 0.08);
      }
    }

    @keyframes template-suggestion-popover-enter {
      from { opacity: 0; transform: translateY(0.35rem) scale(0.985); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }

    .template-picker-button--compact.template-picker-button--suggested {
      border-color: rgba(112, 184, 214, 0.92);
      background: rgba(239, 249, 252, 0.78);
      animation: template-suggestion-selector-pulse 1.45s ease-in-out 3;
    }

    .template-suggestion.template-suggestion--popover {
      position: fixed;
      z-index: 520;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 0.65rem;
      width: max-content;
      max-width: min(27rem, calc(100vw - 1.5rem));
      margin: 0;
      padding: 0.72rem 2.15rem 0.72rem 0.78rem;
      border: 1px solid rgba(112, 184, 214, 0.42);
      border-radius: 0.75rem;
      background: rgba(255, 255, 255, 0.98);
      color: var(--fg);
      box-shadow: 0 16px 38px rgba(26, 32, 44, 0.18), 0 3px 10px rgba(26, 32, 44, 0.08);
      font-size: 0.86rem;
      line-height: 1.35;
      isolation: isolate;
    }

    .template-suggestion.template-suggestion--popover:not([hidden]) {
      animation: template-suggestion-popover-enter 160ms ease-out both;
    }

    .template-suggestion.template-suggestion--popover[hidden] { display: none; }

    .template-suggestion.template-suggestion--popover::before,
    .template-suggestion.template-suggestion--popover::after {
      content: '';
      position: absolute;
      left: var(--template-suggestion-arrow-left, 2.4rem);
      transform: translateX(-50%);
      width: 0;
      height: 0;
      pointer-events: none;
    }

    .template-suggestion.template-suggestion--popover::before {
      top: 100%;
      border-left: 0.5rem solid transparent;
      border-right: 0.5rem solid transparent;
      border-top: 0.55rem solid rgba(112, 184, 214, 0.42);
    }

    .template-suggestion.template-suggestion--popover::after {
      top: calc(100% - 1px);
      border-left: 0.45rem solid transparent;
      border-right: 0.45rem solid transparent;
      border-top: 0.5rem solid rgba(255, 255, 255, 0.98);
    }

    .template-suggestion__spark {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1.9rem;
      height: 1.9rem;
      border: 1px solid rgba(112, 184, 214, 0.34);
      border-radius: 0.55rem;
      background: rgba(224, 242, 245, 0.58);
      color: var(--accent);
      font-size: 1rem;
      line-height: 1;
      flex: 0 0 auto;
    }

    .template-suggestion.template-suggestion--popover p {
      margin: 0;
      min-width: 0;
      color: var(--fg);
      font-weight: 600;
    }

    .template-suggestion.template-suggestion--popover .template-suggestion__actions {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      flex: 0 0 auto;
    }

    .template-suggestion.template-suggestion--popover [data-template-suggestion-use] {
      white-space: nowrap;
      padding: 0.4rem 0.65rem;
      box-shadow: none;
    }

    .template-suggestion.template-suggestion--popover [data-template-suggestion-dismiss] {
      position: absolute;
      top: 0.28rem;
      right: 0.28rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1.7rem;
      height: 1.7rem;
      padding: 0;
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      font-size: 1.1rem;
      font-weight: 500;
      line-height: 1;
    }

    .template-suggestion.template-suggestion--popover [data-template-suggestion-dismiss]:hover,
    .template-suggestion.template-suggestion--popover [data-template-suggestion-dismiss]:focus-visible {
      background: rgba(29, 79, 94, 0.07);
      color: var(--fg);
      outline: none;
    }

    @media (max-width: 640px) {
      .template-suggestion.template-suggestion--popover {
        grid-template-columns: auto minmax(0, 1fr);
        row-gap: 0.5rem;
        width: min(22rem, calc(100vw - 1.5rem));
      }

      .template-suggestion.template-suggestion--popover .template-suggestion__actions {
        grid-column: 2;
        justify-content: flex-start;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .template-picker-button--compact.template-picker-button--suggested,
      .template-suggestion.template-suggestion--popover:not([hidden]) {
        animation: none;
      }
    }
  `;
  document.head.appendChild(style);
}

export function createTemplateSuggestionController({ transcriptText, templateSelect, suggestionRegion, suggestionMessage, useButton, dismissButton, getTranscriptId, chooseTemplate, fetcher = csrfFetch, pollMs = 1500, maxPolls = 40, schedule = (callback, delay) => window.setTimeout(callback, delay) }) {
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
    if (!suggestionRegion || suggestionRegion.hidden || !templatePickerButton || typeof window === 'undefined') return;
    const pickerRect = templatePickerButton.getBoundingClientRect();
    const popoverRect = suggestionRegion.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    const edge = 12;
    const gap = 11;
    const maxLeft = Math.max(edge, viewportWidth - popoverRect.width - edge);
    const left = Math.min(Math.max(pickerRect.left, edge), maxLeft);
    const top = Math.max(edge, pickerRect.top - popoverRect.height - gap);
    const preferredArrowX = pickerRect.left + Math.min(46, Math.max(28, pickerRect.width * 0.25));
    const arrowLeft = Math.min(Math.max(preferredArrowX - left, 22), Math.max(22, popoverRect.width - 22));

    suggestionRegion.style.left = `${Math.round(left)}px`;
    suggestionRegion.style.top = `${Math.round(top)}px`;
    suggestionRegion.style.setProperty('--template-suggestion-arrow-left', `${Math.round(arrowLeft)}px`);
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
  const onDocumentPointerDown = (event) => {
    if (!activeSuggestion || !suggestionRegion || suggestionRegion.hidden) return;
    const target = event.target;
    if (suggestionRegion.contains(target) || templatePickerButton?.contains(target)) return;
    dismiss();
  };
  const preparePopover = () => {
    if (!suggestionRegion || typeof document === 'undefined') return;
    installPopoverStyles();
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
    suggestionRegion.setAttribute('role', 'dialog');
    suggestionRegion.setAttribute('aria-label', 'Template suggestion');
    suggestionRegion.removeAttribute('aria-live');
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
      if (typeof window !== 'undefined') {
        window.removeEventListener('resize', positionSuggestion);
        window.removeEventListener('scroll', positionSuggestion, true);
      }
      restorePopover();
    },
    dismiss,
    requestIfEligible,
  };
}
