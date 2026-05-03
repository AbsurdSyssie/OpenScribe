import { csrfFetch } from '../csrf.js';

(function () {
  const root = document.querySelector('[data-smart-phrase-settings]');
  const drawer = document.querySelector('[data-smart-phrase-drawer]');
  const form = document.querySelector('[data-smart-phrase-form]');
  if (!root || !drawer || !form) return;

  const list = document.querySelector('[data-smart-phrase-list]');
  const searchInput = document.querySelector('[data-smart-phrase-search]');
  const idInput = document.querySelector('[data-smart-phrase-id-input]');
  const triggerInput = document.querySelector('[data-smart-phrase-trigger-input]');
  const expansionInput = document.querySelector('[data-smart-phrase-expansion-input]');
  const descriptionInput = document.querySelector('[data-smart-phrase-description-input]');
  const title = document.querySelector('[data-smart-phrase-drawer-title]');
  const deleteButton = document.querySelector('[data-smart-phrase-delete]');
  const duplicateButton = document.querySelector('[data-smart-phrase-duplicate]');
  let dirty = false;
  let initialState = null;

  function phraseRows() {
    return [...document.querySelectorAll('[data-smart-phrase-row]')];
  }

  function rowPayload(row) {
    return {
      id: row.dataset.smartPhraseId || '',
      trigger: row.dataset.smartPhraseTrigger || '',
      expansion_text: row.dataset.smartPhraseExpansion || '',
      description: row.dataset.smartPhraseDescription || '',
    };
  }

  function currentPayload() {
    return {
      trigger: triggerInput.value.trim(),
      expansion_text: expansionInput.value.trim(),
      description: descriptionInput.value.trim() || null,
    };
  }

  function markClean() {
    initialState = JSON.stringify(currentPayload());
    dirty = false;
  }

  function openDrawer(payload) {
    idInput.value = payload?.id || '';
    triggerInput.value = payload?.trigger || '';
    expansionInput.value = payload?.expansion_text || '';
    descriptionInput.value = payload?.description || '';
    title.textContent = payload?.id ? payload.trigger : 'New phrase';
    deleteButton.hidden = !payload?.id;
    duplicateButton.hidden = !payload?.id;
    drawer.hidden = false;
    markClean();
    triggerInput.focus();
  }

  function closeDrawer() {
    if (dirty && !window.confirm('Discard unsaved smart phrase changes?')) return;
    drawer.hidden = true;
  }

  function updateDirty() {
    dirty = JSON.stringify(currentPayload()) !== initialState;
  }

  function nextCopyTrigger(sourceTrigger) {
    const existing = new Set(phraseRows().map((row) => (row.dataset.smartPhraseTrigger || '').toUpperCase()));
    let candidate = `${sourceTrigger}_COPY`;
    let index = 2;
    while (existing.has(candidate)) {
      candidate = `${sourceTrigger}_COPY_${index}`;
      index += 1;
    }
    return candidate;
  }

  async function parseError(response, fallback) {
    try {
      const body = await response.json();
      return body?.error?.message || fallback;
    } catch (_) {
      return fallback;
    }
  }

  async function savePhrase(payload, id) {
    const response = await csrfFetch(id ? `/api/v1/smart-phrases/personal/${id}` : '/api/v1/smart-phrases/personal', {
      method: id ? 'PATCH' : 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await parseError(response, 'Could not save smart phrase.'));
    return response.json();
  }

  function normalizePreview(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 90);
  }

  function upsertRow(phrase) {
    document.querySelector('[data-smart-phrase-empty]')?.remove();
    let row = document.querySelector(`[data-smart-phrase-row][data-smart-phrase-id="${phrase.id}"]`);
    if (!row) {
      row = document.createElement('button');
      row.type = 'button';
      row.className = 'asset-row smart-phrase-row';
      row.setAttribute('data-smart-phrase-row', '');
      row.innerHTML = '<span class="asset-row__info"><span class="asset-row__title"></span><span class="asset-row__status"></span></span>';
      list.appendChild(row);
    }
    row.dataset.smartPhraseId = phrase.id;
    row.dataset.smartPhraseTrigger = phrase.trigger;
    row.dataset.smartPhraseExpansion = phrase.expansion_text;
    row.dataset.smartPhraseDescription = phrase.description || '';
    row.querySelector('.asset-row__title').textContent = phrase.trigger;
    row.querySelector('.asset-row__status').textContent = normalizePreview(phrase.expansion_text);
    phraseRows()
      .sort((a, b) => (a.dataset.smartPhraseTrigger || '').localeCompare(b.dataset.smartPhraseTrigger || ''))
      .forEach((candidate) => list.appendChild(candidate));
  }

  function removeRow(id) {
    document.querySelector(`[data-smart-phrase-row][data-smart-phrase-id="${id}"]`)?.remove();
  }

  document.addEventListener('click', (event) => {
    const row = event.target.closest('[data-smart-phrase-row]');
    if (row) {
      openDrawer(rowPayload(row));
      return;
    }
    if (event.target.closest('[data-smart-phrase-new]')) {
      openDrawer(null);
      return;
    }
    if (event.target.closest('[data-smart-phrase-close]')) closeDrawer();
  });

  form.addEventListener('input', updateDirty);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const phrase = await savePhrase(currentPayload(), idInput.value);
      upsertRow(phrase);
      openDrawer(phrase);
      window.showToast?.('Smart phrase saved', 'success');
    } catch (error) {
      window.showToast?.(error instanceof Error ? error.message : 'Could not save smart phrase.', 'error');
    }
  });

  deleteButton.addEventListener('click', async () => {
    const id = idInput.value;
    if (!id || !window.confirm('Delete this smart phrase permanently?')) return;
    const response = await csrfFetch(`/api/v1/smart-phrases/personal/${id}`, { method: 'DELETE', credentials: 'include' });
    if (!response.ok) {
      window.showToast?.(await parseError(response, 'Could not delete smart phrase.'), 'error');
      return;
    }
    removeRow(id);
    dirty = false;
    drawer.hidden = true;
    window.showToast?.('Smart phrase deleted', 'success');
  });

  duplicateButton.addEventListener('click', async () => {
    const payload = currentPayload();
    payload.trigger = nextCopyTrigger(payload.trigger.toUpperCase());
    try {
      const phrase = await savePhrase(payload, null);
      upsertRow(phrase);
      openDrawer(phrase);
      window.showToast?.('Smart phrase duplicated', 'success');
    } catch (error) {
      window.showToast?.(error instanceof Error ? error.message : 'Could not duplicate smart phrase.', 'error');
    }
  });

  searchInput?.addEventListener('input', () => {
    const query = searchInput.value.trim().toLowerCase();
    phraseRows().forEach((row) => {
      const haystack = `${row.dataset.smartPhraseTrigger || ''} ${row.dataset.smartPhraseExpansion || ''}`.toLowerCase();
      row.hidden = Boolean(query && !haystack.includes(query));
    });
  });
})();
