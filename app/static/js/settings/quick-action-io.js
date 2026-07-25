import { csrfFetch } from '../csrf.js';

const MAX_BUNDLE_BYTES = 1024 * 1024;
const MAX_EXPORT_ITEMS = 100;

async function errorMessage(response) {
  try {
    const body = await response.json();
    return body.error?.message || (typeof body.detail === 'string' ? body.detail : body.detail?.message) || body.message || 'The request could not be completed.';
  } catch (_) {
    return 'The request could not be completed.';
  }
}

async function downloadResponse(response) {
  const blob = await response.blob();
  const match = (response.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)"?/i);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match?.[1] || 'openscribe-quick-actions.json';
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function initExport(library) {
  const sidebar = library.querySelector('.template-library-sidebar');
  const open = library.querySelector('[data-quick-action-export-open]');
  const defaults = library.querySelector('.template-library-utilities__default');
  const actions = library.querySelector('[data-quick-action-export-actions]');
  const submit = library.querySelector('[data-quick-action-export-submit]');
  const status = library.querySelector('[data-quick-action-export-status]');
  const checks = [...library.querySelectorAll('[data-quick-action-export-checkbox]')];
  const selected = () => checks.filter((checkbox) => checkbox.checked);
  const sync = () => {
    const count = selected().length;
    submit.disabled = count === 0;
    status.textContent = count ? `${count} quick action${count === 1 ? '' : 's'} selected (maximum ${MAX_EXPORT_ITEMS}).` : 'Select up to 100 quick actions.';
  };
  const close = () => {
    sidebar.classList.remove('is-exporting');
    defaults.hidden = false;
    actions.hidden = true;
    checks.forEach((checkbox) => { checkbox.checked = false; checkbox.closest('label').hidden = true; });
    sync();
  };
  open?.addEventListener('click', () => {
    sidebar.classList.add('is-exporting');
    defaults.hidden = true;
    actions.hidden = false;
    checks.forEach((checkbox) => { checkbox.closest('label').hidden = false; });
    checks[0]?.focus();
    sync();
  });
  checks.forEach((checkbox) => checkbox.addEventListener('change', () => {
    if (checkbox.checked && selected().length > MAX_EXPORT_ITEMS) {
      checkbox.checked = false;
      status.textContent = `You can export up to ${MAX_EXPORT_ITEMS} quick actions at once.`;
      return;
    }
    sync();
  }));
  library.querySelector('[data-quick-action-export-select-all]')?.addEventListener('click', () => {
    const shouldSelect = selected().length < Math.min(checks.length, MAX_EXPORT_ITEMS);
    checks.forEach((checkbox, index) => { checkbox.checked = shouldSelect && index < MAX_EXPORT_ITEMS; });
    sync();
  });
  library.querySelector('[data-quick-action-export-cancel]')?.addEventListener('click', close);
  submit?.addEventListener('click', async () => {
    const quickActionIds = selected().map((checkbox) => checkbox.value);
    if (!quickActionIds.length) return;
    if (quickActionIds.length > MAX_EXPORT_ITEMS) {
      status.textContent = `You can export up to ${MAX_EXPORT_ITEMS} quick actions at once.`;
      return;
    }
    submit.disabled = true;
    status.textContent = 'Preparing export…';
    try {
      const response = await csrfFetch('/api/v1/quick-actions/export', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quick_action_ids: quickActionIds }) });
      if (!response.ok) throw new Error(await errorMessage(response));
      await downloadResponse(response);
      close();
    } catch (error) {
      status.textContent = error.message;
      submit.disabled = false;
    }
  });
}

