import { csrfFetch } from '../csrf.js';

const root = document.querySelector('[data-generation-trace]');
if (root) {
  let transcriptId = root.dataset.transcriptId;
  const statusNode = root.querySelector('[data-generation-trace-status]');
  const eventList = root.querySelector('[data-generation-trace-events]');
  const detailRegion = root.querySelector('[data-generation-trace-detail]');
  const clearButton = root.querySelector('[data-generation-trace-clear]');
  const refreshButton = root.querySelector('[data-generation-trace-refresh]');
  const indicator = root.querySelector('[data-generation-trace-indicator]');
  const collectionUrl = () => `/api/v1/transcripts/${encodeURIComponent(transcriptId)}/generation-traces`;
  let loaded = false;
  let expiryTimer = null;
  let requestGeneration = 0;

  const clearLoadedContent = (message = 'Trace content has been cleared.') => {
    requestGeneration += 1;
    if (expiryTimer !== null) window.clearTimeout(expiryTimer);
    expiryTimer = null;
    detailRegion.replaceChildren();
    detailRegion.hidden = true;
    eventList.replaceChildren();
    delete eventList.dataset.expiresAt;
    loaded = false;
    statusNode.textContent = message;
  };

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const getJson = async (url) => {
    const response = await csrfFetch(url, { method: 'GET', cache: 'no-store' });
    if (!response.ok) {
      const error = new Error(response.status === 410 ? 'expired' : 'unavailable');
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  };

  const when = (value) => {
    if (!value) return 'Time unavailable';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleString();
  };

  const updateCaptureState = (enabled) => {
    const isEnabled = Boolean(enabled);
    root.dataset.enabled = String(isEnabled);
    indicator.textContent = isEnabled ? 'Capture on · owner only' : 'Capture off';
  };

  const errorText = (error) => {
    if (error.status === 410) return 'This consultation has expired. Its traces are no longer available.';
    if (error.status === 403) return 'Only the consultation owner can view these traces.';
    return 'Trace history could not be loaded. Try opening this panel again.';
  };

  const safeReason = (reason) => {
    if (!reason) return '';
    return String(reason).replace(/[^a-zA-Z0-9_-]/g, ' ').slice(0, 100);
  };

  const showEditorComparison = (event, parent) => {
    if (!event.generated_document_id) return;
    const noteWorkspace = document.querySelector('.structured-workspace');
    const noteVersion = noteWorkspace?.querySelector('[data-latest-generated-id]');
    const followup = document.querySelector('[data-latest-followup-output]');
    const output = noteVersion?.dataset.latestGeneratedId === String(event.generated_document_id)
      ? noteWorkspace
      : followup?.dataset.latestFollowupId === String(event.generated_document_id) ? followup : null;
    if (!output) {
      parent.append(element('p', 'generation-trace__comparison-note', 'This trace is linked to a draft that is not currently open. Open that draft in the workspace to compare its editor contents.'));
      return;
    }
    const projection = [];
    const structured = output.querySelectorAll('[data-structured-line-input]');
    if (structured.length) {
      structured.forEach((field) => {
        const label = field.dataset.sectionLabel || 'Section';
        projection.push(`${label}\n${field.value}`);
      });
    } else {
      const freeform = output.querySelectorAll('[data-freeform-note-input], [data-followup-body-input]');
      freeform.forEach((field) => projection.push(field.value));
    }
    const heading = element('h3', '', 'Current editor contents');
    const content = element('pre');
    content.textContent = projection.length ? projection.join('\n\n') : 'The linked draft has no editable text in the current view.';
    const wrapper = element('section', 'generation-trace__content');
    wrapper.append(heading, element('p', 'generation-trace__comparison-note', 'This reads the existing owner-authorized editor fields. It may include unsaved clinician edits and is not a saved historical snapshot.'));
    wrapper.append(content);
    parent.append(wrapper);
  };

  const contentSection = (title, value, { escaped = false } = {}) => {
    const section = element('section', 'generation-trace__content');
    section.append(element('h3', '', title));
    const controls = element('div', 'generation-trace__whitespace');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    const label = element('label');
    label.append(checkbox, document.createTextNode(' Show spaces, tabs and line breaks'));
    controls.append(label);
    const pre = element('pre');
    const raw = typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2);
    const visibleWhitespace = raw
      .replace(/\r/g, '␍')
      .replace(/\n/g, '↵\n')
      .replace(/\t/g, '→   ')
      .replace(/ /g, '·');
    pre.textContent = escaped ? visibleWhitespace : raw;
    checkbox.addEventListener('change', () => {
      pre.textContent = checkbox.checked ? visibleWhitespace : raw;
    });
    section.append(controls, pre);
    return section;
  };

  const promptStringsFromRequest = (request) => {
    let value = request;
    if (typeof value === 'string') {
      try { value = JSON.parse(value); } catch { return []; }
    }
    if (!value || typeof value !== 'object') return [];
    const prompts = [];
    const add = (role, text) => {
      if (typeof text === 'string') prompts.push({ role, text });
    };
    const addParts = (role, parts) => {
      if (Array.isArray(parts)) parts.forEach((part) => add(role, part?.text));
    };
    if (Array.isArray(value.messages)) value.messages.forEach((message) => {
      const role = typeof message?.role === 'string' ? message.role : 'message';
      if (typeof message?.content === 'string') add(role, message.content);
      else if (Array.isArray(message?.content)) message.content.forEach((part) => add(role, part?.text));
    });
    if (value.system_instruction) addParts('system instruction', value.system_instruction.parts);
    if (Array.isArray(value.contents)) value.contents.forEach((content) => addParts(content?.role || 'content', content?.parts));
    return prompts;
  };

  const loadDetail = async (event, button) => {
    const generation = ++requestGeneration;
    const requestedTranscriptId = transcriptId;
    detailRegion.replaceChildren();
    detailRegion.hidden = false;
    detailRegion.append(element('p', 'generation-trace__status', 'Loading the selected trace…'));
    eventList.querySelectorAll('button').forEach((item) => item.removeAttribute('aria-current'));
    button.setAttribute('aria-current', 'true');
    try {
      const detail = await getJson(`${collectionUrl()}/${encodeURIComponent(event.id)}`);
      if (generation !== requestGeneration || requestedTranscriptId !== transcriptId || !root.open) return;
      detailRegion.replaceChildren();
      if (expiryTimer !== null) window.clearTimeout(expiryTimer);
      const state = detail.state || event.state || 'incomplete';
      eventList.dataset.expiresAt = detail.expires_at || event.expires_at || '';
      detailRegion.append(element('h3', '', state === 'complete' ? 'Trace details' : 'Trace details · incomplete'));
      const facts = [];
      if (detail.provider_adapter) facts.push(`Provider: ${detail.provider_adapter}`);
      if (detail.provider_model) facts.push(`Model: ${detail.provider_model}`);
      if (detail.request_ordinal) facts.push(`Request ${detail.request_ordinal}`);
      if (detail.outcome) facts.push(`Outcome: ${detail.outcome}`);
      if (detail.validation) facts.push(`Workflow validation: ${detail.validation}`);
      if (detail.error_code) facts.push(`Error code: ${safeReason(detail.error_code)}`);
      if (detail.decision) facts.push(`Next: ${detail.decision}`);
      if (detail.http_status) facts.push(`HTTP ${detail.http_status}`);
      if (Number.isFinite(detail.retry_wait_ms) && detail.retry_wait_ms > 0) facts.push(`Backoff: ${detail.retry_wait_ms} ms`);
      if (facts.length) detailRegion.append(element('p', 'generation-trace__status', facts.join(' · ')));
      if (state !== 'complete') {
        const reason = safeReason(detail.reason || event.reason);
        detailRegion.append(element('p', 'generation-trace__status', reason
          ? `This trace is incomplete. Some request or response content may be missing (${reason}).`
          : 'This trace is incomplete. Some request or response content may be missing.'));
      }
      const requestValue = detail.request ?? detail.request_body;
      if (requestValue !== undefined && requestValue !== null) {
        detailRegion.append(contentSection('Provider-bound request · redacted content', requestValue));
        promptStringsFromRequest(requestValue).forEach(({ role, text }) => {
          detailRegion.append(contentSection(`Derived prompt text · ${role}`, text));
        });
      } else {
        detailRegion.append(element('p', 'generation-trace__status', 'No request content was retained for this event.'));
      }
      if ((detail.response !== undefined && detail.response !== null) || (detail.response_text !== undefined && detail.response_text !== null)) {
        detailRegion.append(contentSection('Raw model response · redaction placeholders remain', detail.response ?? detail.response_text));
      } else {
        detailRegion.append(element('p', 'generation-trace__status', 'No model response content was retained for this event.'));
      }
      if (detail.parsed_json_available === false) detailRegion.append(element('p', 'generation-trace__status', 'JSON syntax parse preview is unavailable.'));
      if (detail.parse_status) detailRegion.append(element('p', 'generation-trace__status', `JSON parsing: ${safeReason(detail.parse_status)}`));
      if (detail.validation_status) detailRegion.append(element('p', 'generation-trace__status', `Workflow validation: ${safeReason(detail.validation_status)}`));
      else if (detail.validation == null) detailRegion.append(element('p', 'generation-trace__status', 'Workflow validation: not captured for this event.'));
      if (detail.error_code) detailRegion.append(element('p', 'generation-trace__status', `Error code: ${safeReason(detail.error_code)}`));
      const parsedJson = detail.parsed_json_available === true ? detail.parsed_json_preview : undefined;
      const parsed = detail.parsed_output ?? detail.validated_output ?? detail.parsed;
      if (parsedJson !== undefined) {
        detailRegion.append(contentSection('JSON syntax parse preview · schema validation is separate', parsedJson));
      }
      if (parsed !== undefined) detailRegion.append(contentSection('Validated result · provider placeholders remain', parsed));
      showEditorComparison(detail, detailRegion);
      if (detail.expires_at || event.expires_at) {
        const expiresAt = new Date(detail.expires_at || event.expires_at).getTime();
        detailRegion.append(element('p', 'generation-trace__comparison-note', `Expires: ${when(detail.expires_at || event.expires_at)}. Traces expire within 24 hours, or earlier when the consultation expires or is deleted.`));
        if (Number.isFinite(expiresAt)) {
          const delay = expiresAt - Date.now();
          if (delay <= 0) clearLoadedContent('This trace has expired and its content has been cleared.');
          else expiryTimer = window.setTimeout(() => clearLoadedContent('This trace has expired and its content has been cleared.'), delay);
        }
      }
    } catch (error) {
      if (generation !== requestGeneration || requestedTranscriptId !== transcriptId) return;
      detailRegion.replaceChildren(element('p', 'generation-trace__status', errorText(error)));
    }
  };

  const loadHistory = async () => {
    if (!transcriptId) {
      statusNode.textContent = 'Open a consultation to see its generation traces.';
      return;
    }
    const generation = ++requestGeneration;
    const requestedTranscriptId = transcriptId;
    if (expiryTimer !== null) window.clearTimeout(expiryTimer);
    expiryTimer = null;
    statusNode.textContent = 'Loading trace history…';
    eventList.replaceChildren();
    detailRegion.replaceChildren();
    detailRegion.hidden = true;
    try {
      const payload = await getJson(collectionUrl());
      if (generation !== requestGeneration || requestedTranscriptId !== transcriptId || !root.open) return;
      updateCaptureState(payload.enabled);
      const events = Array.isArray(payload.events) ? payload.events : [];
      const missingAttempts = Array.isArray(payload.missing_attempts) ? payload.missing_attempts : [];
      loaded = true;
      clearButton.hidden = events.length === 0 && missingAttempts.length === 0;
      eventList.replaceChildren();
      if (missingAttempts.length) {
        const notice = element('li', 'generation-trace__missing', `Capture unavailable (off, expired, or failed) for ${missingAttempts.length} recent provider attempt${missingAttempts.length === 1 ? '' : 's'}.`);
        if (payload.missing_attempts_bounded) notice.append(element('small', '', 'Only the most recent bounded set is shown.'));
        eventList.append(notice);
        missingAttempts.forEach((attempt) => {
          const item = element('li', 'generation-trace__missing-attempt');
          const facts = [];
          if (attempt.occurred_at) facts.push(when(attempt.occurred_at));
          if (attempt.request_ordinal) facts.push(`Request ${attempt.request_ordinal}`);
          if (attempt.outcome) facts.push(attempt.outcome);
          if (attempt.http_status) facts.push(`HTTP ${attempt.http_status}`);
          if (attempt.retry_wait_ms > 0) facts.push(`Backoff ${attempt.retry_wait_ms} ms`);
          if (attempt.attempt_id) facts.push(`Attempt ${String(attempt.attempt_id).slice(0, 8)}`);
          item.textContent = facts.join(' · ') || 'Provider attempt observed; details unavailable.';
          eventList.append(item);
        });
      }
      if (!events.length) {
        statusNode.textContent = payload.enabled
          ? missingAttempts.length ? 'Some provider attempts were observed, but their trace content is unavailable.' : 'Capture is on. No traces have been recorded for this consultation yet.'
          : 'Capture is off. No retained traces are available for this consultation.';
        return;
      }
      statusNode.textContent = `${events.length} trace event${events.length === 1 ? '' : 's'} available${missingAttempts.length ? `; ${missingAttempts.length} observed attempt${missingAttempts.length === 1 ? ' lacks' : 's lack'} trace content` : ''}. Select an event to load its content.`;
      events.forEach((event) => {
        const item = element('li');
        const button = element('button');
        button.type = 'button';
        const stage = String(event.workflow || event.phase || 'Generation event')
          .replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
        const state = event.state === 'complete' ? 'Complete' : event.state === 'pending' ? 'Pending' : 'Incomplete';
        button.append(element('span', '', `${stage} · ${state}`));
        const extra = [when(event.captured_at)];
        if (event.request_ordinal) extra.push(`Request ${event.request_ordinal}`);
        if (event.provider_adapter) extra.push(event.provider_adapter);
        if (event.provider_model) extra.push(event.provider_model);
        if (event.outcome) extra.push(event.outcome);
        if (event.validation) extra.push(`Workflow validation ${event.validation}`);
        if (event.error_code) extra.push(`Error ${safeReason(event.error_code)}`);
        if (event.decision) extra.push(`Next: ${event.decision}`);
        if (event.retry_wait_ms > 0) extra.push(`Backoff ${event.retry_wait_ms} ms`);
        if (event.attempt_id) extra.push(`Attempt ${String(event.attempt_id).slice(0, 8)}`);
        if (event.attempt_number) extra.push(`Attempt #${event.attempt_number}`);
        if (event.correlation_id) extra.push(`Lineage ${String(event.correlation_id).slice(0, 8)}`);
        if (event.backup_of_attempt_id) extra.push(`Backup of ${String(event.backup_of_attempt_id).slice(0, 8)}`);
        if (event.repair_of_attempt_id) extra.push(`Repair of ${String(event.repair_of_attempt_id).slice(0, 8)}`);
        if (event.split_execution_id) extra.push(`Split ${String(event.split_execution_id).slice(0, 8)}`);
        if (event.expires_at) extra.push(`Expires ${when(event.expires_at)}`);
        if (event.reason) extra.push(`Reason: ${safeReason(event.reason)}`);
        button.append(element('small', '', extra.join(' · ')));
        button.addEventListener('click', () => loadDetail(event, button));
        item.append(button);
        eventList.append(item);
      });
    } catch (error) {
      if (generation !== requestGeneration || requestedTranscriptId !== transcriptId) return;
      loaded = false;
      clearButton.hidden = true;
      statusNode.textContent = errorText(error);
    }
  };

  root.addEventListener('toggle', () => {
    if (root.open && !loaded) void loadHistory();
  });

  refreshButton.addEventListener('click', async () => {
    if (!root.open) return;
    refreshButton.disabled = true;
    try {
      await loadHistory();
    } finally {
      refreshButton.disabled = false;
    }
  });

  document.addEventListener('transcribe:workspace-applied', (event) => {
    const nextId = event.detail?.transcriptId || '';
    const changed = String(nextId) !== String(transcriptId || '');
    if (changed) clearLoadedContent(nextId ? 'Open this panel to load trace history.' : 'Open a consultation to see its generation traces.');
    transcriptId = nextId;
    root.dataset.transcriptId = nextId;
    root.hidden = !nextId;
    updateCaptureState(event.detail?.generationTraceEnabled);
    if (changed) root.open = false;
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    const expiry = eventList.dataset.expiresAt;
    if (expiry && new Date(expiry).getTime() <= Date.now()) clearLoadedContent('This trace has expired and its content has been cleared.');
  });
  window.addEventListener('pagehide', () => clearLoadedContent('Trace content has been cleared.'));

  clearButton.addEventListener('click', async () => {
    if (!window.confirm('Clear all generation traces for this consultation? This cannot be undone.')) return;
    clearButton.disabled = true;
    const targetTranscriptId = transcriptId;
    const targetUrl = collectionUrl();
    clearLoadedContent('Clearing traces…');
    const generation = requestGeneration;
    try {
      const response = await csrfFetch(targetUrl, { method: 'DELETE', cache: 'no-store' });
      if (!response.ok) throw new Error('clear-failed');
      if (generation === requestGeneration && targetTranscriptId === transcriptId) await loadHistory();
    } catch (error) {
      if (generation === requestGeneration && targetTranscriptId === transcriptId) statusNode.textContent = errorText(error);
    } finally {
      clearButton.disabled = false;
    }
  });
}
