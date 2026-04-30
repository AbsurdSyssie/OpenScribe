const STRUCTURED_CONTAINER_SELECTOR = '[data-generated-structured-section-rows]';
const FREEFORM_CONTAINER_SELECTOR = '[data-generated-freeform-rows]';
const STRUCTURED_ROW_SELECTOR = '[data-structured-statement-row]';
const FREEFORM_ROW_SELECTOR = '[data-freeform-note-row]';
const HANDLE_SELECTOR = '[data-statement-drag-handle]';

function dispatchCloseSmartPhrases() {
  window.dispatchEvent(new CustomEvent('openscribe:smart-phrases-close'));
}

function sectionRowsContainer(section) {
  return section?.querySelector?.(STRUCTURED_CONTAINER_SELECTOR) || null;
}

function structuredSections() {
  return [...document.querySelectorAll('[data-generated-structured-section]')];
}

function normalizeStructuredAfterMove(structuredEditor, row, oldContainer, newContainer) {
  if (row instanceof HTMLElement) structuredEditor?.syncMovedRowSectionMetadata?.(row);
  [oldContainer, newContainer].forEach((container) => {
    const section = container?.closest?.('[data-generated-structured-section]');
    if (section instanceof HTMLElement) {
      structuredEditor?.ensureSectionHasEditableRow?.(section);
      structuredEditor?.removePlaceholderRowIfFilled?.(section);
      section.querySelectorAll(STRUCTURED_ROW_SELECTOR).forEach((candidate) => {
        structuredEditor?.syncMovedRowSectionMetadata?.(candidate);
      });
    }
  });
  structuredEditor?.notifyNoteRowsChanged?.({ mode: 'structured' });
  if (row instanceof HTMLElement) structuredEditor?.focusLine?.(row, 'handle');
}

function normalizeFreeformAfterMove(structuredEditor, row) {
  structuredEditor?.ensureFreeformHasEditableRow?.();
  structuredEditor?.removePlaceholderRowIfFilled?.(document.querySelector(FREEFORM_CONTAINER_SELECTOR));
  structuredEditor?.notifyNoteRowsChanged?.({ mode: 'freeform' });
  if (row instanceof HTMLElement) structuredEditor?.focusLine?.(row, 'handle');
}

function moveStructuredByKeyboard(row, direction) {
  const currentContainer = row.closest(STRUCTURED_CONTAINER_SELECTOR);
  if (!(currentContainer instanceof HTMLElement)) return null;

  if (direction === 'up') {
    const previous = row.previousElementSibling;
    if (previous instanceof HTMLElement) {
      currentContainer.insertBefore(row, previous);
      return { oldContainer: currentContainer, newContainer: currentContainer };
    }
    const sections = structuredSections();
    const currentSection = row.closest('[data-generated-structured-section]');
    const previousContainer = sectionRowsContainer(sections[sections.indexOf(currentSection) - 1]);
    if (previousContainer) {
      previousContainer.appendChild(row);
      return { oldContainer: currentContainer, newContainer: previousContainer };
    }
  }

  if (direction === 'down') {
    const next = row.nextElementSibling;
    if (next instanceof HTMLElement) {
      currentContainer.insertBefore(next, row);
      return { oldContainer: currentContainer, newContainer: currentContainer };
    }
    const sections = structuredSections();
    const currentSection = row.closest('[data-generated-structured-section]');
    const nextContainer = sectionRowsContainer(sections[sections.indexOf(currentSection) + 1]);
    if (nextContainer) {
      nextContainer.insertBefore(row, nextContainer.firstElementChild);
      return { oldContainer: currentContainer, newContainer: nextContainer };
    }
  }

  if (direction === 'left' || direction === 'right') {
    const sections = structuredSections();
    const currentSection = row.closest('[data-generated-structured-section]');
    const targetContainer = sectionRowsContainer(sections[sections.indexOf(currentSection) + (direction === 'left' ? -1 : 1)]);
    if (targetContainer) {
      targetContainer.appendChild(row);
      return { oldContainer: currentContainer, newContainer: targetContainer };
    }
  }
  return null;
}

