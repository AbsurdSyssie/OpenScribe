export function initLibraryHelp(library, { prefix, schemaUrl, errorMessage, buildPrompt }) {
  const dialog = document.querySelector(`[data-${prefix}-help-dialog]`);
  const open = library.querySelector(`[data-${prefix}-help-open]`);
  const copy = dialog?.querySelector(`[data-${prefix}-help-copy]`);
  const status = dialog?.querySelector(`[data-${prefix}-help-status]`);
  const fallback = dialog?.querySelector(`[data-${prefix}-help-fallback]`);
  const promptOutput = dialog?.querySelector(`[data-${prefix}-help-prompt]`);
  if (!dialog || !open || !copy) return;

  let prompt = '';
  const loadPrompt = async () => {
    if (prompt) return prompt;
    const response = await fetch(schemaUrl, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(errorMessage);
    prompt = buildPrompt(await response.json());
    return prompt;
  };

  open.addEventListener('click', () => {
    status.textContent = '';
    status.classList.remove('is-error');
    fallback.hidden = true;
    promptOutput.value = '';
    dialog.showModal();
  });
  copy.addEventListener('click', async () => {
    copy.disabled = true;
    status.textContent = 'Preparing instructions…';
    status.classList.remove('is-error');
    try {
      const text = await loadPrompt();
      try {
        await navigator.clipboard.writeText(text);
        fallback.hidden = true;
        status.textContent = 'Instructions copied. Now paste them into your AI assistant.';
      } catch (_) {
        promptOutput.value = text;
        fallback.hidden = false;
        promptOutput.focus();
        promptOutput.select();
        status.textContent = 'Automatic copying was blocked. Copy all the selected text below.';
      }
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('is-error');
    } finally {
      copy.disabled = false;
    }
  });
}
