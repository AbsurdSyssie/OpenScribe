import { csrfFetch } from '../csrf.js';

const MAX_BUNDLE_BYTES = 1024 * 1024;

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
  link.download = match?.[1] || 'openscribe-smart-phrases.json';
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function initExport(library) {
  const sidebar = library.querySelector('.smart-phrase-library-sidebar');
  const search = library.querySelector('[data-smart-phrase-search]');
  const open = library.querySelector('[data-smart-phrase-export-open]');
  const defaults = library.querySelector('.smart-phrase-library-utilities__default');
  const actions = library.querySelector('[data-smart-phrase-export-actions]');
  const submit = library.querySelector('[data-smart-phrase-export-submit]');
  const status = library.querySelector('[data-smart-phrase-export-status]');
  const checks = [...library.querySelectorAll('[data-smart-phrase-export-checkbox]')];
  let priorSearchValue = '';
  const rows = () => [...library.querySelectorAll('[data-smart-phrase-row]')];
  const selected = () => checks.filter((checkbox) => checkbox.checked);
  const sync = () => {
    const count = selected().length;
    submit.disabled = count === 0;
    status.textContent = count ? `${count} smart phrase${count === 1 ? '' : 's'} selected` : 'Select at least one smart phrase.';
  };
  const close = () => {
    sidebar.classList.remove('is-exporting');
    defaults.hidden = false;
    actions.hidden = true;
    search.value = priorSearchValue;
    search.disabled = false;
    search.dispatchEvent(new Event('input'));
    checks.forEach((checkbox) => { checkbox.checked = false; checkbox.closest('label').hidden = true; });
    sync();
  };
  open?.addEventListener('click', () => {
    sidebar.classList.add('is-exporting');
    defaults.hidden = true;
    actions.hidden = false;
    priorSearchValue = search.value;
    search.value = '';
    search.disabled = true;
    rows().forEach((row) => { row.hidden = false; });
    checks.forEach((checkbox) => { checkbox.closest('label').hidden = false; });
    checks[0]?.focus();
    sync();
  });
  // Keep existing CRUD/search handlers inert while selection mode is active.
  library.addEventListener('click', (event) => {
    if (!sidebar.classList.contains('is-exporting')) return;
    if (event.target.closest('.smart-phrase-library-row__check, [data-smart-phrase-export-checkbox]')) return;
    if (event.target.closest('.smart-phrase-library-row__select, .smart-phrase-library-row__actions')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  checks.forEach((checkbox) => checkbox.addEventListener('change', sync));
  library.querySelector('[data-smart-phrase-export-select-all]')?.addEventListener('click', () => {
    const shouldSelect = checks.some((checkbox) => !checkbox.checked);
    checks.forEach((checkbox) => { checkbox.checked = shouldSelect; });
    sync();
  });
  library.querySelector('[data-smart-phrase-export-cancel]')?.addEventListener('click', close);
  submit?.addEventListener('click', async () => {
    const smartPhraseIds = selected().map((checkbox) => checkbox.value);
    if (!smartPhraseIds.length) return;
    submit.disabled = true;
    status.textContent = 'Preparing export…';
    try {
      const response = await csrfFetch('/api/v1/smart-phrases/export', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ smart_phrase_ids: smartPhraseIds }) });
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
  // The dialog lives outside the library root, so lookup deliberately starts at document.
  const dialog = document.querySelector('[data-smart-phrase-import-dialog]');
  if (!dialog) return;
  const inputs = dialog.querySelector('[data-smart-phrase-import-inputs]');
  const preflight = dialog.querySelector('[data-smart-phrase-import-preflight]');
  const results = dialog.querySelector('[data-smart-phrase-import-results]');
  const summary = dialog.querySelector('[data-smart-phrase-import-summary]');
  const warningBox = dialog.querySelector('[data-smart-phrase-import-warnings]');
  const status = dialog.querySelector('[data-smart-phrase-import-status]');
  const confirm = dialog.querySelector('[data-smart-phrase-import-confirm]');
  const success = dialog.querySelector('[data-smart-phrase-import-success]');
  const successMessage = dialog.querySelector('[data-smart-phrase-import-success-message]');
  const continueButton = dialog.querySelector('[data-smart-phrase-import-continue]');
  const cancelButton = dialog.querySelector('[data-smart-phrase-import-cancel]');
  const intro = inputs.previousElementSibling;
  const fileInput = dialog.querySelector('[data-smart-phrase-import-file]');
  const pasteInput = dialog.querySelector('[data-smart-phrase-import-json]');
  let currentFile = null;
  let closeTimer = null;
  const libraryUrl = '/workspace/library/smart-phrases';
  const finishImport = () => window.location.assign(libraryUrl);
  const startCloseCountdown = () => {
    let seconds = 5; continueButton.textContent = `Close (${seconds})`; clearInterval(closeTimer);
    closeTimer = window.setInterval(() => {
      seconds -= 1;
      if (seconds <= 0) { clearInterval(closeTimer); finishImport(); return; }
      continueButton.textContent = `Close (${seconds})`;
    }, 1000);
  };
  const selectedIndexes = () => [...results.querySelectorAll('input[data-smart-phrase-import-index]:checked')].map((input) => Number(input.value));
  const syncConfirm = () => { confirm.disabled = selectedIndexes().length === 0 || !currentFile; };
  const showError = (message) => { status.textContent = message; status.classList.add('is-error'); confirm.disabled = true; };
  const reset = () => {
    clearInterval(closeTimer); closeTimer = null;
    currentFile = null; preflight.hidden = true; inputs.hidden = false; success.hidden = true; intro.hidden = false; results.replaceChildren(); warningBox.replaceChildren(); warningBox.hidden = true;
    status.textContent = ''; status.classList.remove('is-error'); confirm.disabled = true; confirm.hidden = false; cancelButton.hidden = false; continueButton.hidden = true; continueButton.textContent = 'Close (5)'; fileInput.value = ''; pasteInput.value = '';
  };
  const messageText = (message, fallback) => typeof message === 'string' ? message : `${message?.path ? `${message.path}: ` : ''}${message?.message || fallback}`;
  const renderPreflight = (body) => {
    const entries = Array.isArray(body.entries) ? body.entries : [];
    results.replaceChildren();
    entries.forEach((entry) => {
      const row = document.createElement('label'); row.className = 'smart-phrase-import-entry';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = String(entry.index); checkbox.dataset.smartPhraseImportIndex = ''; checkbox.checked = Boolean(entry.selected_by_default && entry.selectable); checkbox.disabled = !entry.selectable; checkbox.addEventListener('change', syncConfirm);
      const copy = document.createElement('span'); const trigger = document.createElement('strong'); trigger.textContent = `/${entry.proposed_trigger || entry.source_trigger || `PHRASE_${entry.index + 1}`}`; copy.append(trigger);
      if (entry.proposed_trigger && entry.source_trigger && entry.proposed_trigger !== entry.source_trigger) { const original = document.createElement('small'); original.textContent = `From /${entry.source_trigger}`; copy.append(original); }
      const badge = document.createElement('span'); badge.className = 'smart-phrase-import-entry__badge'; badge.textContent = String(entry.status || 'ready').replaceAll('_', ' ');
      row.append(checkbox, copy, badge);
      if (Array.isArray(entry.errors) && entry.errors.length) { const errors = document.createElement('ul'); errors.className = 'smart-phrase-import-entry__errors'; entry.errors.forEach((error) => { const item = document.createElement('li'); item.textContent = messageText(error, 'Invalid value'); errors.append(item); }); row.append(errors); }
      results.append(row);
    });
    const warnings = [...(body.warnings || []), ...entries.flatMap((entry) => entry.warnings || [])];
    warningBox.hidden = warnings.length === 0;
    if (warnings.length) { const heading = document.createElement('strong'); heading.textContent = `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`; const list = document.createElement('ul'); warnings.forEach((warning) => { const item = document.createElement('li'); item.textContent = messageText(warning, 'Field not imported'); list.append(item); }); warningBox.append(heading, list); }
    summary.textContent = `${entries.length} found · ${entries.filter((entry) => entry.selectable).length} available to import`;
    inputs.hidden = true; preflight.hidden = false; status.textContent = ''; syncConfirm();
  };
  const importCurrent = async (indexes) => {
    if (!currentFile || !indexes.length) return;
    confirm.disabled = true; inputs.hidden = true; status.classList.remove('is-error'); status.textContent = 'Importing smart phrases…';
    const data = new FormData(); data.append('bundle', currentFile, currentFile.name); data.append('selected_indexes', JSON.stringify(indexes));
    try {
      const response = await csrfFetch('/api/v1/smart-phrases/import', { method: 'POST', credentials: 'include', body: data });
      if (!response.ok) throw new Error(await errorMessage(response));
      const body = await response.json(); const imported = body.summary?.imported ?? 0;
      status.textContent = ''; preflight.hidden = true; intro.hidden = true;
      successMessage.textContent = `${imported} smart phrase${imported === 1 ? '' : 's'} imported and ready to use.`;
      success.hidden = false; confirm.hidden = true; cancelButton.hidden = true; continueButton.hidden = false; continueButton.focus(); startCloseCountdown();
      pasteInput.value = '';
    } catch (error) { showError(error.message); if (!preflight.hidden) syncConfirm(); else inputs.hidden = false; }
  };
  const isCleanSingleSmartPhrase = (body) => {
    const entries = Array.isArray(body.entries) ? body.entries : []; const entry = entries[0];
    return entries.length === 1 && entry?.status === 'ready' && entry.selectable && entry.selected_by_default && !(body.warnings || []).length && !(entry.warnings || []).length;
  };
  const preflightFile = async (file) => {
    preflight.hidden = true; results.replaceChildren(); warningBox.replaceChildren(); warningBox.hidden = true; status.textContent = ''; status.classList.remove('is-error'); confirm.disabled = true; currentFile = null;
    if (!file) return;
    if (file.size > MAX_BUNDLE_BYTES) return showError('Choose a JSON bundle no larger than 1 MiB.');
    currentFile = file; status.textContent = 'Checking bundle…';
    const data = new FormData(); data.append('bundle', file, file.name);
    try {
      const response = await csrfFetch('/api/v1/smart-phrases/import/preflight', { method: 'POST', credentials: 'include', body: data });
      if (!response.ok) throw new Error(await errorMessage(response));
      const body = await response.json();
      if (isCleanSingleSmartPhrase(body)) { await importCurrent([body.entries[0].index]); return; }
      renderPreflight(body);
    } catch (error) { currentFile = null; showError(error.message); }
  };
  const pastedFile = () => {
    let json = pasteInput.value.trim(); const fenced = json.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```\s*$/i);
    if (fenced) json = fenced[1].trim();
    if (!json) { showError('Paste a JSON smart phrase bundle first.'); return null; }
    try { JSON.parse(json); } catch (_) { showError('The pasted text is not valid JSON. Ask your AI assistant to check and resend it as valid JSON. A common cause is quotation marks inside smart phrase text that have not been escaped.'); return null; }
    return new File([json], 'pasted-openscribe-smart-phrases.json', { type: 'application/json' });
  };
  library.querySelector('[data-smart-phrase-import-open]')?.addEventListener('click', () => { reset(); dialog.showModal(); });
  dialog.addEventListener('close', () => { if (!success.hidden) finishImport(); else reset(); });
  fileInput.addEventListener('change', () => preflightFile(fileInput.files?.[0]));
  dialog.querySelector('[data-smart-phrase-import-paste-submit]')?.addEventListener('click', () => { const file = pastedFile(); if (file) preflightFile(file); });
  pasteInput.addEventListener('input', () => { if (status.classList.contains('is-error')) { status.textContent = ''; status.classList.remove('is-error'); } });
  dialog.querySelectorAll('[data-smart-phrase-import-dropzone]').forEach((zone) => { ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add('is-dragover'); })); ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.remove('is-dragover'); })); zone.addEventListener('drop', (event) => preflightFile(event.dataTransfer?.files?.[0])); });
  dialog.querySelector('[data-smart-phrase-import-change]')?.addEventListener('click', reset);
  confirm.addEventListener('click', () => importCurrent(selectedIndexes()));
  continueButton.addEventListener('click', finishImport);
}

function smartPhraseMakerPrompt(schema) {
  return `You are helping a lay user create an OpenScribe smart phrase bundle.

Treat any description the user supplies as the brief. Ask only the questions needed to resolve information that is missing or unclear. Depending on the brief, clarify when the phrase is used, the reusable wording needed, preferred tone, placeholders, and whether the user wants one phrase or a related set. Skip questions the brief has already answered.

Create one smart phrase unless the user explicitly asks for several or clearly describes a set.

A smart phrase inserts saved text into the note editor. The trigger is what the user types after a slash. Store trigger without the leading slash, using only uppercase A-Z, numbers, and underscores, with no spaces, up to 64 characters. For example, use "FOLLOWUP", not "/FOLLOWUP". Keep expansion_text useful as reusable text rather than patient-specific content, no longer than 2,000 characters. Use generic placeholders such as [date], [service], or [clinician name] where variable details are needed. Give each phrase a short description or null.

Do not ask for or include patient information, transcripts, clinical notes, credentials, or other confidential data. Use fictional or generic examples only.

When the brief is complete, return one complete OpenScribe smart phrase bundle conforming exactly to the JSON Schema below. Return raw JSON only: no Markdown code fence, explanation, preamble, or trailing text. Ensure the JSON parses and all required fields are present. Do not add ownership, scope, IDs, timestamps, usage counts, or other fields. Smart phrase bundles reject unknown fields.

JSON validity rules are mandatory:
- Before replying, check the entire output with JSON.parse or an equivalent strict JSON parser and correct every error.
- Use double quotes around JSON property names and string values.
- Never place an unescaped double quote inside a string value. Prefer wording that does not need quotation marks. If a double quote is essential, encode it as \\".
- Encode a line break inside a string as \\n. Never put a literal line break inside a JSON string.
- Do not return the bundle until the complete response passes the parse check.

OpenScribe smart phrase bundle JSON Schema:
${JSON.stringify(schema, null, 2)}`;
}

function initHelp(library) {
  const dialog = document.querySelector('[data-smart-phrase-help-dialog]');
  const open = library.querySelector('[data-smart-phrase-help-open]');
  const copy = dialog?.querySelector('[data-smart-phrase-help-copy]');
  const status = dialog?.querySelector('[data-smart-phrase-help-status]');
  const fallback = dialog?.querySelector('[data-smart-phrase-help-fallback]');
  const promptOutput = dialog?.querySelector('[data-smart-phrase-help-prompt]');
  if (!dialog || !open || !copy) return;
  let prompt = '';
  const loadPrompt = async () => {
    if (prompt) return prompt;
    const response = await fetch('/static/schemas/openscribe-smart-phrase-bundle-v1.schema.json?v=20260724-ai-instructions', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('The smart phrase instructions could not be loaded.');
    prompt = smartPhraseMakerPrompt(await response.json());
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

export function initSmartPhraseIO() {
  document.querySelectorAll('[data-smart-phrase-library]').forEach((library) => { initExport(library); initImport(library); initHelp(library); });
}
