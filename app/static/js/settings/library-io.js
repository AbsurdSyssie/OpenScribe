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

async function downloadResponse(response, filename) {
  const blob = await response.blob();
  const match = (response.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)"?/i);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match?.[1] || filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

const selector = (config, name) => `[data-${config.prefix}-${name}]`;
const messageText = (message, fallback) => typeof message === 'string'
  ? message
  : `${message?.path ? `${message.path}: ` : ''}${message?.message || fallback}`;

function isCleanSingleImport(body) {
  const entries = Array.isArray(body.entries) ? body.entries : [];
  const entry = entries[0];
  return entries.length === 1
    && entry?.status === 'ready'
    && entry.selectable
    && entry.selected_by_default
    && !(body.warnings || []).length
    && !(entry.warnings || []).length;
}

function initExport(library, config) {
  const sidebar = library.querySelector('.template-library-sidebar');
  const open = library.querySelector(selector(config, 'export-open'));
  const defaults = library.querySelector('.template-library-utilities__default');
  const actions = library.querySelector(selector(config, 'export-actions'));
  const submit = library.querySelector(selector(config, 'export-submit'));
  const status = library.querySelector(selector(config, 'export-status'));
  const checks = [...library.querySelectorAll(selector(config, 'export-checkbox'))];
  const selected = () => checks.filter((checkbox) => checkbox.checked);
  const sync = () => {
    const count = selected().length;
    submit.disabled = count === 0;
    status.textContent = count
      ? `${count} ${config.asset}${count === 1 ? '' : 's'} selected (maximum ${MAX_EXPORT_ITEMS}).`
      : `Select up to ${MAX_EXPORT_ITEMS} ${config.asset}s.`;
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
      status.textContent = `You can export up to ${MAX_EXPORT_ITEMS} ${config.asset}s at once.`;
      return;
    }
    sync();
  }));
  library.querySelector(selector(config, 'export-select-all'))?.addEventListener('click', () => {
    const shouldSelect = selected().length < Math.min(checks.length, MAX_EXPORT_ITEMS);
    checks.forEach((checkbox, index) => { checkbox.checked = shouldSelect && index < MAX_EXPORT_ITEMS; });
    sync();
  });
  library.querySelector(selector(config, 'export-cancel'))?.addEventListener('click', close);
  submit?.addEventListener('click', async () => {
    const ids = selected().map((checkbox) => checkbox.value);
    if (!ids.length) return;
    if (ids.length > MAX_EXPORT_ITEMS) {
      status.textContent = `You can export up to ${MAX_EXPORT_ITEMS} ${config.asset}s at once.`;
      return;
    }
    submit.disabled = true;
    status.textContent = 'Preparing export…';
    try {
      const response = await csrfFetch(config.exportEndpoint, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [config.exportIdsKey]: ids }),
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      await downloadResponse(response, config.exportFilename);
      close();
    } catch (error) {
      status.textContent = error.message;
      submit.disabled = false;
    }
  });
}

