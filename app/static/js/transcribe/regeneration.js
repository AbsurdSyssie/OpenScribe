const PRESETS = Object.freeze([
  { value: 'more_detail', label: 'More detail' },
  { value: 'less_detail', label: 'Less detail' },
]);

const escapeHtml = (value) => String(value || '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

export function createGeneratedNoteRegenerationController({
  root = null,
  queueRegeneration = async () => null,
  showFlash = () => {},
} = {}) {
  let openPopover = null;
  let openTrigger = null;
  let inFlight = false;
  let positionListenersBound = false;

  const positionPopover = () => {
    if (!openPopover || !openTrigger?.isConnected || !openPopover.isConnected) return;
    const rect = openTrigger.getBoundingClientRect?.();
    if (!rect) return;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const margin = 8;
    const width = openPopover.offsetWidth || Math.min(352, Math.max(0, viewportWidth - (margin * 2)));
    const height = openPopover.offsetHeight || 0;
    let top = rect.bottom + margin;
    if (height && top + height > viewportHeight - margin) {
      top = Math.max(margin, rect.top - height - margin);
    }
    const left = Math.min(
      Math.max(margin, rect.right - width),
      Math.max(margin, viewportWidth - width - margin),
    );
    openPopover.style.position = 'fixed';
    openPopover.style.top = `${Math.round(top)}px`;
    openPopover.style.left = `${Math.round(left)}px`;
    openPopover.style.right = 'auto';
  };

  const bindPositionListeners = () => {
    if (positionListenersBound) return;
    positionListenersBound = true;
    window.addEventListener?.('resize', positionPopover);
    window.addEventListener?.('scroll', positionPopover, true);
  };

  const unbindPositionListeners = () => {
    if (!positionListenersBound) return;
    positionListenersBound = false;
    window.removeEventListener?.('resize', positionPopover);
    window.removeEventListener?.('scroll', positionPopover, true);
  };

  const close = ({ restoreFocus = false } = {}) => {
    unbindPositionListeners();
    if (openPopover) openPopover.remove();
    if (openTrigger) {
      openTrigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) openTrigger.focus();
    }
    openPopover = null;
    openTrigger = null;
  };

  const setPreset = (popover, preset) => {
    popover.dataset.steeringPreset = preset;
    popover.querySelectorAll('[data-regeneration-preset]').forEach((button) => {
      button.setAttribute('aria-pressed', button.dataset.regenerationPreset === preset ? 'true' : 'false');
    });
  };

  const submit = async (popover, documentId, { immediate = false } = {}) => {
    if (inFlight || !documentId) return;
    const textarea = popover.querySelector('[data-regeneration-steering]');
    const steeringText = immediate ? '' : String(textarea?.value || '').trim();
    const steeringPreset = immediate ? null : (popover.dataset.steeringPreset || null);
    inFlight = true;
    popover.querySelectorAll('button, textarea').forEach((control) => { control.disabled = true; });
    const status = popover.querySelector('[data-regeneration-status]');
    if (status) status.textContent = 'Starting regeneration…';
    try {
      const queued = await queueRegeneration({
        generatedDocumentId: documentId,
        steeringText: steeringText || null,
        steeringPreset,
      });
      if (!queued) {
        if (status) status.textContent = 'Save the current note before regenerating.';
        popover.querySelectorAll('button, textarea').forEach((control) => { control.disabled = false; });
        return;
      }
      close();
    } catch (error) {
      showFlash(error instanceof Error ? error.message : 'Could not regenerate the note.', 'error');
      if (status) status.textContent = 'Regeneration could not be started.';
      popover.querySelectorAll('button, textarea').forEach((control) => { control.disabled = false; });
    } finally {
      inFlight = false;
    }
  };

  const open = (trigger) => {
    const item = trigger.closest?.('.document-switcher-item');
    const documentId = trigger.dataset.documentId || '';
    if (!item || !documentId) return;
    if (openTrigger === trigger) {
      close({ restoreFocus: false });
      return;
    }
    close();
    const popover = window.document.createElement('div');
    popover.className = 'note-regeneration-popover';
    popover.dataset.noteRegenerationPopover = 'true';
    popover.dataset.steeringPreset = '';
    const title = item.querySelector('.document-switcher-label')?.textContent?.trim() || 'note';
    const popoverId = `note-regeneration-${documentId}`;
    popover.id = popoverId;
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', `Regenerate ${title}`);
    popover.innerHTML = `
      <p class="note-regeneration-popover__title">Regenerate note</p>
      <p class="note-regeneration-popover__hint">Use the saved note as a starting point. Add a direction if you want to steer the new draft.</p>
      <div class="note-regeneration-popover__presets" role="group" aria-label="Regeneration detail">
        ${PRESETS.map(({ value, label }) => `<button type="button" class="note-regeneration-popover__preset" data-regeneration-preset="${value}" aria-pressed="false">${label}</button>`).join('')}
      </div>
      <label class="sr-only" for="${escapeHtml(popoverId)}-steering">Optional regeneration steering</label>
      <textarea id="${escapeHtml(popoverId)}-steering" class="note-regeneration-popover__textarea" data-regeneration-steering maxlength="4000" placeholder="Optional steering, for example: focus on the safety-netting advice"></textarea>
      <div class="note-regeneration-popover__footer">
        <span class="note-regeneration-popover__count" data-regeneration-count>0 / 4000</span>
        <span class="note-regeneration-popover__actions">
          <button type="button" class="btn-ghost-sm" data-regeneration-now>Regenerate now</button>
          <button type="button" class="btn-primary-sm" data-regeneration-submit>Regenerate</button>
        </span>
      </div>
      <p class="sr-only" data-regeneration-status role="status" aria-live="polite"></p>
    `;
    (window.document.body || item).appendChild(popover);
    openPopover = popover;
    openTrigger = trigger;
    trigger.setAttribute('aria-expanded', 'true');
    trigger.setAttribute('aria-controls', popoverId);
    bindPositionListeners();
    positionPopover();
    window.requestAnimationFrame?.(positionPopover);
    const textarea = popover.querySelector('[data-regeneration-steering]');
    const count = popover.querySelector('[data-regeneration-count]');
    textarea?.addEventListener('input', () => {
      if (count) count.textContent = `${textarea.value.length} / 4000`;
    });
    popover.querySelectorAll('[data-regeneration-preset]').forEach((button) => {
      button.addEventListener('click', () => setPreset(popover, button.dataset.regenerationPreset || ''));
    });
    popover.querySelector('[data-regeneration-now]')?.addEventListener('click', () => {
      void submit(popover, documentId, { immediate: true });
    });
    popover.querySelector('[data-regeneration-submit]')?.addEventListener('click', () => {
      void submit(popover, documentId);
    });
    textarea?.focus();
  };

  root?.addEventListener('click', (event) => {
    const trigger = event.target instanceof Element ? event.target.closest('[data-note-regenerate]') : null;
    if (!trigger || !root.contains(trigger)) return;
    event.preventDefault();
    event.stopPropagation();
    open(trigger);
  });

  window.document?.addEventListener('click', (event) => {
    if (openPopover && !openPopover.contains(event.target) && event.target !== openTrigger) close();
  });
  window.document?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && openPopover) {
      event.preventDefault();
      close({ restoreFocus: true });
    }
  });

  return {
    close,
    getState: () => ({ open: Boolean(openPopover), inFlight }),
  };
}