function moveFreeformByKeyboard(row, direction) {
  const container = row.closest(FREEFORM_CONTAINER_SELECTOR);
  if (!(container instanceof HTMLElement)) return false;
  if (direction === 'up') {
    const previous = row.previousElementSibling;
    if (previous instanceof HTMLElement) {
      container.insertBefore(row, previous);
      return true;
    }
  }
  if (direction === 'down') {
    const next = row.nextElementSibling;
    if (next instanceof HTMLElement) {
      container.insertBefore(next, row);
      return true;
    }
  }
  return false;
}

function attachSortableToContainer(container, options) {
  if (!(container instanceof HTMLElement)) return;
  if (container.dataset.sortableAttached === 'true') return;
  if (!window.Sortable) return;
  window.Sortable.create(container, {
    animation: 130,
    handle: HANDLE_SELECTOR,
    draggable: options.draggable,
    group: options.group,
    ghostClass: 'statement-row--drag-ghost',
    chosenClass: 'statement-row--drag-chosen',
    dragClass: 'statement-row--dragging',
    forceFallback: false,
    onStart: () => {
      dispatchCloseSmartPhrases();
      document.body.classList.add('is-note-dragging');
    },
    onEnd: (event) => {
      document.body.classList.remove('is-note-dragging');
      options.onEnd(event);
    },
  });
  container.dataset.sortableAttached = 'true';
}

export function attachNoteReordering({ structuredEditor, showFlash }) {
  let sortableWarningShown = false;
  const syncSortables = () => {
    if (!window.Sortable) {
      if (!sortableWarningShown) {
        sortableWarningShown = true;
        showFlash?.('Line dragging is unavailable because SortableJS did not load.', 'error');
      }
      return;
    }
    document.querySelectorAll(STRUCTURED_CONTAINER_SELECTOR).forEach((container) => {
      attachSortableToContainer(container, {
        draggable: STRUCTURED_ROW_SELECTOR,
        group: { name: 'openscribe-structured-lines', pull: true, put: true },
        onEnd: (event) => normalizeStructuredAfterMove(structuredEditor, event.item, event.from, event.to),
      });
    });
    document.querySelectorAll(FREEFORM_CONTAINER_SELECTOR).forEach((container) => {
      attachSortableToContainer(container, {
        draggable: FREEFORM_ROW_SELECTOR,
        group: { name: 'openscribe-freeform-lines', pull: false, put: false },
        onEnd: (event) => normalizeFreeformAfterMove(structuredEditor, event.item),
      });
    });
  };

  document.addEventListener('keydown', (event) => {
    if (!event.altKey || !['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const row = event.target.closest?.(`${STRUCTURED_ROW_SELECTOR}, ${FREEFORM_ROW_SELECTOR}`);
    if (!(row instanceof HTMLElement)) return;
    event.preventDefault();
    dispatchCloseSmartPhrases();

    if (row.matches(STRUCTURED_ROW_SELECTOR)) {
      const moved = moveStructuredByKeyboard(row, {
        ArrowUp: 'up',
        ArrowDown: 'down',
        ArrowLeft: 'left',
        ArrowRight: 'right',
      }[event.key]);
      if (moved) normalizeStructuredAfterMove(structuredEditor, row, moved.oldContainer, moved.newContainer);
      return;
    }

    if (row.matches(FREEFORM_ROW_SELECTOR) && ['ArrowUp', 'ArrowDown'].includes(event.key)) {
      const moved = moveFreeformByKeyboard(row, event.key === 'ArrowUp' ? 'up' : 'down');
      if (moved) normalizeFreeformAfterMove(structuredEditor, row);
    }
  });

  syncSortables();
  const observer = new MutationObserver(syncSortables);
  observer.observe(document.body, { childList: true, subtree: true });
  return { refresh: syncSortables, disconnect: () => observer.disconnect() };
}