function initImport(library) {
  // The dialog is intentionally document-scoped because it sits outside the library root.
  const dialog = document.querySelector('[data-quick-action-import-dialog]');
  if (!dialog) return;
  const inputs = dialog.querySelector('[data-quick-action-import-inputs]');
  const preflight = dialog.querySelector('[data-quick-action-import-preflight]');
  const results = dialog.querySelector('[data-quick-action-import-results]');
  const summary = dialog.querySelector('[data-quick-action-import-summary]');
  const warningBox = dialog.querySelector('[data-quick-action-import-warnings]');
  const status = dialog.querySelector('[data-quick-action-import-status]');
  const confirm = dialog.querySelector('[data-quick-action-import-confirm]');
  const success = dialog.querySelector('[data-quick-action-import-success]');
  const successMessage = dialog.querySelector('[data-quick-action-import-success-message]');
  const continueButton = dialog.querySelector('[data-quick-action-import-continue]');
  const cancelButton = dialog.querySelector('[data-quick-action-import-cancel]');
  const intro = dialog.querySelector('[data-quick-action-import-intro]');
  const fileInput = dialog.querySelector('[data-quick-action-import-file]');
  const pasteInput = dialog.querySelector('[data-quick-action-import-json]');
  let currentFile = null;
  let currentDestination = null;
  let closeTimer = null;
  let isCommitting = false;
  let preflightRequestId = 0;
  const libraryUrl = '/workspace/library/quick-actions';
  const finishImport = () => window.location.assign(libraryUrl);
  const startCloseCountdown = () => {
    let seconds = 5; continueButton.textContent = `Close (${seconds})`; clearInterval(closeTimer);
    closeTimer = window.setInterval(() => {
      seconds -= 1;
      if (seconds <= 0) { clearInterval(closeTimer); finishImport(); return; }
      continueButton.textContent = `Close (${seconds})`;
    }, 1000);
  };
  const destination = () => dialog.querySelector('[data-quick-action-import-destination]:checked')?.value || 'personal';
  const selectedIndexes = () => [...results.querySelectorAll('input[data-quick-action-import-index]:checked')].map((input) => Number(input.value));
  const syncConfirm = () => { confirm.disabled = selectedIndexes().length === 0 || !currentFile; };
  const showError = (message) => { status.textContent = message; status.classList.add('is-error'); confirm.disabled = true; };
  const reset = () => {
    if (isCommitting) return;
    preflightRequestId += 1;
    clearInterval(closeTimer); closeTimer = null;
    currentFile = null; currentDestination = null; preflight.hidden = true; inputs.hidden = false; success.hidden = true; intro.hidden = false;
    results.replaceChildren(); warningBox.replaceChildren(); warningBox.hidden = true;
    status.textContent = ''; status.classList.remove('is-error'); confirm.disabled = true; confirm.hidden = false; cancelButton.hidden = false; continueButton.hidden = true; continueButton.textContent = 'Close (5)';
    fileInput.value = ''; pasteInput.value = '';
    const personal = dialog.querySelector('[data-quick-action-import-destination][value="personal"]');
    if (personal) personal.checked = true;
  };
  const messageText = (message, fallback) => typeof message === 'string' ? message : `${message?.path ? `${message.path}: ` : ''}${message?.message || fallback}`;
  const renderPreflight = (body) => {
    const entries = Array.isArray(body.entries) ? body.entries : [];
    results.replaceChildren();
    entries.forEach((entry) => {
      const row = document.createElement('label'); row.className = 'template-import-entry';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = String(entry.index); checkbox.dataset.quickActionImportIndex = ''; checkbox.checked = Boolean(entry.selected_by_default && entry.selectable); checkbox.disabled = !entry.selectable; checkbox.addEventListener('change', syncConfirm);
      const copy = document.createElement('span'); const name = document.createElement('strong'); name.textContent = entry.proposed_name || entry.source_name || `Quick action ${entry.index + 1}`; copy.append(name);
      if (entry.proposed_name && entry.source_name && entry.proposed_name !== entry.source_name) { const original = document.createElement('small'); original.textContent = `From “${entry.source_name}”`; copy.append(original); }
      const badge = document.createElement('span'); badge.className = 'template-import-entry__badge'; badge.textContent = String(entry.status || 'ready').replaceAll('_', ' ');
      row.append(checkbox, copy, badge);
      if (Array.isArray(entry.errors) && entry.errors.length) { const errors = document.createElement('ul'); errors.className = 'template-import-entry__errors'; entry.errors.forEach((error) => { const item = document.createElement('li'); item.textContent = messageText(error, 'Invalid value'); errors.append(item); }); row.append(errors); }
      results.append(row);
    });
    const warnings = [...(body.warnings || []), ...entries.flatMap((entry) => entry.warnings || [])];
    warningBox.hidden = warnings.length === 0;
    if (warnings.length) { const heading = document.createElement('strong'); heading.textContent = `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`; const list = document.createElement('ul'); warnings.forEach((warning) => { const item = document.createElement('li'); item.textContent = messageText(warning, 'Field not imported'); list.append(item); }); warningBox.append(heading, list); }
    summary.textContent = `${entries.length} found · ${entries.filter((entry) => entry.selectable).length} available to import`;
    inputs.hidden = true; preflight.hidden = false; status.textContent = ''; syncConfirm();
  };
  const importCurrent = async (indexes) => {
    if (isCommitting || !currentFile || !indexes.length) return;
    isCommitting = true;
    confirm.disabled = true; inputs.hidden = true; status.classList.remove('is-error'); status.textContent = 'Importing quick actions…';
    const data = new FormData(); data.append('destination', currentDestination); data.append('bundle', currentFile, currentFile.name); data.append('selected_indexes', JSON.stringify(indexes));
    try {
      const response = await csrfFetch('/api/v1/quick-actions/import', { method: 'POST', credentials: 'include', body: data });
      if (!response.ok) throw new Error(await errorMessage(response));
      const body = await response.json(); const imported = body.summary?.imported ?? 0; isCommitting = false;
      status.textContent = ''; preflight.hidden = true; intro.hidden = true;
      successMessage.textContent = `${imported} quick action${imported === 1 ? '' : 's'} imported and ready to use.`;
      success.hidden = false; confirm.hidden = true; cancelButton.hidden = true; continueButton.hidden = false; continueButton.focus(); startCloseCountdown();
      pasteInput.value = '';
    } catch (error) { isCommitting = false; showError(error.message); if (!preflight.hidden) syncConfirm(); else inputs.hidden = false; }
  };
  const isCleanSingleQuickAction = (body) => {
    const entries = Array.isArray(body.entries) ? body.entries : []; const entry = entries[0];
    return entries.length === 1 && entry?.status === 'ready' && entry.selectable && entry.selected_by_default && !(body.warnings || []).length && !(entry.warnings || []).length;
  };
  const preflightFile = async (file) => {
    if (isCommitting) return;
    const requestId = ++preflightRequestId;
    preflight.hidden = true; results.replaceChildren(); warningBox.replaceChildren(); warningBox.hidden = true; status.textContent = ''; status.classList.remove('is-error'); confirm.disabled = true; currentFile = null; currentDestination = null;
    if (!file) return;
    if (file.size > MAX_BUNDLE_BYTES) return showError('Choose a JSON bundle no larger than 1 MiB.');
    currentFile = file; currentDestination = destination(); status.textContent = 'Checking bundle…';
    const data = new FormData(); data.append('destination', currentDestination); data.append('bundle', file, file.name);
    try {
      const response = await csrfFetch('/api/v1/quick-actions/import/preflight', { method: 'POST', credentials: 'include', body: data });
      if (requestId !== preflightRequestId) return;
      if (!response.ok) throw new Error(await errorMessage(response));
      const body = await response.json();
      if (requestId !== preflightRequestId) return;
      if (isCleanSingleQuickAction(body)) { await importCurrent([body.entries[0].index]); return; }
      renderPreflight(body);
    } catch (error) {
      if (requestId !== preflightRequestId) return;
      currentFile = null;
      showError(error.message);
    }
  };
  const pastedFile = () => {
    let json = pasteInput.value.trim(); const fenced = json.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```\s*$/i);
    if (fenced) json = fenced[1].trim();
    if (!json) { showError('Paste a JSON quick action bundle first.'); return null; }
    try { JSON.parse(json); } catch (_) { showError('The pasted text is not valid JSON. Ask your AI assistant to check and resend it as valid JSON. A common cause is quotation marks inside quick action text that have not been escaped.'); return null; }
    return new File([json], 'pasted-openscribe-quick-actions.json', { type: 'application/json' });
  };
  library.querySelector('[data-quick-action-import-open]')?.addEventListener('click', () => { reset(); dialog.showModal(); });
  dialog.querySelector('form')?.addEventListener('submit', (event) => { if (isCommitting) event.preventDefault(); });
  dialog.addEventListener('cancel', (event) => { if (isCommitting) event.preventDefault(); });
  dialog.addEventListener('close', () => { if (isCommitting) return; if (!success.hidden) finishImport(); else reset(); });
  fileInput.addEventListener('change', () => preflightFile(fileInput.files?.[0]));
  dialog.querySelector('[data-quick-action-import-paste-submit]')?.addEventListener('click', () => { const file = pastedFile(); if (file) preflightFile(file); });
  pasteInput.addEventListener('input', () => { if (status.classList.contains('is-error')) { status.textContent = ''; status.classList.remove('is-error'); } });
  dialog.querySelectorAll('[data-quick-action-import-dropzone]').forEach((zone) => { ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add('is-dragover'); })); ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.remove('is-dragover'); })); zone.addEventListener('drop', (event) => preflightFile(event.dataTransfer?.files?.[0])); });
  dialog.querySelector('[data-quick-action-import-change]')?.addEventListener('click', reset);
  confirm.addEventListener('click', () => importCurrent(selectedIndexes()));
  continueButton.addEventListener('click', finishImport);
}

function quickActionMakerPrompt(schema) {
  return `You are helping a lay user create an OpenScribe quick action bundle.

Treat any description the user supplies as the brief. Ask only the questions needed to resolve information that is missing or unclear. Depending on the brief, clarify what the action should produce, who will read it, the desired tone and structure, required content, what to omit, and how it should handle missing consultation information. Skip questions the brief has already answered.

Create one quick action unless the user explicitly asks for several or clearly describes a set.

A quick action is a reusable instruction that OpenScribe applies to the current consultation. Write prompt_text as the instruction OpenScribe should follow, not as an example output. It may tell OpenScribe to create a document, summary, message, checklist, or other useful result. It must use only information supported by the consultation, must not invent facts, and should say how to handle missing information when that matters.

Every latest_version must have mode "freeform". Give each action a clear name and either a short description or null.

Do not ask for or include patient information, transcripts, clinical notes, credentials, or other confidential data. Use fictional or generic examples only.

When the brief is complete, return one complete OpenScribe quick action bundle conforming exactly to the JSON Schema below. Return raw JSON only: no Markdown code fence, explanation, preamble, or trailing text. Ensure the JSON parses and all required fields are present. Do not add ownership, scope, IDs, timestamps, active state, or other non-portable fields.

JSON validity rules are mandatory:
- Before replying, check the entire output with JSON.parse or an equivalent strict JSON parser and correct every error.
- Use double quotes around JSON property names and string values.
- Never place an unescaped double quote inside a string value. Prefer wording that does not need quotation marks. If a double quote is essential, encode it as \\".
- Encode a line break inside a string as \\n. Never put a literal line break inside a JSON string.
- Do not return the bundle until the complete response passes the parse check.

OpenScribe quick action bundle JSON Schema:
${JSON.stringify(schema, null, 2)}`;
}

function initHelp(library) {
  const dialog = document.querySelector('[data-quick-action-help-dialog]');
  const open = library.querySelector('[data-quick-action-help-open]');
  const copy = dialog?.querySelector('[data-quick-action-help-copy]');
  const status = dialog?.querySelector('[data-quick-action-help-status]');
  const fallback = dialog?.querySelector('[data-quick-action-help-fallback]');
  const promptOutput = dialog?.querySelector('[data-quick-action-help-prompt]');
  if (!dialog || !open || !copy) return;
  let prompt = '';
  const loadPrompt = async () => {
    if (prompt) return prompt;
    const response = await fetch('/static/schemas/openscribe-quick-action-bundle-v1.schema.json?v=20260724-ai-instructions', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('The quick action instructions could not be loaded.');
    prompt = quickActionMakerPrompt(await response.json());
    return prompt;
  };
  open.addEventListener('click', () => {
    status.textContent = ''; status.classList.remove('is-error'); fallback.hidden = true; promptOutput.value = ''; dialog.showModal();
  });
  copy.addEventListener('click', async () => {
    copy.disabled = true; status.textContent = 'Preparing instructions…'; status.classList.remove('is-error');
    try {
      const text = await loadPrompt();
      try {
        await navigator.clipboard.writeText(text);
        fallback.hidden = true;
        status.textContent = 'Instructions copied. Now paste them into your AI assistant.';
      } catch (_) {
        promptOutput.value = text; fallback.hidden = false; promptOutput.focus(); promptOutput.select();
        status.textContent = 'Automatic copying was blocked. Copy all the selected text below.';
      }
    } catch (error) {
      status.textContent = error.message; status.classList.add('is-error');
    } finally {
      copy.disabled = false;
    }
  });
}

export function initQuickActionIO() {
  document.querySelectorAll('[data-quick-action-library]').forEach((library) => { initExport(library); initImport(library); initHelp(library); });
}
