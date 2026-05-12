## Cause

`renderLlmRequestPanel()` clears and recreates the panel each render:

```js
slot.innerHTML = '';
const wrapper = window.document.createElement('details');
```

That destroys the existing `<details>` element, so any open dropdown closes when the transcript page refreshes/re-renders.

## Fix

Preserve the open state for the currently selected document before clearing the slot, then restore it on the recreated `<details>` element.

Patch `app/static/js/transcribe/documents.js`:

```js
const renderLlmRequestPanel = (slot, document) => {
  if (!slot) return;

  const previousPanel = slot.querySelector('[data-llm-request-panel]');
  const previousDocumentId = previousPanel?.dataset?.generatedDocumentId || '';
  const shouldRestoreOpen = Boolean(
    previousPanel?.open &&
    document?.id &&
    previousDocumentId === document.id
  );

  slot.innerHTML = '';
  if (!document) return;

  const wrapper = window.document.createElement('details');
  wrapper.className = 'border border-stone bg-white p-3 mt-4 rounded-lg';
  wrapper.dataset.llmRequestPanel = 'true';
  wrapper.dataset.generatedDocumentId = document.id || '';

  if (shouldRestoreOpen) {
    wrapper.open = true;
  }

  const payload = document.llm_request_payload_json || null;
  const body = payload
    ? escapeHtml(JSON.stringify(payload, null, 2))
    : 'LLM request not available for this document.';

  wrapper.innerHTML = `
    <summary class="cursor-pointer text-sm font-medium text-ink">LLM request</summary>
    <pre class="mt-3 max-h-80 overflow-auto rounded bg-parchment p-3 text-xs whitespace-pre-wrap text-slate">${body}</pre>
  `;

  slot.appendChild(wrapper);
};
```

## Optional stronger version

If you want it to remember open/closed state even when switching between note versions and back, use a map inside `createDocumentNavigator()`:

```js
const llmRequestOpenByDocumentId = new Map();

const renderLlmRequestPanel = (slot, document) => {
  if (!slot) return;

  const previousPanel = slot.querySelector('[data-llm-request-panel]');
  const previousDocumentId = previousPanel?.dataset?.generatedDocumentId || '';

  if (previousDocumentId) {
    llmRequestOpenByDocumentId.set(previousDocumentId, Boolean(previousPanel?.open));
  }

  slot.innerHTML = '';
  if (!document) return;

  const wrapper = window.document.createElement('details');
  wrapper.className = 'border border-stone bg-white p-3 mt-4 rounded-lg';
  wrapper.dataset.llmRequestPanel = 'true';
  wrapper.dataset.generatedDocumentId = document.id || '';

  if (llmRequestOpenByDocumentId.get(document.id)) {
    wrapper.open = true;
  }

  wrapper.addEventListener('toggle', () => {
    if (document.id) {
      llmRequestOpenByDocumentId.set(document.id, wrapper.open);
    }
  });

  const payload = document.llm_request_payload_json || null;
  const body = payload
    ? escapeHtml(JSON.stringify(payload, null, 2))
    : 'LLM request not available for this document.';

  wrapper.innerHTML = `
    <summary class="cursor-pointer text-sm font-medium text-ink">LLM request</summary>
    <pre class="mt-3 max-h-80 overflow-auto rounded bg-parchment p-3 text-xs whitespace-pre-wrap text-slate">${body}</pre>
  `;

  slot.appendChild(wrapper);
};
```

## Acceptance check

After this change:

* Open **LLM request**.
* Trigger a page/workspace update.
* The dropdown should stay open for the same generated document.
* Switching to a different note/follow-up can either reset closed or restore its own last state, depending on which version above is used.