function initImport(library, config) {
  // The dialog intentionally remains document-scoped because it sits outside the library root.
  const dialog = document.querySelector(selector(config, 'import-dialog'));
  if (!dialog) return;
  const inputs = dialog.querySelector(selector(config, 'import-inputs'));
  const preflight = dialog.querySelector(selector(config, 'import-preflight'));
  const results = dialog.querySelector(selector(config, 'import-results'));
  const summary = dialog.querySelector(selector(config, 'import-summary'));
  const warningBox = dialog.querySelector(selector(config, 'import-warnings'));
  const status = dialog.querySelector(selector(config, 'import-status'));
  const confirm = dialog.querySelector(selector(config, 'import-confirm'));
  const success = dialog.querySelector(selector(config, 'import-success'));
  const successMessage = dialog.querySelector(selector(config, 'import-success-message'));
  const continueButton = dialog.querySelector(selector(config, 'import-continue'));
  const cancelButton = dialog.querySelector(selector(config, 'import-cancel'));
  const intro = dialog.querySelector(selector(config, 'import-intro'));
  const fileInput = dialog.querySelector(selector(config, 'import-file'));
  const pasteInput = dialog.querySelector(selector(config, 'import-json'));
  const pasteSubmit = dialog.querySelector(selector(config, 'import-paste-submit'));
  let currentFile = null;
  let currentDestination = null;
  let closeTimer = null;
  let isCommitting = false;
  let preflightRequestId = 0;
  const finishImport = () => window.location.assign(config.libraryUrl);
  const destination = () => dialog.querySelector(`${selector(config, 'import-destination')}:checked`)?.value || 'personal';
  const selectedIndexes = () => [...results.querySelectorAll(`input${selector(config, 'import-index')}:checked`)].map((input) => Number(input.value));
  const syncConfirm = () => { confirm.disabled = selectedIndexes().length === 0 || !currentFile; };
  const showError = (message) => {
    status.textContent = message;
    status.classList.add('is-error');
    confirm.disabled = true;
  };
  const startCloseCountdown = () => {
    let seconds = 5;
    continueButton.textContent = `Close (${seconds})`;
    clearInterval(closeTimer);
    closeTimer = window.setInterval(() => {
      seconds -= 1;
      if (seconds <= 0) {
        clearInterval(closeTimer);
        finishImport();
        return;
      }
      continueButton.textContent = `Close (${seconds})`;
    }, 1000);
  };
  const reset = () => {
    if (isCommitting) return;
    preflightRequestId += 1;
    clearInterval(closeTimer);
    closeTimer = null;
    currentFile = null;
    currentDestination = null;
    preflight.hidden = true;
    inputs.hidden = false;
    success.hidden = true;
    intro.hidden = false;
    results.replaceChildren();
    warningBox.replaceChildren();
    warningBox.hidden = true;
    status.textContent = '';
    status.classList.remove('is-error');
    confirm.disabled = true;
    confirm.hidden = false;
    cancelButton.hidden = false;
    continueButton.hidden = true;
    continueButton.textContent = 'Close (5)';
    fileInput.value = '';
    pasteInput.value = '';
    const personal = dialog.querySelector(`${selector(config, 'import-destination')}[value="personal"]`);
    if (personal) personal.checked = true;
  };
  const renderPreflight = (body) => {
    const entries = Array.isArray(body.entries) ? body.entries : [];
    results.replaceChildren();
    entries.forEach((entry) => {
      const row = document.createElement('label');
      row.className = 'template-import-entry';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = String(entry.index);
      checkbox.setAttribute(`data-${config.prefix}-import-index`, '');
      checkbox.checked = Boolean(entry.selected_by_default && entry.selectable);
      checkbox.disabled = !entry.selectable;
      checkbox.addEventListener('change', syncConfirm);
      const copy = document.createElement('span');
      const name = document.createElement('strong');
      name.textContent = entry.proposed_name || entry.source_name || `${config.assetLabel} ${entry.index + 1}`;
      copy.append(name);
      if (entry.proposed_name && entry.source_name && entry.proposed_name !== entry.source_name) {
        const original = document.createElement('small');
        original.textContent = `From “${entry.source_name}”`;
        copy.append(original);
      }
      const badge = document.createElement('span');
      badge.className = 'template-import-entry__badge';
      badge.textContent = String(entry.status || 'ready').replaceAll('_', ' ');
      row.append(checkbox, copy, badge);
      if (Array.isArray(entry.errors) && entry.errors.length) {
        const errors = document.createElement('ul');
        errors.className = 'template-import-entry__errors';
        entry.errors.forEach((error) => {
          const item = document.createElement('li');
          item.textContent = messageText(error, 'Invalid value');
          errors.append(item);
        });
        row.append(errors);
      }
      results.append(row);
    });
    const warnings = [...(body.warnings || []), ...entries.flatMap((entry) => entry.warnings || [])];
    warningBox.hidden = warnings.length === 0;
    if (warnings.length) {
      const heading = document.createElement('strong');
      heading.textContent = `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`;
      const list = document.createElement('ul');
      warnings.forEach((warning) => {
        const item = document.createElement('li');
        item.textContent = messageText(warning, 'Field not imported');
        list.append(item);
      });
      warningBox.append(heading, list);
    }
    summary.textContent = `${entries.length} found · ${entries.filter((entry) => entry.selectable).length} available to import`;
    inputs.hidden = true;
    preflight.hidden = false;
    status.textContent = '';
    syncConfirm();
  };
  const importCurrent = async (indexes) => {
    if (isCommitting || !currentFile || !indexes.length) return;
    isCommitting = true;
    confirm.disabled = true;
    inputs.hidden = true;
    status.classList.remove('is-error');
    status.textContent = `Importing ${config.asset}s…`;
    const data = new FormData();
    data.append('destination', currentDestination);
    data.append('bundle', currentFile, currentFile.name);
    data.append('selected_indexes', JSON.stringify(indexes));
    try {
      const response = await csrfFetch(config.importEndpoint, { method: 'POST', credentials: 'include', body: data });
      if (!response.ok) throw new Error(await errorMessage(response));
      const body = await response.json();
      const imported = body.summary?.imported ?? 0;
      isCommitting = false;
      status.textContent = '';
      preflight.hidden = true;
      intro.hidden = true;
      successMessage.textContent = `${imported} ${config.asset}${imported === 1 ? '' : 's'} imported and ready to use.`;
      success.hidden = false;
      confirm.hidden = true;
      cancelButton.hidden = true;
      continueButton.hidden = false;
      continueButton.focus();
      startCloseCountdown();
      pasteInput.value = '';
    } catch (error) {
      isCommitting = false;
      showError(error.message);
      if (!preflight.hidden) syncConfirm();
      else inputs.hidden = false;
    }
  };
  const preflightFile = async (file) => {
    if (isCommitting) return;
    const requestId = ++preflightRequestId;
    preflight.hidden = true;
    results.replaceChildren();
    warningBox.replaceChildren();
    warningBox.hidden = true;
    status.textContent = '';
    status.classList.remove('is-error');
    confirm.disabled = true;
    currentFile = null;
    currentDestination = null;
    if (!file) return;
    if (file.size > MAX_BUNDLE_BYTES) return showError('Choose a JSON bundle no larger than 1 MiB.');
    currentFile = file;
    currentDestination = destination();
    status.textContent = 'Checking bundle…';
    const data = new FormData();
    data.append('destination', currentDestination);
    data.append('bundle', file, file.name);
    try {
      const response = await csrfFetch(config.preflightEndpoint, { method: 'POST', credentials: 'include', body: data });
      if (requestId !== preflightRequestId) return;
      if (!response.ok) throw new Error(await errorMessage(response));
      const body = await response.json();
      if (requestId !== preflightRequestId) return;
      if (isCleanSingleImport(body)) {
        await importCurrent([body.entries[0].index]);
        return;
      }
      renderPreflight(body);
    } catch (error) {
      if (requestId !== preflightRequestId) return;
      currentFile = null;
      showError(error.message);
    }
  };
  const pastedFile = () => {
    let json = pasteInput.value.trim();
    const fenced = json.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```\s*$/i);
    if (fenced) json = fenced[1].trim();
    if (!json) {
      showError(config.emptyPasteMessage);
      return null;
    }
    try {
      JSON.parse(json);
    } catch (_) {
      showError(config.invalidPasteMessage);
      return null;
    }
    return new File([json], config.pastedFilename, { type: 'application/json' });
  };

  library.querySelector(selector(config, 'import-open'))?.addEventListener('click', () => { reset(); dialog.showModal(); });
  dialog.querySelector('form')?.addEventListener('submit', (event) => { if (isCommitting) event.preventDefault(); });
  dialog.addEventListener('cancel', (event) => { if (isCommitting) event.preventDefault(); });
  dialog.addEventListener('close', () => { if (isCommitting) return; if (!success.hidden) finishImport(); else reset(); });
  fileInput.addEventListener('change', () => preflightFile(fileInput.files?.[0]));
  pasteSubmit?.addEventListener('click', () => { const file = pastedFile(); if (file) preflightFile(file); });
  pasteInput.addEventListener('input', () => {
    if (status.classList.contains('is-error')) {
      status.textContent = '';
      status.classList.remove('is-error');
    }
  });
  dialog.querySelectorAll(selector(config, 'import-dropzone')).forEach((zone) => {
    ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add('is-dragover'); }));
    ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.remove('is-dragover'); }));
    zone.addEventListener('drop', (event) => preflightFile(event.dataTransfer?.files?.[0]));
  });
  dialog.querySelector(selector(config, 'import-change'))?.addEventListener('click', reset);
  confirm.addEventListener('click', () => importCurrent(selectedIndexes()));
  continueButton.addEventListener('click', finishImport);
}

export function initLibraryIO(library, config) {
  initExport(library, config);
  initImport(library, config);
}
