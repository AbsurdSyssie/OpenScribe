export function createStructuredEditor({
  dom,
  structuredSectionDefinitions,
  getTranscriptId,
  getDraftText,
  syncGenerationAvailability,
  persistStructuredContextSilently,
}) {
  let generatedStructuredDraft = null;
  let emisSaveTimer = null;
  let lastSavedStructuredContext = null;

  const autosizeStatementEditor = (textarea) => {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.max(textarea.scrollHeight, 22)}px`;
  };

  const focusStatementEditor = (textarea) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    textarea.focus();
    const length = textarea.value.length;
    textarea.setSelectionRange(length, length);
  };

  const syncStatementRowVisualState = (row) => {
    if (!row) return;
    const checkbox = row.querySelector('input[type="checkbox"]');
    row.classList.toggle('is-unchecked', Boolean(checkbox && !checkbox.checked));
  };

  const selectedOutputTemplateMode = () => {
    const selectedOption = dom.generateOutputTemplateSelect?.selectedOptions?.[0];
    return selectedOption?.dataset?.templateMode || 'freeform';
  };

  const collectStructuredContext = () => {
    const context = {};
    document.querySelectorAll('[data-generated-structured-section]').forEach((section) => {
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
  };

  const syncStructuredContextHiddenInputs = () => {
    const context = collectStructuredContext();
    dom.structuredContextHiddenInputs.forEach((input) => {
      const sectionKey = input.dataset.sectionKey || '';
      input.value = JSON.stringify(context[sectionKey] || []);
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
  };

  const scheduleStructuredContextSave = () => {
    if (selectedOutputTemplateMode() !== 'structured') {
      return;
    }
    if (emisSaveTimer) {
      window.clearTimeout(emisSaveTimer);
    }
    emisSaveTimer = window.setTimeout(async () => {
      try {
        await persistStructuredContextSilently?.();
      } catch (_) {}
    }, 500);
  };

  const handleStructuredContextChanged = () => {
    syncGenerationAvailability(getDraftText() || '');
    scheduleStructuredContextSave();
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

  const createStatementRow = ({
    sectionLabel = 'section',
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

    const checkboxLabel = document.createElement('label');
    checkboxLabel.className = 'statement-checkbox';
    checkboxLabel.setAttribute('aria-label', `Select ${sectionLabel} statement`);

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = checked;
    checkbox.disabled = disabled;
    checkbox.setAttribute(lineCheckboxAttr, '');
    checkbox.dataset.sectionLabel = sectionLabel;
    checkboxLabel.appendChild(checkbox);

    const textarea = document.createElement('textarea');
    textarea.rows = 1;
    textarea.className = 'statement-editor';
    textarea.placeholder = placeholder || `Add ${sectionLabel} statement`;
    textarea.value = value;
    textarea.disabled = disabled;
    textarea.setAttribute(lineInputAttr, '');
    textarea.dataset.sectionLabel = sectionLabel;

    row.appendChild(checkboxLabel);
    row.appendChild(textarea);

    const emitChange = () => {
      syncStatementRowVisualState(row);
      autosizeStatementEditor(textarea);
      onChange?.();
    };

    row.addEventListener('click', (event) => {
      const target = event.target;
      if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
        return;
      }
      checkbox.checked = !checkbox.checked;
      emitChange();
    });
    checkbox.addEventListener('change', emitChange);
    textarea.addEventListener('input', emitChange);
    textarea.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        onAddAfter?.(row);
        return;
      }
      if ((event.key === 'Backspace' || event.key === 'Escape') && textarea.value.length === 0) {
        event.preventDefault();
        onEmptyDelete?.(row);
        return;
      }
      if (event.key === 'ArrowUp') {
        const position = textarea.selectionStart ?? 0;
        if (!textarea.value.slice(0, position).includes('\n')) {
          const previousRow = getAdjacentStatementRow(row, 'previous');
          if (previousRow) {
            event.preventDefault();
            focusStatementEditor(previousRow.querySelector('[data-structured-line-input]'));
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
            focusStatementEditor(nextRow.querySelector('[data-structured-line-input]'));
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
    });

    syncStatementRowVisualState(row);
    window.requestAnimationFrame(() => autosizeStatementEditor(textarea));
    return row;
  };

  const serializeStructuredTextLines = (section) => {
    const lineText = String(section.edited_text_encrypted || section.original_text_encrypted || '');
    return lineText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  };

  const buildGeneratedStructuredDraft = (generatedDocument, structuredContext = {}) => {
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
      sections: structuredSectionDefinitions.map((section) => ({
        sectionKey: section.key,
        sectionLabel: section.label,
        lines: [
          ...(generatedSectionMap.get(section.key) || structuredContext[section.key] || [])
            .filter((line) => typeof line === 'string' && line.trim().length > 0)
            .map((line) => ({ text: line, checked: true })),
          { text: '', checked: true },
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
      sections,
    };
  };

  const ensureSectionHasEmptyRow = (sectionContainer) => {
    const rows = [...sectionContainer.querySelectorAll('[data-structured-statement-row]')];
    const hasEmptyRow = rows.some((row) => {
      const input = row.querySelector('[data-structured-line-input]');
      return !input || !input.value.trim();
    });
    if (!hasEmptyRow) {
      addGeneratedStructuredLine(sectionContainer, '', null, true);
    }
  };

  const syncGeneratedStructuredDraftFromDom = () => {
    if (!generatedStructuredDraft || !dom.generatedStructuredSections) return;
    const sections = [...dom.generatedStructuredSections.querySelectorAll('[data-generated-structured-section]')];
    generatedStructuredDraft.sections = sections.map((section) => ({
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
  };

  const addGeneratedStructuredLine = (sectionContainer, value = '', afterRow = null, checked = true, options = {}) => {
    const rows = sectionContainer.querySelector('[data-generated-structured-section-rows]');
    if (!rows) return null;
    const row = createStatementRow({
      sectionLabel: sectionContainer.dataset.sectionLabel || 'Section',
      value,
      checked,
      disabled: false,
      placeholder: `Add ${(sectionContainer.dataset.sectionLabel || 'section').toLowerCase()} statement`,
      lineInputAttr: 'data-structured-line-input',
      lineCheckboxAttr: 'data-structured-line-checkbox',
      lineRowAttr: 'data-structured-statement-row',
      onChange: () => {
        ensureSectionHasEmptyRow(sectionContainer);
        syncGeneratedStructuredDraftFromDom();
        syncStructuredContextHiddenInputs();
        handleStructuredContextChanged();
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = 'Select the statements you want to copy.';
        }
      },
      onAddAfter: (currentRow) => {
        const nextRow = addGeneratedStructuredLine(sectionContainer, '', currentRow, true);
        focusStatementEditor(nextRow?.querySelector('[data-structured-line-input]'));
      },
      onEmptyDelete: (currentRow) => {
        const previousRow = getAdjacentStatementRow(currentRow, 'previous');
        if (!(previousRow instanceof HTMLElement)) {
          return;
        }
        currentRow.remove();
        ensureSectionHasEmptyRow(sectionContainer);
        syncGeneratedStructuredDraftFromDom();
        syncStructuredContextHiddenInputs();
        handleStructuredContextChanged();
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = 'Select the statements you want to copy.';
        }
        focusStatementEditor(previousRow.querySelector('[data-structured-line-input]'));
      },
    });
    if (afterRow && afterRow.parentNode === rows) {
      afterRow.insertAdjacentElement('afterend', row);
    } else {
      rows.appendChild(row);
    }
    if (options.focus === true) {
      focusStatementEditor(row.querySelector('[data-structured-line-input]'));
    }
    return row;
  };

  const renderStructuredSections = (draft) => {
    if (!dom.generatedStructuredSections || !dom.generatedStructuredPanel) return;
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

      const title = document.createElement('h3');
      title.className = 'structured-section-title';
      title.textContent = section.sectionLabel || 'Section';

      const body = document.createElement('div');
      body.className = 'structured-statement-list';
      body.setAttribute('data-generated-structured-section-rows', '');

      card.appendChild(title);
      card.appendChild(body);
      dom.generatedStructuredSections.appendChild(card);
      const sectionLines = Array.isArray(section.lines) ? section.lines : [];
      if (sectionLines.length > 0) {
        sectionLines.forEach((line) => {
          addGeneratedStructuredLine(card, line.text || '', null, line.checked !== false);
        });
      } else {
        addGeneratedStructuredLine(card, '', null, true);
      }
    });
    syncGeneratedStructuredDraftFromDom();
    syncStructuredContextHiddenInputs();
    syncStructuredEditorAvailability();
    dom.generatedStructuredPanel.hidden = false;
  };

  const renderGeneratedOutput = (generatedDocument, structuredContext = {}) => {
    if (!dom.latestGeneratedOutput || !dom.generatedStructuredPanel) return;
    dom.generatedStructuredPanel.hidden = true;
    if (dom.generatedStructuredSections) dom.generatedStructuredSections.innerHTML = '';
    if (dom.structuredCopyStatus) dom.structuredCopyStatus.textContent = 'Select the statements you want to copy.';
    dom.latestGeneratedOutput.hidden = false;
    const shouldShowStructuredEditor = generatedDocument
      ? generatedDocument.status === 'ready' && generatedDocument.document_mode === 'structured'
      : selectedOutputTemplateMode() === 'structured';
    if (shouldShowStructuredEditor) {
      generatedStructuredDraft = buildGeneratedStructuredDraft(generatedDocument, structuredContext);
      dom.latestGeneratedOutput.hidden = true;
      renderStructuredSections(generatedStructuredDraft);
      return;
    }
    generatedStructuredDraft = null;
    if (!generatedDocument) {
      dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">No note yet.</span>';
      return;
    }
    if (generatedDocument.status === 'ready' && generatedDocument.edited_output_text_encrypted) {
      dom.latestGeneratedOutput.textContent = generatedDocument.edited_output_text_encrypted;
      return;
    }
    if (generatedDocument.status === 'queued') {
      dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">Your note is waiting to be written.</span>';
      return;
    }
    if (generatedDocument.status === 'processing') {
      dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">Your note is being written.</span>';
      return;
    }
    if (generatedDocument.status === 'failed') {
      dom.latestGeneratedOutput.innerHTML = `<span class="text-slate">The latest note could not be created${generatedDocument.error_message ? `: ${generatedDocument.error_message}` : ''}.</span>`;
      return;
    }
    dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">No note yet.</span>';
  };

  const syncStructuredTemplateUi = () => {
    const isStructuredTemplate = selectedOutputTemplateMode() === 'structured';
    if (dom.templateModeBadge) {
      dom.templateModeBadge.textContent = isStructuredTemplate ? 'Sectioned note' : 'Free text note';
    }
    if (isStructuredTemplate) {
      if (!generatedStructuredDraft) {
        generatedStructuredDraft = buildGeneratedStructuredDraft(null, collectStructuredContext());
      }
      renderStructuredSections(generatedStructuredDraft);
      if (dom.latestGeneratedOutput) {
        dom.latestGeneratedOutput.hidden = true;
      }
    } else if (!dom.latestGeneratedOutput?.dataset.latestGeneratedId) {
      if (dom.generatedStructuredPanel) {
        dom.generatedStructuredPanel.hidden = true;
      }
      if (dom.latestGeneratedOutput) {
        dom.latestGeneratedOutput.hidden = false;
        dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">No note yet.</span>';
      }
    }
    syncGenerationAvailability(getDraftText() || '');
  };

  const bootstrapFromDom = () => {
    if (dom.generatedStructuredPanel && !dom.generatedStructuredPanel.hidden) {
      generatedStructuredDraft = buildGeneratedStructuredDraftFromDom();
      if (generatedStructuredDraft) {
        renderStructuredSections(generatedStructuredDraft);
      }
    }
  };

  const clearStructuredSelection = () => {
    document.querySelectorAll('[data-generated-structured-section] [data-structured-line-checkbox]').forEach((checkbox) => {
      checkbox.checked = false;
      syncStatementRowVisualState(checkbox.closest('[data-structured-statement-row]'));
    });
    if (dom.structuredCopyStatus) {
      dom.structuredCopyStatus.textContent = 'Selection cleared.';
    }
    syncGeneratedStructuredDraftFromDom();
  };

  return {
    bootstrapFromDom,
    buildGeneratedStructuredDraft,
    clearStructuredSelection,
    collectStructuredContext,
    getGeneratedStructuredDraft: () => generatedStructuredDraft,
    getLastSavedStructuredContext: () => lastSavedStructuredContext,
    renderGeneratedOutput,
    renderStructuredSections,
    selectedOutputTemplateMode,
    setGeneratedStructuredDraft: (draft) => {
      generatedStructuredDraft = draft;
    },
    setLastSavedStructuredContext: (value) => {
      lastSavedStructuredContext = value;
    },
    syncStatementRowVisualState,
    syncStructuredContextHiddenInputs,
    syncStructuredEditorAvailability,
    syncStructuredTemplateUi,
  };
}
