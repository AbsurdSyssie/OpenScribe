import { csrfFetch } from '../csrf.js';

(function () {
  const root = document.querySelector('[data-smart-phrase-library]');
  if (!root) return;

  const form = root.querySelector('[data-smart-phrase-form]');
  const searchInput = root.querySelector('[data-smart-phrase-search]');
  const errorBox = form?.querySelector('[data-smart-phrase-error]');
  const libraryFeedback = root.querySelector('[data-smart-phrase-library-feedback]');
  const statusBox = form?.querySelector('[data-smart-phrase-status]');
  const saveButton = form?.querySelector('[data-smart-phrase-save]');
  let initialPayload = form ? JSON.stringify(formPayload(form)) : '';
  let allowNavigation = false;

  function rows() {
    return [...root.querySelectorAll('[data-smart-phrase-row]')];
  }

  function rowRecord(row) {
    try {
      return JSON.parse(row.dataset.smartPhraseRecord || '{}');
    } catch (_) {
      return {};
    }
  }

  function formPayload(targetForm) {
    const values = new FormData(targetForm);
    return {
      trigger: String(values.get('trigger') || '').trim(),
      expansion_text: String(values.get('expansion_text') || '').trim(),
      description: String(values.get('description') || '').trim() || null,
    };
  }

  function canonicalUrl(id = '') {
    const params = new URLSearchParams({ tab: 'smart-phrases' });
    if (id) params.set('smart_phrase_id', id);
    return `/settings?${params.toString()}`;
  }

  async function errorMessage(response, fallback) {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg.replace(/^Value error, /, '');
      return body?.error?.message || fallback;
    } catch (_) {
      return fallback;
    }
  }

  async function savePhrase(payload, id = '') {
    const response = await csrfFetch(id ? `/api/v1/smart-phrases/personal/${id}` : '/api/v1/smart-phrases/personal', {
      method: id ? 'PATCH' : 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await errorMessage(response, 'Could not save smart phrase.'));
    return response.json();
  }

  async function deletePhrase(id) {
    const response = await csrfFetch(`/api/v1/smart-phrases/personal/${id}`, { method: 'DELETE', credentials: 'include' });
    if (!response.ok) throw new Error(await errorMessage(response, 'Could not delete smart phrase.'));
  }

  function nextCopyTrigger(sourceTrigger) {
    const existing = new Set(rows().map((row) => String(rowRecord(row).trigger || '').toUpperCase()));
    let candidate = `${String(sourceTrigger || '').toUpperCase()}_COPY`;
    let suffix = 2;
    while (existing.has(candidate)) {
      candidate = `${String(sourceTrigger || '').toUpperCase()}_COPY_${suffix}`;
      suffix += 1;
    }
    return candidate;
  }

  function showError(message = '') {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = !message;
    if (message) errorBox.focus?.();
  }

  function showLibraryError(message = '') {
    if (!libraryFeedback) return;
    libraryFeedback.textContent = message;
    libraryFeedback.hidden = !message;
  }

  function navigateTo(id = '') {
    allowNavigation = true;
    window.location.assign(canonicalUrl(id));
  }

  function confirmDiscardIfDirty() {
    if (!form || JSON.stringify(formPayload(form)) === initialPayload) return true;
    return window.confirm('Discard unsaved smart phrase changes?');
  }

  async function duplicate(record) {
    const payload = {
      trigger: nextCopyTrigger(record.trigger),
      expansion_text: record.expansion_text,
      description: record.description || null,
    };
    const phrase = await savePhrase(payload);
    navigateTo(phrase.id);
  }

  root.addEventListener('click', async (event) => {
    const row = event.target.closest('[data-smart-phrase-row]');
    const duplicateRowButton = event.target.closest('[data-smart-phrase-duplicate-row]');
    const deleteRowButton = event.target.closest('[data-smart-phrase-delete-row]');
    if (!row || (!duplicateRowButton && !deleteRowButton)) return;
    event.preventDefault();
    showLibraryError();
    const record = rowRecord(row);
    const leavesCurrentEditor = Boolean(duplicateRowButton || (deleteRowButton && form?.dataset.smartPhraseId === record.id));
    if (leavesCurrentEditor && !confirmDiscardIfDirty()) return;
    try {
      if (duplicateRowButton) {
        await duplicate(record);
        return;
      }
      if (!record.id || !window.confirm(`Delete /${record.trigger} permanently?`)) return;
      await deletePhrase(record.id);
      row.remove();
      const count = root.querySelector('[data-smart-phrase-count]');
      if (count) count.textContent = String(rows().length);
      if (form?.dataset.smartPhraseId === record.id) navigateTo();
    } catch (error) {
      showLibraryError(error instanceof Error ? error.message : 'Smart phrase action failed.');
    }
  });

  searchInput?.addEventListener('input', () => {
    const query = searchInput.value.trim().toLowerCase();
    rows().forEach((row) => {
      const record = rowRecord(row);
      const haystack = `${record.trigger || ''} ${record.expansion_text || ''} ${record.description || ''}`.toLowerCase();
      row.hidden = Boolean(query && !haystack.includes(query));
    });
  });

  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    showError();
    statusBox.textContent = '';
    if (!form.reportValidity()) return;
    saveButton.disabled = true;
    try {
      const phrase = await savePhrase(formPayload(form), form.dataset.smartPhraseId || '');
      initialPayload = JSON.stringify(formPayload(form));
      statusBox.textContent = 'Saved';
      navigateTo(phrase.id);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not save smart phrase.');
    } finally {
      saveButton.disabled = false;
    }
  });

  form?.querySelector('[data-smart-phrase-duplicate]')?.addEventListener('click', async () => {
    showError();
    try {
      await duplicate(formPayload(form));
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not duplicate smart phrase.');
    }
  });

  form?.querySelector('[data-smart-phrase-delete]')?.addEventListener('click', async () => {
    const id = form.dataset.smartPhraseId;
    if (!id || !window.confirm('Delete this smart phrase permanently?')) return;
    showError();
    try {
      await deletePhrase(id);
      navigateTo();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not delete smart phrase.');
    }
  });

  window.addEventListener('beforeunload', (event) => {
    if (allowNavigation || !form || JSON.stringify(formPayload(form)) === initialPayload) return;
    event.preventDefault();
    event.returnValue = '';
  });

  form?.elements.trigger?.addEventListener('input', (event) => {
    const start = event.target.selectionStart;
    event.target.value = event.target.value.toUpperCase();
    event.target.setSelectionRange?.(start, start);
  });
})();
