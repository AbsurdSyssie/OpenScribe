export function createStructuredEditor({
  dom,
  structuredSectionDefinitions,
  getTranscriptId,
  getDraftText,
  syncGenerationAvailability,
  onNoteEditorChanged,
  persistStructuredContextSilently,
}) {
  let generatedStructuredDraft = null;
  let generatedFreeformDraft = null;
  let emisSaveTimer = null;
  let lastSavedStructuredContext = null;
  const rowSelectionState = new Map();

  const noteCopyStatusDefault = 'Select the note lines you want to copy.';

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
    row.classList.toggle('unchecked', Boolean(checkbox && !checkbox.checked));
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
    document.querySelectorAll('[data-freeform-note-checkbox]').forEach((input) => {
      input.disabled = disabled;
    });
    document.querySelectorAll('[data-freeform-note-input]').forEach((input) => {
      input.disabled = disabled;
    });
  };

  const syncNoteEmptyState = () => {
    const structuredHasText = Boolean(dom.generatedStructuredPanel && [...dom.generatedStructuredPanel.querySelectorAll('[data-structured-line-input]')].some((input) => String(input.value || '').trim().length > 0));
    const freeformHasText = Boolean(dom.generatedFreeformPanel && [...dom.generatedFreeformPanel.querySelectorAll('[data-freeform-note-input]')].some((input) => String(input.value || '').trim().length > 0));
    const selectedMode = selectedOutputTemplateMode();
    if (dom.structuredNoteEmptyState) {
      dom.structuredNoteEmptyState.hidden = selectedMode !== 'structured' || structuredHasText;
    }
    if (dom.freeformNoteEmptyState) {
      dom.freeformNoteEmptyState.hidden = selectedMode !== 'freeform' || freeformHasText;
    }
  };

  const scheduleStructuredContextSave = () => {
    if (selectedOutputTemplateMode() !== 'structured' || activeGeneratedDocumentId()) {
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
    onNoteEditorChanged?.();
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

    const checkboxLabel = document.createElement('div');
    checkboxLabel.className = 'statement-checkbox';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = checked;
    checkbox.disabled = disabled;
    checkbox.setAttribute(lineCheckboxAttr, '');
    checkbox.dataset.sectionLabel = sectionLabel;
    checkbox.setAttribute('aria-label', `Select ${sectionLabel} statement`);
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
    textarea.dataset.sectionLabel = sectionLabel;

    row.appendChild(checkboxLabel);
    content.appendChild(textarea);
    row.appendChild(content);

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
      focusStatementEditor(textarea);
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
    ) ? collectFreeformLinesFromText(generatedDocument.edited_output_text_encrypted || '') : [];
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
    const lineText = String(section.edited_text_encrypted || section.original_text_encrypted || '');
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
        rememberRowSelectionState({ mode: 'structured', sectionKey: section.dataset.sectionKey || '', lineIndex, checked: Boolean(checkbox?.checked) });
        return {
          text: textarea?.value || '',
          checked: Boolean(checkbox?.checked),
        };
      }),
    }));
  };

  const syncGeneratedFreeformDraftFromDom = () => {
    if (!generatedFreeformDraft || !dom.generatedFreeformRows) return;
    generatedFreeformDraft.lines = [...dom.generatedFreeformRows.querySelectorAll('[data-freeform-note-row]')].map((row, lineIndex) => {
      const checkbox = row.querySelector('[data-freeform-note-checkbox]');
      const textarea = row.querySelector('[data-freeform-note-input]');
      rememberRowSelectionState({ mode: 'freeform', lineIndex, checked: Boolean(checkbox?.checked) });
      return {
        text: textarea?.value || '',
        checked: Boolean(checkbox?.checked),
      };
    });
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
        syncGeneratedStructuredDraftFromDom();
        syncStructuredContextHiddenInputs();
        handleStructuredContextChanged();
        syncNoteEmptyState();
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
        }
      },
      onAddAfter: (currentRow) => {
        const nextRow = addGeneratedStructuredLine(sectionContainer, '', currentRow, true);
        window.requestAnimationFrame(() => {
          focusStatementEditor(nextRow?.querySelector('[data-structured-line-input]'));
        });
      },
      onEmptyDelete: (currentRow) => {
        const previousRow = getAdjacentStatementRow(currentRow, 'previous');
        if (!(previousRow instanceof HTMLElement)) {
          return;
        }
        currentRow.remove();
        ensureSectionHasEditableRow(sectionContainer);
        syncGeneratedStructuredDraftFromDom();
        syncStructuredContextHiddenInputs();
        handleStructuredContextChanged();
        syncNoteEmptyState();
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
        }
        window.requestAnimationFrame(() => {
          focusStatementEditor(previousRow.querySelector('[data-structured-line-input]'));
        });
      },
    });
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

  const addGeneratedFreeformLine = (value = '', afterRow = null, checked = true, options = {}) => {
    if (!dom.generatedFreeformRows) return null;
    const row = createStatementRow({
      sectionLabel: 'note',
      value,
      checked,
      disabled: false,
      placeholder: 'Add note line',
      lineInputAttr: 'data-freeform-note-input',
      lineCheckboxAttr: 'data-freeform-note-checkbox',
      lineRowAttr: 'data-freeform-note-row',
      onChange: () => {
        syncGeneratedFreeformDraftFromDom();
        syncNoteEmptyState();
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
        }
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
        currentRow.remove();
        ensureFreeformHasEditableRow();
        syncGeneratedFreeformDraftFromDom();
        syncNoteEmptyState();
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
        }
        window.requestAnimationFrame(() => {
          focusStatementEditor(previousRow.querySelector('[data-freeform-note-input], [data-structured-line-input]'));
        });
      },
    });
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

  const renderStructuredSections = (draft) => {
    if (!dom.generatedStructuredSections || !dom.generatedStructuredPanel) return;
    const focusState = captureEditorFocusState();
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

      const body = document.createElement('div');
      body.className = 'structured-statement-list';
      body.setAttribute('data-generated-structured-section-rows', '');

      card.appendChild(header);
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
    restoreEditorFocusState(focusState?.mode === 'structured' ? focusState : null);
  };

  const renderFreeformLines = (draft) => {
    if (!dom.generatedFreeformRows || !dom.generatedFreeformPanel) return;
    const focusState = captureEditorFocusState();
    dom.generatedFreeformRows.innerHTML = '';
    if (!draft || !Array.isArray(draft.lines) || draft.lines.length === 0) {
      dom.generatedFreeformPanel.hidden = true;
      return;
    }
    draft.lines.forEach((line, lineIndex) => {
      addGeneratedFreeformLine(line.text || '', null, readRememberedRowSelectionState({ mode: 'freeform', lineIndex, fallback: line.checked !== false }));
    });
    syncGeneratedFreeformDraftFromDom();
    syncStructuredEditorAvailability();
    dom.generatedFreeformPanel.hidden = false;
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
    if (dom.structuredCopyStatus) dom.structuredCopyStatus.textContent = noteCopyStatusDefault;
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
      syncNoteEditorToolbar();
      return;
    }
    if (shouldShowFreeformEditor) {
      generatedStructuredDraft = null;
      generatedFreeformDraft = buildFreeformDraft(generatedDocument);
      dom.latestGeneratedOutput.hidden = true;
      renderFreeformLines(generatedFreeformDraft);
      syncNoteEditorToolbar();
      return;
    }
    generatedStructuredDraft = null;
    generatedFreeformDraft = null;
    if (!generatedDocument) {
      syncStructuredTemplateUi();
      syncNoteEmptyState();
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'ready' && generatedDocument.edited_output_text_encrypted) {
      dom.latestGeneratedOutput.textContent = generatedDocument.edited_output_text_encrypted;
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'queued') {
      dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">Your note is waiting to be written.</span>';
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'processing') {
      dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">Your note is being written.</span>';
      syncNoteEditorToolbar();
      return;
    }
    if (generatedDocument.status === 'failed') {
      dom.latestGeneratedOutput.innerHTML = `<span class="text-slate">The latest note could not be created${generatedDocument.error_message ? `: ${generatedDocument.error_message}` : ''}.</span>`;
      syncNoteEditorToolbar();
      return;
    }
    dom.latestGeneratedOutput.innerHTML = '<span class="text-slate">No note yet.</span>';
    syncNoteEditorToolbar();
  };

  const syncStructuredTemplateUi = () => {
    const isStructuredTemplate = selectedOutputTemplateMode() === 'structured';
    const hasGeneratedNote = Boolean(dom.latestGeneratedOutput?.dataset.latestGeneratedId);
    const generatedMode = activeGeneratedDocumentMode();
    if (dom.templateModeBadge) {
      dom.templateModeBadge.textContent = isStructuredTemplate ? 'Sectioned note' : 'Free text note';
    }
    if (isStructuredTemplate) {
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
        edited_output_text_encrypted: collectSelectedNoteLines({ includeUnselected: true, mode: 'freeform' }).map((line) => line.text).join('\n'),
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
    syncNoteEmptyState();
    syncGenerationAvailability(getDraftText() || '');
  };

  const bootstrapFromDom = () => {
    if (dom.generatedStructuredPanel && !dom.generatedStructuredPanel.hidden) {
      generatedStructuredDraft = buildGeneratedStructuredDraftFromDom();
      if (generatedStructuredDraft) {
        renderStructuredSections(generatedStructuredDraft);
      }
    }
    if (dom.generatedFreeformPanel && !dom.generatedFreeformPanel.hidden) {
      generatedFreeformDraft = buildFreeformDraftFromDom();
      if (generatedFreeformDraft) {
        renderFreeformLines(generatedFreeformDraft);
      }
    }
    syncNoteEditorToolbar();
    syncNoteEmptyState();
  };

  const clearStructuredSelection = () => {
    document.querySelectorAll('[data-generated-structured-section] [data-structured-line-checkbox], [data-generated-freeform-panel] [data-freeform-note-checkbox]').forEach((checkbox) => {
      checkbox.checked = false;
      syncStatementRowVisualState(checkbox.closest('[data-structured-statement-row], [data-freeform-note-row]'));
    });
    if (dom.structuredCopyStatus) {
      dom.structuredCopyStatus.textContent = 'Selection cleared.';
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
      dom.structuredCopyStatus.textContent = 'Selection added.';
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

  const hasNoteInputContent = () => {
    return collectSelectedNoteLines({ includeUnselected: true }).some((line) => String(line.text || '').trim().length > 0);
  };

  return {
    bootstrapFromDom,
    buildGeneratedStructuredDraft,
    collectSelectedNoteLines,
    clearStructuredSelection,
    collectStructuredContext,
    getGeneratedStructuredDraft: () => generatedStructuredDraft,
    getLastSavedStructuredContext: () => lastSavedStructuredContext,
    hasNoteInputContent,
    renderGeneratedOutput,
    renderStructuredSections,
    selectedOutputTemplateMode,
    selectStructuredSelection,
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
