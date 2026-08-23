import { generationLoadingHtml } from './documents.js?v=20260812-long-note-editor';

export function createStructuredEditor({
  dom,
  structuredSectionDefinitions,
  getTranscriptId,
  getDraftText,
  getTranscriptWaitingForText,
  syncGenerationAvailability,
  onNoteEditorChanged,
}) {
  const escapeHtml = (value) => {
    if (value == null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };
  let generatedStructuredDraft = null;
  let generatedFreeformDraft = null;
  let currentRenderedDocument = null;
  let currentRenderRequiresCopyReview = false;
  const rowSelectionState = new Map();
  let copyReviewDocumentId = null;
  let copyReviewObserver = null;
  let reviewedStructuredSectionKeys = new Set();
  let structuredSectionReviewFingerprints = new Map();
  let freeformReviewFingerprint = null;
  let freeformNoteReviewed = false;
  let copyReviewObservationReady = false;
  let copyReviewViewportListener = null;
  let copyReviewResizeListener = null;
  let copyReviewViewportCheckScheduled = false;
  let copyReviewRefreshScheduled = false;
  let statementAutosizeObserver = null;
  const pendingStatementAutosizeTextareas = new Set();
  let copyReviewLayoutCheck = null;

  const noteCopyStatusDefault = '';
  const EAGER_AUTOSIZE_EDITOR_LIMIT = 80;

  const autosizeStatementEditor = (textarea) => {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.max(textarea.scrollHeight, 22)}px`;
    const wasPending = pendingStatementAutosizeTextareas.delete(textarea);
    if (wasPending) {
      statementAutosizeObserver?.unobserve(textarea);
      copyReviewLayoutCheck?.();
    }
  };

  const disconnectStatementAutosizeObserver = () => {
    statementAutosizeObserver?.disconnect();
    statementAutosizeObserver = null;
    pendingStatementAutosizeTextareas.clear();
  };

  const autosizeStatementEditorsIn = (container) => {
    if (!(container instanceof HTMLElement)) return;
    const textareas = [...container.querySelectorAll('textarea')];
    if (!textareas.length) return;

    if (!('IntersectionObserver' in window)) {
      textareas.forEach((textarea) => autosizeStatementEditor(textarea));
      return;
    }

    const eagerTextareas = textareas.slice(0, EAGER_AUTOSIZE_EDITOR_LIMIT);
    const deferredTextareas = textareas.slice(EAGER_AUTOSIZE_EDITOR_LIMIT);
    eagerTextareas.forEach((textarea) => autosizeStatementEditor(textarea));
    if (!deferredTextareas.length) return;

    if (!statementAutosizeObserver) {
      statementAutosizeObserver = new window.IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting || !(entry.target instanceof HTMLTextAreaElement)) return;
          autosizeStatementEditor(entry.target);
        });
      }, { rootMargin: '600px 0px' });
    }
    deferredTextareas.forEach((textarea) => {
      pendingStatementAutosizeTextareas.add(textarea);
      statementAutosizeObserver.observe(textarea);
    });
  };

  const focusStatementEditor = (textarea) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    autosizeStatementEditor(textarea);
    textarea.focus();
    const length = textarea.value.length;
    textarea.setSelectionRange(length, length);
  };

  const syncStatementRowVisualState = (row) => {
    if (!row) return;
    const checkbox = row.querySelector('input[type="checkbox"]');
    const input = row.querySelector('[data-structured-line-input], [data-freeform-note-input]');
    const dragHandle = row.querySelector('[data-statement-drag-handle]');
    const isBlank = String(input?.value || '').trim().length === 0;
    row.classList.toggle('is-unchecked', Boolean(checkbox && !checkbox.checked));
    row.classList.toggle('unchecked', Boolean(checkbox && !checkbox.checked));
    row.classList.toggle('is-blank-line', isBlank);
    if (dragHandle instanceof HTMLButtonElement) {
      dragHandle.disabled = isBlank;
      dragHandle.setAttribute('aria-disabled', isBlank ? 'true' : 'false');
      dragHandle.title = isBlank ? 'Add text before reordering line' : 'Drag to reorder line';
    }
  };

  const currentDraftSelectionKey = (mode = selectedOutputTemplateMode()) => {
    const generatedDocumentId = activeGeneratedDocumentId();
    if (generatedDocumentId) {
      return `${mode}:document:${generatedDocumentId}`;
    }
    return `${mode}:draft:${getTranscriptId() || 'none'}:${dom.generateOutputTemplateSelect?.value || 'none'}`;
  };

  const rowSelectionCacheKey = ({ mode, sectionKey = '', lineIndex = 0 }) => `${currentDraftSelectionKey(mode)}:${sectionKey}:${lineIndex}`;

  const rememberRowSelectionState = ({ mode, sectionKey = '', lineIndex = 0, checked = true }) => {
    rowSelectionState.set(rowSelectionCacheKey({ mode, sectionKey, lineIndex }), Boolean(checked));
  };

  const readRememberedRowSelectionState = ({ mode, sectionKey = '', lineIndex = 0, fallback = true }) => {
    const cacheKey = rowSelectionCacheKey({ mode, sectionKey, lineIndex });
    return rowSelectionState.has(cacheKey) ? rowSelectionState.get(cacheKey) : fallback;
  };

  const captureEditorFocusState = () => {
    const activeElement = document.activeElement;
    if (!(activeElement instanceof HTMLTextAreaElement)) {
      return null;
    }
    if (activeElement.hasAttribute('data-structured-line-input')) {
      const row = activeElement.closest('[data-structured-statement-row]');
      const section = activeElement.closest('[data-generated-structured-section]');
      if (!(row instanceof HTMLElement) || !(section instanceof HTMLElement)) {
        return null;
      }
      const rows = [...section.querySelectorAll('[data-structured-statement-row]')];
      return {
        mode: 'structured',
        sectionKey: section.dataset.sectionKey || '',
        lineIndex: Math.max(0, rows.indexOf(row)),
        selectionStart: activeElement.selectionStart ?? activeElement.value.length,
        selectionEnd: activeElement.selectionEnd ?? activeElement.value.length,
      };
    }
    if (activeElement.hasAttribute('data-freeform-note-input')) {
      const row = activeElement.closest('[data-freeform-note-row]');
      if (!(row instanceof HTMLElement) || !dom.generatedFreeformRows) {
        return null;
      }
      const rows = [...dom.generatedFreeformRows.querySelectorAll('[data-freeform-note-row]')];
      return {
        mode: 'freeform',
        sectionKey: '',
        lineIndex: Math.max(0, rows.indexOf(row)),
        selectionStart: activeElement.selectionStart ?? activeElement.value.length,
        selectionEnd: activeElement.selectionEnd ?? activeElement.value.length,
      };
    }
    return null;
  };

  const restoreEditorFocusState = (focusState) => {
    if (!focusState) return;
    const selector = focusState.mode === 'structured'
      ? `[data-generated-structured-section][data-section-key="${window.CSS?.escape ? window.CSS.escape(focusState.sectionKey || '') : (focusState.sectionKey || '')}"] [data-structured-statement-row] [data-structured-line-input]`
      : '[data-generated-freeform-rows] [data-freeform-note-row] [data-freeform-note-input]';
    const inputs = [...document.querySelectorAll(selector)];
    const target = inputs[focusState.lineIndex];
    if (!(target instanceof HTMLTextAreaElement)) {
      return;
    }
    window.requestAnimationFrame(() => {
      autosizeStatementEditor(target);
      target.focus();
      const max = target.value.length;
      target.setSelectionRange(Math.min(focusState.selectionStart, max), Math.min(focusState.selectionEnd, max));
    });
  };

  const selectedOutputTemplateMode = () => {
    const selectedOption = dom.generateOutputTemplateSelect?.selectedOptions?.[0];
    return selectedOption?.dataset?.templateMode || 'freeform';
  };

  const activeGeneratedDocumentMode = () => dom.latestGeneratedOutput?.dataset?.latestGeneratedMode || '';
  const activeGeneratedDocumentId = () => dom.latestGeneratedOutput?.dataset?.latestGeneratedId || '';
  const activeRenderedGeneratedDocumentId = () => (
    activeGeneratedDocumentId()
    || generatedStructuredDraft?.documentId
    || generatedFreeformDraft?.documentId
    || ''
  );
  const generatedCopyReviewRequired = () => currentRenderRequiresCopyReview && Boolean(activeRenderedGeneratedDocumentId());

  const setCopyReviewStatus = (message = '') => {
    if (dom.structuredCopyStatus) {
      dom.structuredCopyStatus.textContent = message || noteCopyStatusDefault;
    }
  };

  const setStructuredSectionCopyReviewState = (section, reviewed) => {
    if (!(section instanceof HTMLElement)) return;
    const isReviewed = Boolean(reviewed);
    section.dataset.copyReviewViewed = isReviewed ? 'true' : 'false';
    const button = section.querySelector('[data-copy-structured-section]');
    if (button instanceof HTMLButtonElement) {
      const blocked = generatedCopyReviewRequired() && !isReviewed;
      button.disabled = false;
      button.dataset.copyReviewBlocked = blocked ? 'true' : 'false';
      button.title = blocked ? 'Scroll to the bottom of this section before copying it' : 'Copy section';
    }
  };

  const setFreeformCopyReviewState = (reviewed) => {
    freeformNoteReviewed = Boolean(reviewed);
    if (dom.generatedFreeformPanel) {
      dom.generatedFreeformPanel.dataset.copyReviewViewed = freeformNoteReviewed ? 'true' : 'false';
    }
  };

  const visibleBottomReached = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    if (element.closest('[hidden]') || element.getClientRects().length === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return false;
    }
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    return rect.bottom <= viewportHeight + 2 && rect.top < viewportHeight;
  };

  const copyReviewTargetHasPendingLayout = (target) => {
    if (!(target instanceof HTMLElement)) return false;
    let pending = false;
    pendingStatementAutosizeTextareas.forEach((textarea) => {
      if (!textarea.isConnected) {
        pendingStatementAutosizeTextareas.delete(textarea);
        return;
      }
      if (target.contains(textarea)) pending = true;
    });
    return pending;
  };

  const normalizedCopyReviewLines = (container, selector) => {
    if (!(container instanceof HTMLElement)) return [];
    return [...container.querySelectorAll(selector)]
      .map((input) => String(input?.value || '').trim())
      .filter((value) => value.length > 0);
  };

  const structuredSectionCopyReviewFingerprint = (section) => [
    section?.dataset?.sectionKey || '',
    ...normalizedCopyReviewLines(section, '[data-structured-line-input]'),
  ].join('\u001f');

  const freeformCopyReviewFingerprint = () => normalizedCopyReviewLines(
    dom.generatedFreeformPanel,
    '[data-freeform-note-input]'
  ).join('\u001f');

  const syncCopyReviewContentFingerprints = () => {
    if (!generatedCopyReviewRequired()) {
      structuredSectionReviewFingerprints = new Map();
      freeformReviewFingerprint = null;
      return;
    }

    let reviewInvalidated = false;
    const nextStructuredFingerprints = new Map();
    document.querySelectorAll('[data-generated-structured-section]').forEach((section) => {
      const sectionKey = section.dataset.sectionKey || '';
      if (!sectionKey) return;
      const fingerprint = structuredSectionCopyReviewFingerprint(section);
      const previousFingerprint = structuredSectionReviewFingerprints.get(sectionKey);
      if (previousFingerprint !== undefined && previousFingerprint !== fingerprint) {
        reviewedStructuredSectionKeys.delete(sectionKey);
        reviewInvalidated = true;
      }
      nextStructuredFingerprints.set(sectionKey, fingerprint);
    });
    reviewedStructuredSectionKeys.forEach((sectionKey) => {
      if (!nextStructuredFingerprints.has(sectionKey)) {
        reviewedStructuredSectionKeys.delete(sectionKey);
      }
    });
    structuredSectionReviewFingerprints = nextStructuredFingerprints;

    const freeformVisible = dom.generatedFreeformPanel instanceof HTMLElement && !dom.generatedFreeformPanel.hidden;
    if (freeformVisible) {
      const nextFreeformFingerprint = freeformCopyReviewFingerprint();
      if (freeformReviewFingerprint !== null && freeformReviewFingerprint !== nextFreeformFingerprint) {
        freeformNoteReviewed = false;
        reviewInvalidated = true;
      }
      freeformReviewFingerprint = nextFreeformFingerprint;
    } else {
      freeformReviewFingerprint = null;
    }

    if (reviewInvalidated) {
      setCopyReviewStatus('Generated note changed. Scroll to the bottom before copying.');
    }
    syncCopyReviewUi();
  };

  const syncCopyReviewUi = () => {
    if (!generatedCopyReviewRequired()) {
      document.querySelectorAll('[data-generated-structured-section]').forEach((section) => {
        setStructuredSectionCopyReviewState(section, true);
      });
      if (dom.copyStructuredLinesButton) {
        dom.copyStructuredLinesButton.disabled = false;
        dom.copyStructuredLinesButton.title = '';
      }
      setFreeformCopyReviewState(true);
      setCopyReviewStatus();
      return;
    }

    document.querySelectorAll('[data-generated-structured-section]').forEach((section) => {
      setStructuredSectionCopyReviewState(section, reviewedStructuredSectionKeys.has(section.dataset.sectionKey || ''));
    });
    if (dom.copyStructuredLinesButton instanceof HTMLButtonElement) {
      const mode = activeGeneratedDocumentMode();
      const blocked = mode === 'freeform' && !freeformNoteReviewed;
      dom.copyStructuredLinesButton.disabled = false;
      dom.copyStructuredLinesButton.dataset.copyReviewBlocked = blocked ? 'true' : 'false';
      dom.copyStructuredLinesButton.title = blocked
        ? 'Scroll to the bottom of the generated note before copying'
        : '';
    }
  };

  const invalidateCopyReviewForEdit = ({ mode, sectionKey = '' } = {}) => {
    if (!generatedCopyReviewRequired()) return;
    if (mode === 'structured') {
      if (sectionKey) {
        reviewedStructuredSectionKeys.delete(sectionKey);
        structuredSectionReviewFingerprints.delete(sectionKey);
      }
    } else if (mode === 'freeform') {
      freeformNoteReviewed = false;
      freeformReviewFingerprint = null;
    }
    setCopyReviewStatus('Generated note changed. Scroll to the bottom before copying.');
    syncCopyReviewUi();
  };

  const resetCopyReviewStateForDocument = () => {
    const documentId = activeGeneratedDocumentId();
    if (copyReviewDocumentId === documentId) return;
    copyReviewDocumentId = documentId;
    reviewedStructuredSectionKeys = new Set();
    structuredSectionReviewFingerprints = new Map();
    freeformReviewFingerprint = null;
    freeformNoteReviewed = false;
    if (!documentId) {
      setFreeformCopyReviewState(true);
    }
  };

  const markCopyReviewTargetViewed = (target) => {
    if (!(target instanceof HTMLElement)) return;
    if (!copyReviewObservationReady) return;
    if (copyReviewTargetHasPendingLayout(target)) return;
    const section = target.closest('[data-generated-structured-section]');
    if (section instanceof HTMLElement) {
      const sections = [...document.querySelectorAll('[data-generated-structured-section]')];
      const viewedThroughIndex = sections.indexOf(section);
      const viewedSections = viewedThroughIndex >= 0 ? sections.slice(0, viewedThroughIndex + 1) : [];
      if (viewedSections.some((candidate) => copyReviewTargetHasPendingLayout(candidate))) return;
      sections.forEach((candidate, index) => {
        if (viewedThroughIndex >= 0 && index > viewedThroughIndex) return;
        const sectionKey = candidate.dataset.sectionKey || '';
        if (sectionKey) {
          reviewedStructuredSectionKeys.add(sectionKey);
        }
        setStructuredSectionCopyReviewState(candidate, true);
      });
      setCopyReviewStatus();
      syncCopyReviewUi();
      return;
    }
    if (target.hasAttribute('data-generated-freeform-panel')) {
      setFreeformCopyReviewState(true);
      setCopyReviewStatus();
      syncCopyReviewUi();
    }
  };

  const observeCopyReviewTargets = () => {
    copyReviewObserver?.disconnect();
    copyReviewObserver = null;
    if (copyReviewViewportListener) {
      document.removeEventListener('scroll', copyReviewViewportListener, true);
      copyReviewViewportListener = null;
    }
    if (copyReviewResizeListener) {
      window.removeEventListener('resize', copyReviewResizeListener);
      copyReviewResizeListener = null;
    }
    copyReviewViewportCheckScheduled = false;
    copyReviewLayoutCheck = null;
    if (!generatedCopyReviewRequired()) {
      syncCopyReviewUi();
      return;
    }
    syncCopyReviewContentFingerprints();
    const targets = [
      ...document.querySelectorAll('[data-generated-structured-section]'),
      ...document.querySelectorAll('[data-generated-freeform-panel]:not([hidden])'),
    ];
    if (!targets.length) {
      syncCopyReviewUi();
      return;
    }
    copyReviewObservationReady = true;
    const checkTargets = () => {
      targets.forEach((target) => {
        if (visibleBottomReached(target)) {
          markCopyReviewTargetViewed(target);
        }
      });
      syncCopyReviewUi();
    };
    const scheduleViewportCheck = () => {
      if (copyReviewViewportCheckScheduled) return;
      copyReviewViewportCheckScheduled = true;
      window.requestAnimationFrame(() => {
        copyReviewViewportCheckScheduled = false;
        checkTargets();
      });
    };
    copyReviewLayoutCheck = scheduleViewportCheck;
    if ('IntersectionObserver' in window) {
      copyReviewObserver = new window.IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && visibleBottomReached(entry.target)) {
            markCopyReviewTargetViewed(entry.target);
          }
        });
      }, { threshold: 0 });
      targets.forEach((target) => copyReviewObserver.observe(target));
    }
    copyReviewViewportListener = scheduleViewportCheck;
    copyReviewResizeListener = scheduleViewportCheck;
    document.addEventListener('scroll', copyReviewViewportListener, true);
    window.addEventListener('resize', copyReviewResizeListener);
    window.requestAnimationFrame(() => {
      checkTargets();
    });
  };

  const scheduleCopyReviewRefresh = () => {
    if (copyReviewRefreshScheduled) return;
    copyReviewRefreshScheduled = true;
    window.requestAnimationFrame(() => {
      copyReviewRefreshScheduled = false;
      syncCopyReviewContentFingerprints();
      observeCopyReviewTargets();
    });
  };

  const noteCopyReviewBlocker = ({ lines = [], section = null } = {}) => {
    if (!generatedCopyReviewRequired()) {
      return null;
    }
    if (section instanceof HTMLElement) {
      if (section.dataset.copyReviewViewed !== 'true') {
        return `Scroll to the bottom of ${section.dataset.sectionLabel || 'this section'} before copying it.`;
      }
      return null;
    }
    const mode = activeGeneratedDocumentMode();
    if (mode === 'freeform' && !freeformNoteReviewed) {
      return 'Scroll to the bottom of the generated note before copying it.';
    }
    if (mode === 'structured') {
      const blockedLabels = new Set();
      lines.forEach((line) => {
        const sectionKey = line.sectionKey || '';
        if (sectionKey && !reviewedStructuredSectionKeys.has(sectionKey)) {
          blockedLabels.add(line.label || 'section');
        }
      });
      if (blockedLabels.size > 0) {
        return `Scroll to the bottom of ${[...blockedLabels].join(', ')} before copying selected lines.`;
      }
    }
    return null;
  };

  const structuredSectionDefinitionsFromDocument = (generatedDocument) => {
    const snapshot = generatedDocument?.structured_section_definitions_json;
    if (!snapshot || !Array.isArray(snapshot.sections)) {
      return [];
    }
    const definitions = snapshot.sections
      .filter((section) => section && typeof section === 'object' && typeof section.section_key === 'string')
      .sort((a, b) => {
        const left = Number.isInteger(a.section_order) ? a.section_order : 999;
        const right = Number.isInteger(b.section_order) ? b.section_order : 999;
        return left - right;
      })
      .map((section) => ({
        key: section.section_key,
        label: typeof section.section_label === 'string' && section.section_label.trim()
          ? section.section_label
          : section.section_key.replaceAll('_', ' '),
      }));
    return definitions;
  };

  const selectedStructuredSectionDefinitions = () => {
    const selectedOption = dom.generateOutputTemplateSelect?.selectedOptions?.[0];
    if (selectedOption?.dataset?.templateSections) {
      try {
        const parsed = JSON.parse(selectedOption.dataset.templateSections);
        if (Array.isArray(parsed) && parsed.length > 0) {
          const definitions = parsed
            .filter((section) => section && typeof section === 'object' && typeof section.section_key === 'string')
            .sort((a, b) => {
              const left = Number.isInteger(a.section_order) ? a.section_order : 999;
              const right = Number.isInteger(b.section_order) ? b.section_order : 999;
              return left - right;
            })
            .map((section) => ({
              key: section.section_key,
              label: typeof section.section_label === 'string' && section.section_label.trim()
                ? section.section_label
                : section.section_key.replaceAll('_', ' '),
            }));
          if (definitions.length > 0) {
            return definitions;
          }
        }
      } catch (_) {}
    }
    return structuredSectionDefinitions;
  };

  const collectStructuredContext = () => {
    const context = {};
    const generatedSections = [...document.querySelectorAll('[data-generated-structured-section]')];
    if (generatedSections.length > 0) {
      generatedSections.forEach((section) => {
        const sectionKey = section.dataset.sectionKey || '';
        if (!sectionKey) return;
        const lines = [...section.querySelectorAll('[data-structured-statement-row]')]
          .map((row) => {
            const checkbox = row.querySelector('[data-structured-line-checkbox]');
            const input = row.querySelector('[data-structured-line-input]');
            if (!checkbox?.checked || !input) return '';
            return input.value.trim();
          })
          .filter((value) => value.length > 0);
        if (lines.length > 0) {
          context[sectionKey] = lines;
        }
      });
      return context;
    }
    document.querySelectorAll('[data-legacy-note-workspace] .section-block').forEach((section) => {
      const sectionKey = section.dataset.sectionKey || '';
      if (!sectionKey) return;
      const lines = [...section.querySelectorAll('.statement')]
        .map((row) => {
          const checkbox = row.querySelector('[data-statement-checkbox]');
          const input = row.querySelector('[data-statement-input]');
          if (!checkbox?.checked || !input) return '';
          return input.value.trim();
        })
        .concat(
          [...section.querySelectorAll('.statement')]
            .filter((row) => !row.querySelector('[data-statement-input]'))
            .map((row) => {
              const checkbox = row.querySelector('[data-statement-checkbox]');
              if (!checkbox?.checked) return '';
              return String(row.dataset.statementText || '').trim();
            }),
        )
        .filter((value) => value.length > 0);
      if (lines.length > 0) {
        context[sectionKey] = lines;
      }
    });
    return context;
  };

  const syncStructuredEditorAvailability = () => {
    const disabled = !getTranscriptId();
    document.querySelectorAll('[data-structured-line-checkbox]').forEach((input) => {
      input.disabled = disabled;
    });
    document.querySelectorAll('[data-structured-line-input]').forEach((input) => {
      input.disabled = disabled;
    });
    document.querySelectorAll('[data-freeform-note-checkbox]').forEach((input) => {
      input.disabled = disabled;
    });
    document.querySelectorAll('[data-freeform-note-input]').forEach((input) => {
      input.disabled = disabled;
    });
  };

  const handleStructuredContextChanged = () => {
    onNoteEditorChanged?.();
    syncGenerationAvailability(getDraftText() || '');
  };

  const getAdjacentStatementRow = (row, direction) => {
    const direct = direction === 'previous' ? row.previousElementSibling : row.nextElementSibling;
    if (direct instanceof HTMLElement) {
      return direct;
    }
    const currentSection = row.closest('[data-generated-structured-section]');
    if (!(currentSection instanceof HTMLElement)) return null;
    let siblingSection = direction === 'previous' ? currentSection.previousElementSibling : currentSection.nextElementSibling;
    while (siblingSection) {
      if (siblingSection instanceof HTMLElement && siblingSection.hasAttribute('data-generated-structured-section')) {
        const rows = [...siblingSection.querySelectorAll('[data-structured-statement-row]')];
        if (rows.length > 0) {
          return direction === 'previous' ? rows[rows.length - 1] : rows[0];
        }
      }
      siblingSection = direction === 'previous' ? siblingSection.previousElementSibling : siblingSection.nextElementSibling;
    }
    return null;
  };

  const focusAdjacentSectionArea = (row, direction) => {
    const currentSection = row.closest('[data-generated-structured-section]');
    if (!(currentSection instanceof HTMLElement)) return false;
    let siblingSection = direction === 'previous' ? currentSection.previousElementSibling : currentSection.nextElementSibling;
    while (siblingSection) {
      if (siblingSection instanceof HTMLElement && siblingSection.hasAttribute('data-generated-structured-section')) {
        const rows = [...siblingSection.querySelectorAll('[data-structured-statement-row]')];
        const targetRow = direction === 'previous' ? rows[rows.length - 1] : rows[0];
        if (targetRow) {
          focusStatementEditor(targetRow.querySelector('[data-structured-line-input]'));
          return true;
        }
      }
      siblingSection = direction === 'previous' ? siblingSection.previousElementSibling : siblingSection.nextElementSibling;
    }
    return false;
  };

  const statementRowForEvent = (event, container) => {
    const target = event.target;
    if (!(target instanceof Element)) return null;
    const row = target.closest('[data-structured-statement-row], [data-freeform-note-row]');
    return row instanceof HTMLElement && container.contains(row) ? row : null;
  };

  const emitStatementRowChange = (row) => {
    const checkbox = row.querySelector('[data-structured-line-checkbox], [data-freeform-note-checkbox]');
    const textarea = row.querySelector('[data-structured-line-input], [data-freeform-note-input]');
    if (!(checkbox instanceof HTMLInputElement) || !(textarea instanceof HTMLTextAreaElement)) return;
    syncStatementRowVisualState(row);
    autosizeStatementEditor(textarea);
    row._openscribeStatementCallbacks?.onChange?.({ row, checkbox, textarea });
  };

  const handleStatementEditorKeydown = (event, row, textarea) => {
    const callbacks = row._openscribeStatementCallbacks;
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      callbacks?.onAddAfter?.(row);
      return;
    }
    if ((event.key === 'Backspace' || event.key === 'Escape') && textarea.value.length === 0) {
      event.preventDefault();
      callbacks?.onEmptyDelete?.(row);
      return;
    }
    if (event.key === 'ArrowUp') {
      const position = textarea.selectionStart ?? 0;
      if (!textarea.value.slice(0, position).includes('\n')) {
        const previousRow = getAdjacentStatementRow(row, 'previous');
        if (previousRow) {
          event.preventDefault();
          focusStatementEditor(previousRow.querySelector('[data-structured-line-input], [data-freeform-note-input]'));
        }
      }
      return;
    }
    if (event.key === 'ArrowDown') {
      const position = textarea.selectionEnd ?? textarea.value.length;
      if (!textarea.value.slice(position).includes('\n')) {
        const nextRow = getAdjacentStatementRow(row, 'next');
        if (nextRow) {
          event.preventDefault();
          focusStatementEditor(nextRow.querySelector('[data-structured-line-input], [data-freeform-note-input]'));
        }
      }
      return;
    }
    if (event.key === 'Tab') {
      const moved = focusAdjacentSectionArea(row, event.shiftKey ? 'previous' : 'next');
      if (moved) {
        event.preventDefault();
      }
    }
  };

  const bindStatementEditorContainer = (container) => {
    if (!(container instanceof HTMLElement) || container.dataset.statementEditorDelegated === 'true') return;
    container.addEventListener('click', (event) => {
      const row = statementRowForEvent(event, container);
      const target = event.target;
      if (!row || target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement || target instanceof HTMLButtonElement) {
        return;
      }
      focusStatementEditor(row.querySelector('[data-structured-line-input], [data-freeform-note-input]'));
    });
    container.addEventListener('change', (event) => {
      const row = statementRowForEvent(event, container);
      if (!row || !(event.target instanceof HTMLInputElement)) return;
      if (!event.target.matches('[data-structured-line-checkbox], [data-freeform-note-checkbox]')) return;
      emitStatementRowChange(row);
    });
    container.addEventListener('input', (event) => {
      const row = statementRowForEvent(event, container);
      if (!row || !(event.target instanceof HTMLTextAreaElement)) return;
      if (!event.target.matches('[data-structured-line-input], [data-freeform-note-input]')) return;
      emitStatementRowChange(row);
    });
    container.addEventListener('keydown', (event) => {
      const row = statementRowForEvent(event, container);
      if (!row || !(event.target instanceof HTMLTextAreaElement)) return;
      if (!event.target.matches('[data-structured-line-input], [data-freeform-note-input]')) return;
      handleStatementEditorKeydown(event, row, event.target);
    });
    container.dataset.statementEditorDelegated = 'true';
  };

  const bindStatementRow = ({
    row,
    checkbox,
    textarea,
    sectionLabel = 'section',
    sectionKey = '',
    onChange,
    onAddAfter,
    onEmptyDelete,
  }) => {
    if (!(row instanceof HTMLElement) || !(checkbox instanceof HTMLInputElement) || !(textarea instanceof HTMLTextAreaElement)) {
      return row;
    }

    let dragHandle = row.querySelector('[data-statement-drag-handle]');
    if (!(dragHandle instanceof HTMLButtonElement)) {
      dragHandle = document.createElement('button');
      dragHandle.type = 'button';
      dragHandle.className = 'statement-drag-handle';
      dragHandle.setAttribute('data-statement-drag-handle', '');
      dragHandle.setAttribute('aria-label', 'Drag to reorder line');
      dragHandle.title = 'Drag to reorder line';
      dragHandle.textContent = '⋮⋮';
      row.insertBefore(dragHandle, row.firstChild);
    }

    checkbox.dataset.sectionKey = sectionKey;
    checkbox.dataset.sectionLabel = sectionLabel;
    checkbox.setAttribute('aria-label', `Select ${sectionLabel} statement`);
    textarea.dataset.sectionKey = sectionKey;
    textarea.dataset.sectionLabel = sectionLabel;
    row._openscribeStatementCallbacks = { onChange, onAddAfter, onEmptyDelete };
    row.dataset.statementEditorBound = 'true';
    syncStatementRowVisualState(row);
    return row;
  };

  const createStatementRow = ({
    sectionLabel = 'section',
    sectionKey = '',
    value = '',
    checked = true,
    disabled = false,
    placeholder = '',
    lineInputAttr,
    lineCheckboxAttr,
    lineRowAttr,
    onChange,
    onAddAfter,
    onEmptyDelete,
  }) => {
    const row = document.createElement('div');
    row.className = 'statement-row';
    row.setAttribute(lineRowAttr, '');

    const checkboxLabel = document.createElement('div');
    checkboxLabel.className = 'statement-checkbox';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = checked;
    checkbox.disabled = disabled;
    checkbox.setAttribute(lineCheckboxAttr, '');
    checkboxLabel.appendChild(checkbox);

    const content = document.createElement('div');
    content.className = 'statement-content';

    const textarea = document.createElement('textarea');
    textarea.rows = 1;
    textarea.className = 'statement-editor';
    textarea.placeholder = placeholder || `Add ${sectionLabel} statement`;
    textarea.value = value;
    textarea.disabled = disabled;
    textarea.setAttribute(lineInputAttr, '');

    row.appendChild(checkboxLabel);
    content.appendChild(textarea);
    row.appendChild(content);

    return bindStatementRow({
      row,
      checkbox,
      textarea,
      sectionLabel,
      sectionKey,
      onChange,
      onAddAfter,
      onEmptyDelete,
    });
  };

  const collectFreeformLinesFromText = (value = '') => {
    return String(value)
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  };

  const buildFreeformDraft = (generatedDocument) => {
    const lines = (
      generatedDocument
      && generatedDocument.status === 'ready'
      && generatedDocument.document_mode === 'freeform'
    ) ? collectFreeformLinesFromText(generatedDocument.edited_output_text || '') : [];
    return {
      documentId: generatedDocument?.id || '',
      templateId: dom.generateOutputTemplateSelect?.value || '',
      lines: lines.length > 0
        ? lines.map((line) => ({ text: line, checked: true }))
        : [{ text: '', checked: true }],
    };
  };

  const buildFreeformDraftFromDom = () => {
    if (!dom.generatedFreeformRows) return null;
    const rows = [...dom.generatedFreeformRows.querySelectorAll('[data-freeform-note-row]')].map((row) => {
      const checkbox = row.querySelector('[data-freeform-note-checkbox]');
      const textarea = row.querySelector('[data-freeform-note-input]');
      return {
        text: textarea?.value || '',
        checked: Boolean(checkbox?.checked),
      };
    });
    if (!rows.length) return null;
    return {
      documentId: dom.latestGeneratedOutput?.dataset.latestGeneratedId || '',
      templateId: dom.generateOutputTemplateSelect?.value || '',
      lines: rows,
    };
  };

  const serializeStructuredTextLines = (section) => {
    const lineText = String(section.text || section.edited_text || section.original_text || section.edited_text_encrypted || section.original_text_encrypted || '');
    return lineText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  };

  const buildGeneratedStructuredDraft = (generatedDocument, structuredContext = {}) => {
    const sourceSections = (structuredSectionDefinitionsFromDocument(generatedDocument).length
      ? structuredSectionDefinitionsFromDocument(generatedDocument)
      : selectedStructuredSectionDefinitions());
    const generatedSectionMap = (
      generatedDocument
      && generatedDocument.status === 'ready'
      && generatedDocument.document_mode === 'structured'
      && Array.isArray(generatedDocument.sections)
    )
      ? new Map(
          generatedDocument.sections.map((section) => [
            section.section_key || '',
            serializeStructuredTextLines(section),
          ])
        )
      : new Map();
    return {
      documentId: generatedDocument?.id || '',
      templateId: dom.generateOutputTemplateSelect?.value || '',
      sections: sourceSections.map((section) => ({
        sectionKey: section.key,
        sectionLabel: section.label,
        lines: [
          ...((generatedDocument ? generatedSectionMap.get(section.key) : structuredContext[section.key]) || [])
            .filter((line) => typeof line === 'string' && line.trim().length > 0)
            .map((line, lineIndex) => ({ text: line, checked: readRememberedRowSelectionState({ mode: 'structured', sectionKey: section.key, lineIndex, fallback: true }) })),
          { text: '', checked: readRememberedRowSelectionState({ mode: 'structured', sectionKey: section.key, lineIndex: (((generatedDocument ? generatedSectionMap.get(section.key) : structuredContext[section.key]) || []).filter((line) => typeof line === 'string' && line.trim().length > 0).length), fallback: true }) },
        ],
      })),
    };
  };

  const buildGeneratedStructuredDraftFromDom = () => {
    if (!dom.generatedStructuredSections) return null;
    const sections = [...dom.generatedStructuredSections.querySelectorAll('[data-generated-structured-section]')].map((section) => ({
      sectionKey: section.dataset.sectionKey || '',
      sectionLabel: section.dataset.sectionLabel || 'Section',
      lines: [...section.querySelectorAll('[data-structured-statement-row]')].map((row) => {
        const checkbox = row.querySelector('[data-structured-line-checkbox]');
        const textarea = row.querySelector('[data-structured-line-input]');
        return {
          text: textarea?.value || '',
          checked: Boolean(checkbox?.checked),
        };
      }),
    }));
    if (!sections.length) return null;
    return {
      documentId: dom.latestGeneratedOutput?.dataset.latestGeneratedId || '',
      templateId: dom.generateOutputTemplateSelect?.value || '',
      sections,
    };
  };

  const ensureSectionHasEditableRow = (sectionContainer) => {
    const rows = [...sectionContainer.querySelectorAll('[data-structured-statement-row]')];
    if (rows.length === 0) {
      addGeneratedStructuredLine(sectionContainer, '', null, true);
    }
  };

  const syncGeneratedStructuredDraftFromDom = () => {
    if (!generatedStructuredDraft || !dom.generatedStructuredSections) return;
    const sections = [...dom.generatedStructuredSections.querySelectorAll('[data-generated-structured-section]')];
    generatedStructuredDraft.sections = sections.map((section) => ({
      sectionKey: section.dataset.sectionKey || '',
      sectionLabel: section.dataset.sectionLabel || 'Section',
      lines: [...section.querySelectorAll('[data-structured-statement-row]')].map((row, lineIndex) => {
        const checkbox = row.querySelector('[data-structured-line-checkbox]');
        const textarea = row.querySelector('[data-structured-line-input]');
        const line = row._openscribeDraftLine || {};
        line.text = textarea?.value || '';
        line.checked = Boolean(checkbox?.checked);
        row._openscribeDraftLine = line;
        rememberRowSelectionState({ mode: 'structured', sectionKey: section.dataset.sectionKey || '', lineIndex, checked: line.checked });
        return line;
      }),
    }));
  };

  const syncGeneratedFreeformDraftFromDom = () => {
    if (!generatedFreeformDraft || !dom.generatedFreeformRows) return;
    generatedFreeformDraft.lines = [...dom.generatedFreeformRows.querySelectorAll('[data-freeform-note-row]')].map((row, lineIndex) => {
      const checkbox = row.querySelector('[data-freeform-note-checkbox]');
      const textarea = row.querySelector('[data-freeform-note-input]');
      const line = row._openscribeDraftLine || {};
      line.text = textarea?.value || '';
      line.checked = Boolean(checkbox?.checked);
      row._openscribeDraftLine = line;
      rememberRowSelectionState({ mode: 'freeform', lineIndex, checked: line.checked });
      return line;
    });
  };

  const rowInput = (row) => row?.querySelector?.('[data-structured-line-input], [data-freeform-note-input]') || null;
  const rowCheckbox = (row) => row?.querySelector?.('[data-structured-line-checkbox], [data-freeform-note-checkbox]') || null;

  const structuredDraftSectionForRow = (row) => {
    const sectionKey = row?.closest?.('[data-generated-structured-section]')?.dataset?.sectionKey || '';
    return generatedStructuredDraft?.sections?.find((section) => section.sectionKey === sectionKey) || null;
  };

  const syncGeneratedStructuredDraftLineFromDom = (row, textarea = rowInput(row), checkbox = rowCheckbox(row)) => {
    const line = row?._openscribeDraftLine;
    if (!line) {
      syncGeneratedStructuredDraftFromDom();
      return;
    }
    line.text = textarea?.value || '';
    line.checked = Boolean(checkbox?.checked);
    const section = structuredDraftSectionForRow(row);
    const lineIndex = section?.lines?.indexOf(line) ?? -1;
    if (lineIndex >= 0) {
      rememberRowSelectionState({
        mode: 'structured',
        sectionKey: section.sectionKey || '',
        lineIndex,
        checked: line.checked,
      });
    }
  };

  const syncGeneratedFreeformDraftLineFromDom = (row, textarea = rowInput(row), checkbox = rowCheckbox(row)) => {
    const line = row?._openscribeDraftLine;
    if (!line) {
      syncGeneratedFreeformDraftFromDom();
      return;
    }
    line.text = textarea?.value || '';
    line.checked = Boolean(checkbox?.checked);
    const lineIndex = generatedFreeformDraft?.lines?.indexOf(line) ?? -1;
    if (lineIndex >= 0) {
      rememberRowSelectionState({ mode: 'freeform', lineIndex, checked: line.checked });
    }
  };

  const insertGeneratedStructuredDraftLine = (sectionContainer, draftLine, afterRow = null) => {
    if (!generatedStructuredDraft || !draftLine) return;
    const sectionKey = sectionContainer?.dataset?.sectionKey || '';
    const section = generatedStructuredDraft.sections?.find((candidate) => candidate.sectionKey === sectionKey);
    if (!section) return;
    const afterLine = afterRow?._openscribeDraftLine;
    const afterIndex = afterLine ? section.lines.indexOf(afterLine) : -1;
    if (afterIndex >= 0) {
      section.lines.splice(afterIndex + 1, 0, draftLine);
    } else {
      section.lines.push(draftLine);
    }
  };

  const insertGeneratedFreeformDraftLine = (draftLine, afterRow = null) => {
    if (!generatedFreeformDraft || !draftLine) return;
    const afterLine = afterRow?._openscribeDraftLine;
    const afterIndex = afterLine ? generatedFreeformDraft.lines.indexOf(afterLine) : -1;
    if (afterIndex >= 0) {
      generatedFreeformDraft.lines.splice(afterIndex + 1, 0, draftLine);
    } else {
      generatedFreeformDraft.lines.push(draftLine);
    }
  };

  const removeGeneratedStructuredDraftLine = (row) => {
    const line = row?._openscribeDraftLine;
    const section = structuredDraftSectionForRow(row);
    if (!line || !section) return;
    const index = section.lines.indexOf(line);
    if (index >= 0) section.lines.splice(index, 1);
  };

  const removeGeneratedFreeformDraftLine = (row) => {
    const line = row?._openscribeDraftLine;
    if (!line || !generatedFreeformDraft) return;
    const index = generatedFreeformDraft.lines.indexOf(line);
    if (index >= 0) generatedFreeformDraft.lines.splice(index, 1);
  };

  const syncMovedRowSectionMetadata = (row) => {
    if (!(row instanceof HTMLElement)) return;
    const section = row.closest('[data-generated-structured-section]');
    if (!(section instanceof HTMLElement)) return;
    const sectionKey = section.dataset.sectionKey || '';
    const sectionLabel = section.dataset.sectionLabel || 'Section';
    row.querySelectorAll('[data-structured-line-input], [data-structured-line-checkbox]').forEach((node) => {
      node.dataset.sectionKey = sectionKey;
      node.dataset.sectionLabel = sectionLabel;
      if (node instanceof HTMLTextAreaElement) {
        node.placeholder = `Add ${sectionLabel.toLowerCase()} statement`;
      }
      if (node instanceof HTMLInputElement) {
        node.setAttribute('aria-label', `Select ${sectionLabel} statement`);
      }
    });
  };

  const removePlaceholderRowIfFilled = (container) => {
    if (!(container instanceof HTMLElement)) return;
    const rows = [
      ...container.querySelectorAll('[data-structured-statement-row], [data-freeform-note-row]'),
    ];
    const filledRows = rows.filter((row) => String(rowInput(row)?.value || '').trim().length > 0);
    if (!filledRows.length) return;

    const blankRows = rows.filter((row) => String(rowInput(row)?.value || '').trim().length === 0);
    blankRows.slice(0, -1).forEach((row) => row.remove());
  };

  const structuredSectionForRow = (row) => row?.closest?.('[data-generated-structured-section]') || null;

  const structuredRowCallbacks = () => ({
    onChange: ({ row, textarea, checkbox }) => {
      syncGeneratedStructuredDraftLineFromDom(row, textarea, checkbox);
      handleStructuredContextChanged();
      if (dom.structuredCopyStatus) {
        dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
      }
      const sectionContainer = structuredSectionForRow(row);
      invalidateCopyReviewForEdit({ mode: 'structured', sectionKey: sectionContainer?.dataset?.sectionKey || '' });
    },
    onAddAfter: (currentRow) => {
      const sectionContainer = structuredSectionForRow(currentRow);
      if (!(sectionContainer instanceof HTMLElement)) return;
      const nextRow = addGeneratedStructuredLine(sectionContainer, '', currentRow, true);
      window.requestAnimationFrame(() => {
        focusStatementEditor(nextRow?.querySelector('[data-structured-line-input]'));
      });
    },
    onEmptyDelete: (currentRow) => {
      const sectionContainer = structuredSectionForRow(currentRow);
      if (!(sectionContainer instanceof HTMLElement)) return;
      const previousRow = getAdjacentStatementRow(currentRow, 'previous');
      if (!(previousRow instanceof HTMLElement)) {
        return;
      }
      removeGeneratedStructuredDraftLine(currentRow);
      currentRow.remove();
      ensureSectionHasEditableRow(sectionContainer);
      handleStructuredContextChanged();
      if (dom.structuredCopyStatus) {
        dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
      }
      invalidateCopyReviewForEdit({ mode: 'structured', sectionKey: sectionContainer.dataset.sectionKey || '' });
      window.requestAnimationFrame(() => {
        focusStatementEditor(previousRow.querySelector('[data-structured-line-input], [data-freeform-note-input]'));
      });
    },
  });

  const freeformRowCallbacks = () => ({
    onChange: ({ row, textarea, checkbox }) => {
      syncGeneratedFreeformDraftLineFromDom(row, textarea, checkbox);
      if (dom.structuredCopyStatus) {
        dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
      }
      invalidateCopyReviewForEdit({ mode: 'freeform' });
      onNoteEditorChanged?.();
    },
    onAddAfter: (currentRow) => {
      const nextRow = addGeneratedFreeformLine('', currentRow, true);
      window.requestAnimationFrame(() => {
        focusStatementEditor(nextRow?.querySelector('[data-freeform-note-input]'));
      });
    },
    onEmptyDelete: (currentRow) => {
      const previousRow = getAdjacentStatementRow(currentRow, 'previous');
      if (!(previousRow instanceof HTMLElement)) {
        return;
      }
      removeGeneratedFreeformDraftLine(currentRow);
      currentRow.remove();
      ensureFreeformHasEditableRow();
      if (dom.structuredCopyStatus) {
        dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
      }
      invalidateCopyReviewForEdit({ mode: 'freeform' });
      onNoteEditorChanged?.();
      window.requestAnimationFrame(() => {
        focusStatementEditor(previousRow.querySelector('[data-freeform-note-input], [data-structured-line-input]'));
      });
    },
  });

  const addGeneratedStructuredLine = (sectionContainer, value = '', afterRow = null, checked = true, options = {}) => {
    const rows = sectionContainer.querySelector('[data-generated-structured-section-rows]');
    if (!rows) return null;
    const draftLine = options.draftLine || { text: value, checked };
    const effectiveValue = String(draftLine.text ?? value ?? '');
    const effectiveChecked = draftLine.checked !== false;
    const row = createStatementRow({
      sectionLabel: sectionContainer.dataset.sectionLabel || 'Section',
      sectionKey: sectionContainer.dataset.sectionKey || '',
      value: effectiveValue,
      checked: effectiveChecked,
      disabled: false,
      placeholder: `Add ${(sectionContainer.dataset.sectionLabel || 'section').toLowerCase()} statement`,
      lineInputAttr: 'data-structured-line-input',
      lineCheckboxAttr: 'data-structured-line-checkbox',
      lineRowAttr: 'data-structured-statement-row',
      ...structuredRowCallbacks(),
    });
    row._openscribeDraftLine = draftLine;
    if (!options.draftLine) {
      insertGeneratedStructuredDraftLine(sectionContainer, draftLine, afterRow);
    }
    if (afterRow && afterRow.parentNode === rows) {
      afterRow.insertAdjacentElement('afterend', row);
    } else {
      rows.appendChild(row);
    }
    if (options.focus === true) {
      window.requestAnimationFrame(() => {
        focusStatementEditor(row.querySelector('[data-structured-line-input]'));
      });
    }
    return row;
  };

  const ensureFreeformHasEditableRow = () => {
    if (!dom.generatedFreeformRows) return;
    const rows = [...dom.generatedFreeformRows.querySelectorAll('[data-freeform-note-row]')];
    if (rows.length === 0) {
      addGeneratedFreeformLine('', null, true);
    }
  };

  const focusLine = (rowOrInput, target = 'handle') => {
    const row = rowOrInput instanceof HTMLTextAreaElement
      ? rowOrInput.closest('[data-structured-statement-row], [data-freeform-note-row]')
      : rowOrInput;
    if (!(row instanceof HTMLElement)) return;
    const focusTarget = target === 'textarea'
      ? rowInput(row)
      : row.querySelector('[data-statement-drag-handle]');
    window.requestAnimationFrame(() => {
      if (focusTarget instanceof HTMLElement) {
        if (focusTarget instanceof HTMLTextAreaElement) autosizeStatementEditor(focusTarget);
        focusTarget.focus();
        row.scrollIntoView({ block: 'nearest' });
      }
    });
  };

  const getLineContextFromInput = (input) => {
    if (!(input instanceof HTMLTextAreaElement)) return null;
    const structuredRow = input.closest('[data-structured-statement-row]');
    if (structuredRow instanceof HTMLElement) {
      const section = input.closest('[data-generated-structured-section]');
      return {
        mode: 'structured',
        row: structuredRow,
        section,
        input,
        checkbox: rowCheckbox(structuredRow),
      };
    }
    const freeformRow = input.closest('[data-freeform-note-row]');
    if (freeformRow instanceof HTMLElement) {
      return {
        mode: 'freeform',
        row: freeformRow,
        section: null,
        input,
        checkbox: rowCheckbox(freeformRow),
      };
    }
    return null;
  };

  const notifyNoteRowsChanged = ({ mode } = {}) => {
    if (mode === 'freeform') {
      ensureFreeformHasEditableRow();
      syncGeneratedFreeformDraftFromDom();
    } else {
      document.querySelectorAll('[data-generated-structured-section]').forEach((section) => {
        section.querySelectorAll('[data-structured-statement-row]').forEach((row) => {
          syncMovedRowSectionMetadata(row);
        });
        ensureSectionHasEditableRow(section);
        removePlaceholderRowIfFilled(section);
      });
      syncGeneratedStructuredDraftFromDom();
    }
    handleStructuredContextChanged();
    if (dom.structuredCopyStatus) {
      dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
    }
    scheduleCopyReviewRefresh();
    onNoteEditorChanged?.();
  };

  const addGeneratedFreeformLine = (value = '', afterRow = null, checked = true, options = {}) => {
    if (!dom.generatedFreeformRows) return null;
    const draftLine = options.draftLine || { text: value, checked };
    const effectiveValue = String(draftLine.text ?? value ?? '');
    const effectiveChecked = draftLine.checked !== false;
    const row = createStatementRow({
      sectionLabel: 'note',
      sectionKey: '',
      value: effectiveValue,
      checked: effectiveChecked,
      disabled: false,
      placeholder: 'Add note line',
      lineInputAttr: 'data-freeform-note-input',
      lineCheckboxAttr: 'data-freeform-note-checkbox',
      lineRowAttr: 'data-freeform-note-row',
      ...freeformRowCallbacks(),
    });
    row._openscribeDraftLine = draftLine;
    if (!options.draftLine) {
      insertGeneratedFreeformDraftLine(draftLine, afterRow);
    }
    if (afterRow && afterRow.parentNode === dom.generatedFreeformRows) {
      afterRow.insertAdjacentElement('afterend', row);
    } else {
      dom.generatedFreeformRows.appendChild(row);
    }
    if (options.focus === true) {
      window.requestAnimationFrame(() => {
        focusStatementEditor(row.querySelector('[data-freeform-note-input]'));
      });
    }
    return row;
  };

  const finalizeStructuredEditorRows = (panel) => {
    syncStructuredEditorAvailability();
    syncCopyReviewUi();
    window.requestAnimationFrame(() => {
      autosizeStatementEditorsIn(panel);
      window.requestAnimationFrame(() => observeCopyReviewTargets());
    });
  };

  const hydrateStructuredRowsFromDom = () => {
    if (!dom.generatedStructuredSections || !dom.generatedStructuredPanel || !generatedStructuredDraft) return;
    disconnectStatementAutosizeObserver();
    const sectionElements = [...dom.generatedStructuredSections.querySelectorAll('[data-generated-structured-section]')];
    sectionElements.forEach((sectionElement) => {
      const draftSection = generatedStructuredDraft.sections.find((section) => section.sectionKey === (sectionElement.dataset.sectionKey || ''));
      const rows = [...sectionElement.querySelectorAll('[data-structured-statement-row]')];
      rows.forEach((row, lineIndex) => {
        const checkbox = row.querySelector('[data-structured-line-checkbox]');
        const textarea = row.querySelector('[data-structured-line-input]');
        if (!(checkbox instanceof HTMLInputElement) || !(textarea instanceof HTMLTextAreaElement)) return;
        const draftLine = draftSection?.lines?.[lineIndex] || { text: textarea.value || '', checked: checkbox.checked };
        row._openscribeDraftLine = draftLine;
        bindStatementRow({
          row,
          checkbox,
          textarea,
          sectionLabel: sectionElement.dataset.sectionLabel || 'Section',
          sectionKey: sectionElement.dataset.sectionKey || '',
          ...structuredRowCallbacks(),
        });
      });
    });
    dom.generatedStructuredPanel.hidden = false;
    finalizeStructuredEditorRows(dom.generatedStructuredPanel);
  };

  const hydrateFreeformRowsFromDom = () => {
    if (!dom.generatedFreeformRows || !dom.generatedFreeformPanel || !generatedFreeformDraft) return;
    disconnectStatementAutosizeObserver();
    const rows = [...dom.generatedFreeformRows.querySelectorAll('[data-freeform-note-row]')];
    rows.forEach((row, lineIndex) => {
      const checkbox = row.querySelector('[data-freeform-note-checkbox]');
      const textarea = row.querySelector('[data-freeform-note-input]');
      if (!(checkbox instanceof HTMLInputElement) || !(textarea instanceof HTMLTextAreaElement)) return;
      const draftLine = generatedFreeformDraft.lines[lineIndex] || { text: textarea.value || '', checked: checkbox.checked };
      row._openscribeDraftLine = draftLine;
      bindStatementRow({
        row,
        checkbox,
        textarea,
        sectionLabel: 'note',
        sectionKey: '',
        ...freeformRowCallbacks(),
      });
    });
    dom.generatedFreeformPanel.hidden = false;
    finalizeStructuredEditorRows(dom.generatedFreeformPanel);
  };

  const renderStructuredSections = (draft) => {
    if (!dom.generatedStructuredSections || !dom.generatedStructuredPanel) return;
    const focusState = captureEditorFocusState();
    copyReviewObservationReady = false;
    disconnectStatementAutosizeObserver();
    dom.generatedStructuredSections.innerHTML = '';
    if (!draft || !Array.isArray(draft.sections) || draft.sections.length === 0) {
      dom.generatedStructuredPanel.hidden = true;
      return;
    }
    draft.sections.forEach((section) => {
      const card = document.createElement('div');
      card.className = 'structured-section-block';
      card.setAttribute('data-generated-structured-section', '');
      card.dataset.sectionKey = section.sectionKey || '';
      card.dataset.sectionLabel = section.sectionLabel || 'Section';

      const header = document.createElement('div');
      header.className = 'structured-section-header';

      const title = document.createElement('h3');
      title.className = 'structured-section-title';
      title.textContent = section.sectionLabel || 'Section';
      header.appendChild(title);

      const copyButton = document.createElement('button');
      copyButton.type = 'button';
      copyButton.className = 'structured-section-copy-button';
      copyButton.setAttribute('data-copy-structured-section', '');
      copyButton.setAttribute('aria-label', `Copy ${section.sectionLabel || 'section'} section`);
      copyButton.title = 'Copy section';
      const copyIcon = document.createElement('i');
      copyIcon.className = 'w-3.5 h-3.5';
      copyIcon.setAttribute('data-lucide', 'copy');
      copyIcon.setAttribute('aria-hidden', 'true');
      copyButton.appendChild(copyIcon);
      header.appendChild(copyButton);

      const body = document.createElement('div');
      body.className = 'structured-statement-list';
      body.setAttribute('data-generated-structured-section-rows', '');

      card.appendChild(header);
      card.appendChild(body);
      dom.generatedStructuredSections.appendChild(card);
      const sectionLines = Array.isArray(section.lines) ? section.lines : [];
      if (sectionLines.length > 0) {
        sectionLines.forEach((line, lineIndex) => {
          line.checked = readRememberedRowSelectionState({
            mode: 'structured',
            sectionKey: section.sectionKey || '',
            lineIndex,
            fallback: line.checked !== false,
          });
          addGeneratedStructuredLine(card, line.text || '', null, line.checked !== false, { draftLine: line });
        });
      } else {
        addGeneratedStructuredLine(card, '', null, true);
      }
      window.refreshLucideIcons?.(card);
    });
    dom.generatedStructuredPanel.hidden = false;
    finalizeStructuredEditorRows(dom.generatedStructuredPanel);
    restoreEditorFocusState(focusState?.mode === 'structured' ? focusState : null);
  };

  const renderFreeformLines = (draft) => {
    if (!dom.generatedFreeformRows || !dom.generatedFreeformPanel) return;
    const focusState = captureEditorFocusState();
    copyReviewObservationReady = false;
    disconnectStatementAutosizeObserver();
    dom.generatedFreeformRows.innerHTML = '';
    if (!draft || !Array.isArray(draft.lines) || draft.lines.length === 0) {
      dom.generatedFreeformPanel.hidden = true;
      return;
    }
    draft.lines.forEach((line, lineIndex) => {
      line.checked = readRememberedRowSelectionState({ mode: 'freeform', lineIndex, fallback: line.checked !== false });
      addGeneratedFreeformLine(line.text || '', null, line.checked !== false, { draftLine: line });
    });
    dom.generatedFreeformPanel.hidden = false;
    finalizeStructuredEditorRows(dom.generatedFreeformPanel);
    restoreEditorFocusState(focusState?.mode === 'freeform' ? focusState : null);
  };

  const syncNoteEditorToolbar = () => {
    if (!dom.noteEditorToolbar) return;
    const structuredVisible = Boolean(dom.generatedStructuredPanel && !dom.generatedStructuredPanel.hidden);
    const freeformVisible = Boolean(dom.generatedFreeformPanel && !dom.generatedFreeformPanel.hidden);
    dom.noteEditorToolbar.hidden = !(structuredVisible || freeformVisible);
  };

  const renderGeneratedOutput = (generatedDocument, structuredContext = {}) => {
    if (!dom.latestGeneratedOutput || !dom.generatedStructuredPanel || !dom.generatedFreeformPanel) return;
    currentRenderedDocument = generatedDocument || null;
    currentRenderRequiresCopyReview = Boolean(generatedDocument?.id && generatedDocument?.kind !== 'working_note');
    resetCopyReviewStateForDocument();
    setCopyReviewStatus();
    dom.latestGeneratedOutput.hidden = false;
    renderStructuredSections(null);
    renderFreeformLines(null);
    const shouldShowStructuredEditor = generatedDocument
      ? generatedDocument.status === 'ready' && generatedDocument.document_mode === 'structured'
      : false;
    const shouldShowFreeformEditor = generatedDocument
      ? generatedDocument.status === 'ready' && generatedDocument.document_mode === 'freeform'
      : false;
    if (shouldShowStructuredEditor) {
      generatedStructuredDraft = buildGeneratedStructuredDraft(generatedDocument, structuredContext);
      generatedFreeformDraft = null;
      dom.latestGeneratedOutput.hidden = true;
      renderStructuredSections(generatedStructuredDraft);
      setCopyReviewStatus();
      syncNoteEditorToolbar();
      return;
    }
    if (shouldShowFreeformEditor) {
      generatedStructuredDraft = null;
      generatedFreeformDraft = buildFreeformDraft(generatedDocument);
      dom.latestGeneratedOutput.hidden = true;
      renderFreeformLines(generatedFreeformDraft);
      setCopyReviewStatus();
      syncNoteEditorToolbar();
      return;
    }
    generatedStructuredDraft = null;
    generatedFreeformDraft = null;
    observeCopyReviewTargets();
    if (!generatedDocument) {
      syncStructuredTemplateUi();
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'ready' && generatedDocument.edited_output_text) {
      dom.latestGeneratedOutput.textContent = generatedDocument.edited_output_text;
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'queued') {
      const message = getTranscriptWaitingForText?.()
        ? 'Waiting for transcription to finish before writing your note.'
        : "This usually takes a few seconds.<br>We're preparing your clinical note...";
      dom.latestGeneratedOutput.innerHTML = generationLoadingHtml({ label: 'note', message });
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'processing') {
      dom.latestGeneratedOutput.innerHTML = generationLoadingHtml({ label: 'note' });
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'failed') {
      dom.latestGeneratedOutput.innerHTML = `<span class="text-slate">The latest note could not be created${generatedDocument.error_message ? `: ${escapeHtml(generatedDocument.error_message)}` : ''}.</span>`;
      syncNoteEditorToolbar();
      return;
    }
    dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">No note yet.</span>';
    syncNoteEditorToolbar();
  };

  const initializeHydratedGeneratedDocument = (generatedDocument) => {
    currentRenderedDocument = generatedDocument || null;
    currentRenderRequiresCopyReview = Boolean(generatedDocument?.id && generatedDocument?.kind !== 'working_note');
    copyReviewObservationReady = false;
    // Bootstrap runs before the first workspace payload establishes whether the
    // server-rendered rows belong to a generated note or a Working note. Reset
    // even for the same ID so a generated note starts unreviewed.
    copyReviewDocumentId = null;
    resetCopyReviewStateForDocument();
    setCopyReviewStatus();
    observeCopyReviewTargets();
  };

  const syncTemplateModeBadge = () => {
    const isStructuredTemplate = selectedOutputTemplateMode() === 'structured';
    if (dom.templateModeBadge) {
      dom.templateModeBadge.textContent = isStructuredTemplate ? 'Sectioned note' : 'Free text note';
    }
  };

  const syncStructuredTemplateUi = () => {
    const isStructuredTemplate = selectedOutputTemplateMode() === 'structured';
    const hasGeneratedNote = Boolean(dom.latestGeneratedOutput?.dataset.latestGeneratedId);
    const generatedMode = activeGeneratedDocumentMode();
    const generatedStatus = dom.latestGeneratedOutput?.dataset?.latestGeneratedStatus || '';
    syncTemplateModeBadge();
    if (currentRenderedDocument?.kind === 'working_note') {
      const nextDocument = currentRenderedDocument.mode_locked
        ? currentRenderedDocument
        : { ...currentRenderedDocument, document_mode: selectedOutputTemplateMode() };
      renderGeneratedOutput(nextDocument, {});
    } else if (hasGeneratedNote && generatedStatus === 'ready' && generatedMode === 'freeform') {
      // A selected saved note owns the editor mode. Do not expose a blank
      // sectioned template before an already-rendered free-text note.
      dom.generatedStructuredPanel.hidden = true;
      dom.generatedFreeformPanel.hidden = false;
    } else if (hasGeneratedNote && generatedStatus === 'ready' && generatedMode === 'structured') {
      // Mirror the free-text case: the selected ready document wins over the
      // currently selected template until the user creates a new document.
      dom.generatedFreeformPanel.hidden = true;
      dom.generatedStructuredPanel.hidden = false;
    } else if (isStructuredTemplate) {
      if (hasGeneratedNote && generatedMode === 'structured' && !generatedStructuredDraft) {
        generatedStructuredDraft = buildGeneratedStructuredDraftFromDom() || generatedStructuredDraft;
      }
      if (hasGeneratedNote && generatedMode === 'structured' && generatedStructuredDraft) {
        renderStructuredSections(generatedStructuredDraft);
        dom.generatedFreeformPanel.hidden = true;
      } else if (!hasGeneratedNote) {
        generatedStructuredDraft = buildGeneratedStructuredDraft(null, collectStructuredContext());
        generatedFreeformDraft = null;
        renderStructuredSections(generatedStructuredDraft);
        dom.generatedFreeformPanel.hidden = true;
      }
    } else if (hasGeneratedNote && generatedMode === 'freeform' && (!generatedFreeformDraft || generatedFreeformDraft.templateId !== (dom.generateOutputTemplateSelect?.value || ''))) {
      generatedFreeformDraft = buildFreeformDraft({
        id: dom.latestGeneratedOutput?.dataset.latestGeneratedId || '',
        status: 'ready',
        document_mode: 'freeform',
        edited_output_text: collectSelectedNoteLines({ includeUnselected: true, mode: 'freeform' }).map((line) => line.text).join('\n'),
      });
      renderFreeformLines(generatedFreeformDraft);
      dom.generatedStructuredPanel.hidden = true;
    } else if (!hasGeneratedNote) {
      generatedStructuredDraft = null;
      generatedFreeformDraft = buildFreeformDraft(null);
      renderFreeformLines(generatedFreeformDraft);
      dom.generatedStructuredPanel.hidden = true;
    }
    syncNoteEditorToolbar();
    syncGenerationAvailability(getDraftText() || '');
  };

  const bootstrapFromDom = () => {
    resetCopyReviewStateForDocument();
    setCopyReviewStatus();
    if (dom.generatedStructuredPanel && !dom.generatedStructuredPanel.hidden) {
      generatedStructuredDraft = buildGeneratedStructuredDraftFromDom();
      if (generatedStructuredDraft) {
        hydrateStructuredRowsFromDom();
      }
    }
    if (dom.generatedFreeformPanel && !dom.generatedFreeformPanel.hidden) {
      generatedFreeformDraft = buildFreeformDraftFromDom();
      if (generatedFreeformDraft) {
        hydrateFreeformRowsFromDom();
      }
    }
    syncNoteEditorToolbar();
  };

  const clearStructuredSelection = () => {
    document.querySelectorAll('[data-generated-structured-section] [data-structured-line-checkbox], [data-generated-freeform-panel] [data-freeform-note-checkbox]').forEach((checkbox) => {
      checkbox.checked = false;
      syncStatementRowVisualState(checkbox.closest('[data-structured-statement-row], [data-freeform-note-row]'));
    });
    if (dom.structuredCopyStatus) {
      dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
    }
    syncGeneratedStructuredDraftFromDom();
    syncGeneratedFreeformDraftFromDom();
  };

  const selectStructuredSelection = () => {
    document.querySelectorAll('[data-generated-structured-section] [data-structured-line-checkbox], [data-generated-freeform-panel] [data-freeform-note-checkbox]').forEach((checkbox) => {
      checkbox.checked = true;
      syncStatementRowVisualState(checkbox.closest('[data-structured-statement-row], [data-freeform-note-row]'));
    });
    if (dom.structuredCopyStatus) {
      dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
    }
    syncGeneratedStructuredDraftFromDom();
    syncGeneratedFreeformDraftFromDom();
  };

  const collectSelectedNoteLines = ({ includeUnselected = false, mode = 'all' } = {}) => {
    const rows = [];
    if (mode === 'all' || mode === 'structured') {
      document.querySelectorAll('[data-generated-structured-section] [data-structured-statement-row]').forEach((row) => {
        const checkbox = row.querySelector('[data-structured-line-checkbox]');
        const textarea = row.querySelector('[data-structured-line-input]');
        const checked = Boolean(checkbox?.checked);
        const text = textarea?.value?.trim() || '';
        if ((!includeUnselected && !checked) || !text) return;
        rows.push({
          checked,
          sectionKey: checkbox?.dataset.sectionKey || textarea?.dataset.sectionKey || '',
          label: checkbox?.dataset.sectionLabel || textarea?.dataset.sectionLabel || '',
          text,
        });
      });
    }
    if (mode === 'all' || mode === 'freeform') {
      document.querySelectorAll('[data-generated-freeform-panel] [data-freeform-note-row]').forEach((row) => {
        const checkbox = row.querySelector('[data-freeform-note-checkbox]');
        const textarea = row.querySelector('[data-freeform-note-input]');
        const checked = Boolean(checkbox?.checked);
        const text = textarea?.value?.trim() || '';
        if ((!includeUnselected && !checked) || !text) return;
        rows.push({
          checked,
          label: '',
          text,
        });
      });
    }
    return rows;
  };

  const serializeCurrentNoteEditor = ({ mode = activeGeneratedDocumentMode() || selectedOutputTemplateMode(), includeUncheckedStructuredLines = true } = {}) => {
    if (mode === 'structured') {
      if (!generatedStructuredDraft) {
        generatedStructuredDraft = buildGeneratedStructuredDraftFromDom();
      }
      syncGeneratedStructuredDraftFromDom();
      const draft = generatedStructuredDraft || buildGeneratedStructuredDraftFromDom();
      return {
        mode: 'structured',
        sections: (draft?.sections || []).map((section, index) => ({
          section_key: section.sectionKey || '',
          section_label: section.sectionLabel || 'Section',
          section_order: index,
          text: (section.lines || [])
            .filter((line) => includeUncheckedStructuredLines || line.checked !== false)
            .map((line) => String(line.text || '').trim())
            .filter((value) => value.length > 0)
            .join('\n'),
        })),
      };
    }

    if (!generatedFreeformDraft) {
      generatedFreeformDraft = buildFreeformDraftFromDom();
    }
    syncGeneratedFreeformDraftFromDom();
    const draft = generatedFreeformDraft || buildFreeformDraftFromDom();
    return {
      mode: 'freeform',
      edited_output_text: (draft?.lines || [])
        .map((line) => String(line.text || '').trim())
        .filter((value) => value.length > 0)
        .join('\n'),
    };
  };

  const collectStructuredSectionLines = (section) => {
    if (!(section instanceof HTMLElement)) {
      return [];
    }
    const sectionKey = section.dataset.sectionKey || '';
    return collectSelectedNoteLines({ mode: 'structured' })
      .filter((line) => line.sectionKey === sectionKey)
      .map((line) => line.text);
  };

  const hasNoteInputContent = () => {
    return collectSelectedNoteLines({ includeUnselected: true }).some((line) => String(line.text || '').trim().length > 0);
  };

  bindStatementEditorContainer(dom.generatedStructuredPanel);
  bindStatementEditorContainer(dom.generatedFreeformPanel);

  return {
    notifyNoteRowsChanged,
    getLineContextFromInput,
    focusLine,
    autosizeStatementEditor,
    syncMovedRowSectionMetadata,
    ensureSectionHasEditableRow,
    ensureFreeformHasEditableRow,
    removePlaceholderRowIfFilled,
    bootstrapFromDom,
    buildGeneratedStructuredDraft,
    collectSelectedNoteLines,
    collectStructuredSectionLines,
    clearStructuredSelection,
    collectStructuredContext,
    getGeneratedStructuredDraft: () => generatedStructuredDraft,
    hasNoteInputContent,
    initializeHydratedGeneratedDocument,
    renderGeneratedOutput,
    renderStructuredSections,
    noteCopyReviewBlocker,
    selectedOutputTemplateMode,
    selectStructuredSelection,
    serializeCurrentNoteEditor,
    setGeneratedStructuredDraft: (draft) => {
      generatedStructuredDraft = draft;
    },
    syncStatementRowVisualState,
    syncStructuredEditorAvailability,
    syncTemplateModeBadge,
    syncStructuredTemplateUi,
  };
}
