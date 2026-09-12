import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';

export const generationLoadingHtml = ({
  label = 'note',
  message = "This usually takes a few seconds.<br>We're preparing your clinical note...",
} = {}) => `
  <div class="note-generation-loading" role="status" aria-live="polite">
    <div class="note-generation-loading__orbit" aria-hidden="true">
      <div class="note-generation-loading__ring"></div>
      <div class="note-generation-loading__dot-wrap"><div class="note-generation-loading__dot"></div></div>
      <div class="note-generation-loading__star">&#10022;</div>
    </div>
    <h2>${label === 'follow-up' ? 'Creating' : 'Generating'} your ${label}</h2>
    <p>${message}</p>
  </div>
`;

export const formatWorkspaceCreatedAt = (value) => {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(timestamp));
};

const canonicalNoteVersionMarker = (value) => {
  const marker = String(value || '');
  const timestamp = Date.parse(marker);
  return Number.isFinite(timestamp) ? String(timestamp) : marker;
};

export const createInitialNoteRenderPreserver = (initialRender = {}) => {
  const initial = {
    targetId: String(initialRender.targetId || ''),
    documentMode: String(initialRender.documentMode || ''),
    kind: String(initialRender.kind || ''),
    status: String(initialRender.status || ''),
    updatedAt: canonicalNoteVersionMarker(initialRender.updatedAt),
  };
  let pending = Boolean(
    initialRender.hasEditorDom
    && initial.targetId
    && initial.documentMode
    && initial.kind
    && initial.status === 'ready'
    && initial.updatedAt
  );

  return (nextRender = {}) => {
    if (!pending) return false;
    pending = false;
    return (
      String(nextRender.targetId || '') === initial.targetId
      && String(nextRender.documentMode || '') === initial.documentMode
      && String(nextRender.kind || '') === initial.kind
      && String(nextRender.status || '') === initial.status
      && canonicalNoteVersionMarker(nextRender.updatedAt) === initial.updatedAt
    );
  };
};

const structuredDefinitionsSnapshot = (definitions = []) => {
  const sections = (Array.isArray(definitions) ? definitions : [])
    .map((section, index) => {
      const sectionKey = section?.section_key || section?.key || '';
      if (!sectionKey) return null;
      return {
        section_key: sectionKey,
        section_label: section?.section_label || section?.label || sectionKey.replaceAll('_', ' '),
        section_order: Number.isInteger(section?.section_order) ? section.section_order : index,
      };
    })
    .filter(Boolean);
  return sections.length ? { sections } : null;
};

export function workingNoteToEditorDocument({ transcriptId, workingNote, selectedTemplateMode, structuredSectionDefinitions = [] }) {
  if (!transcriptId) return null;
  const note = workingNote || {};
  const mode = note.mode || selectedTemplateMode || 'freeform';
  const sections = note.structured_note?.sections || {};
  const sectionDefinitionsSnapshot = mode === 'structured'
    ? structuredDefinitionsSnapshot(structuredSectionDefinitions)
    : null;
  return {
    id: workingNoteTargetId(transcriptId || ''),
    kind: 'working_note',
    title: 'Working note',
    status: 'ready',
    document_mode: mode === 'structured' ? 'structured' : 'freeform',
    mode_locked: Boolean(note.mode),
    edited_output_text: note.freeform_text || '',
    updated_at: note.updated_at || '',
    structured_section_definitions_json: sectionDefinitionsSnapshot,
    sections: Object.entries(sections).map(([sectionKey, lines], index) => ({
      section_key: sectionKey,
      section_label: sectionKey.replaceAll('_', ' '),
      section_order: index,
      text: Array.isArray(lines) ? lines.join('\n') : '',
    })),
  };
}

export function createDocumentNavigator({
  dom,
  helpers,
  getState,
  setState,
  clearNoteEditorDirty,
  hasPendingGeneratedNoteEdits,
  persistNoteEditsSilently,
  shouldPreserveNoteEditorRender,
  shouldInitializeHydratedNoteEditor,
  clearFollowupEditorDirty,
  hasPendingGeneratedFollowupEdits,
  persistFollowupEditsSilently,
  shouldPreserveFollowupEditorRender,
}) {
  const {
    noteSelectorWrap,
    noteSelector,
    noteSelectorCount,
    followupSelectorWrap,
    followupSelector,
    followupSelectorCount,
    noteSelectorScrollPrev,
    noteSelectorScrollNext,
    followupOutputTitle,
    followupOutputSubtitle,
    noteMeta,
    followupMeta,
    noteHistory,
    followupHistory,
    latestGeneratedOutput,
    latestFollowupOutput,
    outputRedactionSlot,
    followupRedactionSlot,
    outputLlmRequestSlot,
  } = dom;
  const {
    escapeHtml,
    renderGeneratedOutput,
    initializeHydratedGeneratedDocument,
    renderFollowupOutput,
    renderPiiEntities,
    renderRedactionDebugPanel,
    refreshIcons,
    setTab,
  } = helpers;

  const selectedDocumentFromList = (documents, selectedId) => {
    if (!Array.isArray(documents) || documents.length === 0) {
      return null;
    }
    return documents.find((document) => document.id === selectedId) || documents[0] || null;
  };

  const noteDocumentLabel = (document) => document?.title || document?.source_template_name || "Untitled note";

  const isSplitPlaceholder = (document) => document?.kind === 'split_placeholder' || document?.split_placeholder === true;

  const followupDocumentLabel = (document) => {
    const title = String(document?.title || '');
    if (document?.generator_type === 'followup' && title.startsWith('Follow-up:')) {
      return 'Custom follow-up';
    }
    if (document?.generator_type === 'quick_action' && title.startsWith('Quick action:') && document?.source_quick_action_name) {
      return document.source_quick_action_name;
    }
    return title || document?.source_quick_action_name || 'Custom follow-up';
  };

  noteSelector?.querySelectorAll?.('[data-note-created-at]').forEach((node) => {
    node.textContent = formatWorkspaceCreatedAt(node.dataset.noteCreatedAt);
  });

  const truncateSwitcherLabel = (value, maxWords = 4) => {
    const words = String(value || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) {
      return "";
    }
    if (words.length <= maxWords) {
      return words.join(" ");
    }
    return `${words.slice(0, maxWords).join(" ")}…`;
  };

  const dispatchLegacyWorkspaceSelection = (kind, document) => {
    if (!window.document.querySelector('[data-legacy-note-workspace]')) {
      return;
    }
    window.document.dispatchEvent(new window.CustomEvent('openscribe:legacy-workspace-document-selected', {
      detail: {
        kind,
        document: document || null,
      },
    }));
  };

  const workingNoteDocument = (state) => {
    if (!state.hasActiveTranscript) return null;
    return workingNoteToEditorDocument({
      transcriptId: state.activeTranscriptId || '',
      workingNote: state.activeWorkingNote || {},
      selectedTemplateMode: state.selectedTemplateMode,
      structuredSectionDefinitions: state.structuredSectionDefinitions || [],
    });
  };

  const noteTargets = (state) => [
    workingNoteDocument(state),
    ...(Array.isArray(state.workspaceNoteDocuments) ? state.workspaceNoteDocuments : []),
  ].filter(Boolean);

  const renderDocumentSelector = ({ wrap, container, countNode, documents, selectedId, kind }) => {
    if (!wrap || !container) return;
    wrap.hidden = !documents.length;
    if (countNode) {
      const count = documents.length;
      countNode.textContent = `${count} item${count === 1 ? "" : "s"}`;
    }
    const existingChildren = kind === 'note' ? [...container.children] : [];
    const hasUnkeyedExistingItems = existingChildren.some((child) => !child.dataset.documentRenderKey);
    const existingItems = kind === 'note' && !hasUnkeyedExistingItems
      ? new Map(existingChildren.map((child) => [child.dataset.documentRenderKey, child]))
      : new Map();
    if (hasUnkeyedExistingItems) container.innerHTML = "";
    if (kind !== 'note') container.innerHTML = "";
    const renderedKeys = new Set();
    documents.forEach((item) => {
      const itemWrap = kind === 'note' ? window.document.createElement('div') : null;
      if (itemWrap) {
        itemWrap.className = 'document-switcher-item';
        itemWrap.dataset.documentRenderKey = item.split_slot_key || item.id;
      }
      const renderKey = item.split_slot_key || item.id;
      const stableItemWrap = itemWrap ? (existingItems.get(renderKey) || itemWrap) : null;
      if (stableItemWrap) renderedKeys.add(renderKey);
      const button = window.document.createElement("button");
      button.type = "button";
      button.className = `document-switcher-button${item.id === selectedId ? " active" : ""}`;
      button.dataset.documentId = item.id;
      button.dataset.documentKind = kind;
      if (item.split_slot_key) button.dataset.documentSlotKey = item.split_slot_key;
      const label = item.kind === "working_note" ? "Working note" : (kind === "note" ? noteDocumentLabel(item) : followupDocumentLabel(item));
      button.title = label;
      const meta = item.kind === "working_note"
        ? "Your own notes used as context"
        : (isSplitPlaceholder(item)
          ? escapeHtml(item.split_generation_status_label || item.status || 'Processing')
          : `${escapeHtml(item.status || "")} · ${escapeHtml(formatWorkspaceCreatedAt(item.created_at))}`);
      button.innerHTML = `
        <span class="document-switcher-label">${escapeHtml(truncateSwitcherLabel(label))}</span>
        <span class="document-switcher-meta">${meta}</span>
      `;
      if (stableItemWrap) {
        if (typeof stableItemWrap.replaceChildren === 'function') stableItemWrap.replaceChildren();
        else {
          stableItemWrap.innerHTML = '';
          if (Array.isArray(stableItemWrap.children)) stableItemWrap.children.length = 0;
        }
        const itemActions = [button];
        if (item.kind !== 'working_note' && !isSplitPlaceholder(item)) {
          const regenerateButton = window.document.createElement('button');
          regenerateButton.type = 'button';
          regenerateButton.className = 'document-switcher-item__regenerate';
          regenerateButton.dataset.noteRegenerate = 'true';
          regenerateButton.dataset.documentId = item.id;
          regenerateButton.title = `Regenerate ${label.toLowerCase()}`;
          regenerateButton.setAttribute('aria-label', regenerateButton.title);
          regenerateButton.setAttribute('aria-haspopup', 'dialog');
          regenerateButton.setAttribute('aria-expanded', 'false');
          if (item.status === 'queued' || item.status === 'processing') {
            regenerateButton.disabled = true;
            regenerateButton.title = 'This note is still being generated';
            regenerateButton.setAttribute('aria-label', regenerateButton.title);
          }
          regenerateButton.innerHTML = '<i class="w-3.5 h-3.5" data-lucide="rotate-ccw" aria-hidden="true"></i>';
          itemActions.push(regenerateButton);
        }
        const deleteButton = window.document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'document-switcher-item__delete';
        deleteButton.dataset.noteHoverDelete = 'true';
        deleteButton.dataset.documentId = item.id;
        deleteButton.title = `Delete ${label.toLowerCase()} permanently`;
        deleteButton.setAttribute('aria-label', deleteButton.title);
        deleteButton.innerHTML = '<i class="w-3.5 h-3.5" data-lucide="trash-2" aria-hidden="true"></i>';
        stableItemWrap.append(...itemActions, deleteButton);
        container.appendChild(stableItemWrap);
      } else {
        container.appendChild(button);
      }
    });
    if (kind === 'note') {
      existingItems.forEach((node, key) => {
        if (!renderedKeys.has(key)) node.remove();
      });
    }
    refreshIcons?.(container);
    const syncScrollButtons = () => {
      if (!noteSelectorScrollPrev || !noteSelectorScrollNext || kind !== 'note') return;
      const maxScroll = Math.max(0, container.scrollWidth - container.clientWidth);
      noteSelectorScrollPrev.disabled = container.scrollLeft <= 2;
      noteSelectorScrollNext.disabled = container.scrollLeft >= maxScroll - 2;
      const hasOverflow = maxScroll > 2;
      noteSelectorScrollPrev.hidden = !hasOverflow;
      noteSelectorScrollNext.hidden = !hasOverflow;
    };
    if (kind === 'note' && typeof container.addEventListener === 'function' && !container.dataset.scrollControlsBound) {
      container.dataset.scrollControlsBound = 'true';
      container.tabIndex = 0;
      container.setAttribute('aria-label', 'Note versions');
      container.addEventListener('scroll', syncScrollButtons, { passive: true });
      container.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        container.scrollBy?.({ left: event.key === 'ArrowRight' ? 280 : -280, behavior: 'smooth' });
      });
      noteSelectorScrollPrev?.addEventListener?.('click', () => container.scrollBy?.({ left: -280, behavior: 'smooth' }));
      noteSelectorScrollNext?.addEventListener?.('click', () => container.scrollBy?.({ left: 280, behavior: 'smooth' }));
    }
    if (kind === 'note') window.requestAnimationFrame?.(syncScrollButtons);
  };

  const renderLlmRequestPanel = (slot, document) => {
    if (!slot) return;
    slot.innerHTML = '';
    if (!document) return;
    const checkBucket = document.hallucination_check_bucket || 'not_applicable';
    if (checkBucket !== 'not_applicable' || document.hallucination_check_debug_json) {
      const checkWrapper = window.document.createElement('section');
      checkWrapper.className = 'followup-output-card-v2 followup-llm-request-card-v2';
      checkWrapper.dataset.hallucinationCheckDebugPanel = 'true';
      const debugPayload = document.hallucination_check_debug_json
        ? `<pre class="followup-llm-request-pre-v2">${escapeHtml(JSON.stringify(document.hallucination_check_debug_json, null, 2))}</pre>`
        : '<p class="text-xs text-slate">Debug payload not available. Set HALLUCINATION_CHECK_DEBUG_UI=1 before generating the note to capture first-pass output and checker edits.</p>';
      checkWrapper.innerHTML = `
        <div class="followup-output-card-v2__meta"><span class="followup-status">Hallucination check</span><span>${escapeHtml(checkBucket)}</span></div>
        ${debugPayload}
      `;
      slot.appendChild(checkWrapper);
    }
    const wrapper = window.document.createElement('section');
    wrapper.className = 'followup-output-card-v2 followup-llm-request-card-v2';
    wrapper.dataset.llmRequestPanel = 'true';
    wrapper.dataset.generatedDocumentId = document.id || '';
    wrapper.hidden = true;

    const payload = document.llm_request_payload_json || null;
    const body = payload
      ? escapeHtml(JSON.stringify(payload, null, 2))
      : 'LLM request not available for this document.';
    wrapper.innerHTML = `
      <div class="followup-output-card-v2__meta"><span class="followup-status">LLM request</span><span>${escapeHtml(document.created_at || '')}</span></div>
      <pre class="followup-llm-request-pre-v2">${body}</pre>
    `;
    slot.appendChild(wrapper);
  };

  const renderNoteHistory = (documents, selectedId) => {
    if (!noteHistory) return;
    documents = (Array.isArray(documents) ? documents : []).filter((item) => !isSplitPlaceholder(item));
    noteHistory.innerHTML = "";
    if (!documents.length) {
      noteHistory.innerHTML = '<div class="text-sm text-slate">No note history yet.</div>';
      return;
    }
    documents.forEach((item) => {
      const card = window.document.createElement("button");
      card.type = "button";
      card.className = `assistant-subsection block w-full rounded-lg px-3 py-3 text-left transition ${item.id === selectedId ? "bg-teal-pale/35 border border-teal-muted/35" : "hover:bg-parchment/50"}`;
      card.dataset.documentId = item.id;
      card.dataset.documentKind = "note";
      card.innerHTML = `
        <div class="flex items-center justify-between gap-4">
          <div class="min-w-0">
            <div class="text-sm font-medium text-ink">${escapeHtml(noteDocumentLabel(item))}</div>
            <div class="text-xs text-slate mt-1">${escapeHtml(item.source_template_name || "Note layout output")} · ${escapeHtml(item.model_used || "model not shown")}</div>
          </div>
          <div class="text-xs text-slate text-right">${escapeHtml(item.status || "")}<br>${escapeHtml(formatWorkspaceCreatedAt(item.created_at))}</div>
        </div>
      `;
      noteHistory.appendChild(card);
    });
  };

  const renderFollowupHistory = (documents, selectedId) => {
    if (!followupHistory) return;
    followupHistory.innerHTML = "";
    if (!documents.length) {
      followupHistory.innerHTML = '<div class="followup-empty-v2">No follow-ups yet.</div><div class="followup-empty-v2" data-followup-history-no-results hidden>No follow-ups match your search.</div>';
      refreshIcons?.(followupHistory);
      window.document.dispatchEvent(new window.CustomEvent('transcribe:followup-history-rendered'));
      return;
    }
    documents.forEach((item) => {
      const card = window.document.createElement("div");
      card.className = `followup-history-item-v3${item.id === selectedId ? " is-selected" : ""}`;
      card.dataset.documentId = item.id;
      card.dataset.documentKind = "followup";
      const title = followupDocumentLabel(item);
      card.dataset.followupSearchText = `${title} ${item.source_quick_action_name || ''}`;
      card.innerHTML = `
        <button type="button" class="followup-history-item-v3__select" data-followup-history-select ${item.id === selectedId ? 'aria-current="true"' : ''} aria-label="Open ${escapeHtml(title)}, created ${escapeHtml(formatWorkspaceCreatedAt(item.created_at))}">
          <span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(formatWorkspaceCreatedAt(item.created_at))}</small></span>
        </button>
        <details class="followup-history-menu-v3" data-followup-history-menu>
          <summary aria-label="More actions for ${escapeHtml(title)}" aria-haspopup="menu" aria-expanded="false" title="More actions"><i data-lucide="ellipsis-vertical" aria-hidden="true"></i></summary>
          <div role="menu">
            <button type="button" role="menuitem" data-followup-copy>Copy</button>
            <button type="button" role="menuitem" class="followup-history-menu-v3__delete" data-followup-delete data-generated-document-id="${escapeHtml(item.id || '')}">Delete</button>
          </div>
        </details>
      `;
      followupHistory.appendChild(card);
    });
    followupHistory.insertAdjacentHTML('beforeend', '<div class="followup-empty-v2" data-followup-history-no-results hidden>No follow-ups match your search.</div>');
    refreshIcons?.(followupHistory);
    window.document.dispatchEvent(new window.CustomEvent('transcribe:followup-history-rendered'));
  };

  const renderSelectedNote = ({ forcePreserveEditor = false } = {}) => {
    const state = getState();
    const documents = noteTargets(state);
    const selectedNote = selectedDocumentFromList(documents, state.selectedNoteDocumentId);
    const selectedNoteId = selectedNote?.id || '';
    const selectedEditorId = selectedNoteId || (state.hasActiveTranscript ? workingNoteTargetId(state.activeTranscriptId || '') : null);
    const preserveCurrentEditorRender = Boolean(
      forcePreserveEditor || shouldPreserveNoteEditorRender?.(selectedEditorId, selectedNote)
    );
    setState({
      selectedNoteDocumentId: selectedEditorId,
      selectedNoteSlotKey: selectedNote?.split_slot_key || null,
    });
    if (latestGeneratedOutput) {
      latestGeneratedOutput.dataset.latestGeneratedStatus = selectedNote?.status || "";
      latestGeneratedOutput.dataset.latestGeneratedId = selectedNoteId;
      latestGeneratedOutput.dataset.latestGeneratedMode = selectedNote?.document_mode || "";
      latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = selectedNote?.updated_at || "";
      latestGeneratedOutput.dataset.latestGeneratedKind = selectedNote?.kind || "generated_note";
      if (preserveCurrentEditorRender && shouldInitializeHydratedNoteEditor?.(selectedNote)) {
        initializeHydratedGeneratedDocument?.(selectedNote);
      } else if (!preserveCurrentEditorRender) {
        renderGeneratedOutput(selectedNote, selectedNote?.kind === "working_note" ? {} : (state.workspaceStructuredContext || {}));
      }
    }
    if (noteMeta) {
      noteMeta.textContent = selectedNote?.kind === "working_note"
        ? "Working note · Your own notes used as context for generation."
        : (selectedNote
          ? `${noteDocumentLabel(selectedNote)} · ${selectedNote.model_used || "model not shown"} · ${selectedNote.status} · ${selectedNote.hallucination_check_bucket || "not_applicable"} · ${formatWorkspaceCreatedAt(selectedNote.created_at)}`
          : "No note yet.");
    }
    renderDocumentSelector({
      wrap: noteSelectorWrap,
      container: noteSelector,
      countNode: noteSelectorCount,
      documents,
      selectedId: selectedNote?.id || (state.hasActiveTranscript ? workingNoteTargetId(state.activeTranscriptId || '') : null),
      kind: "note",
    });
    renderNoteHistory(state.workspaceNoteHistoryDocuments || state.workspaceNoteDocuments, selectedNote?.id || null);
    const selectedGeneratedNote = selectedNote?.kind === "working_note" || isSplitPlaceholder(selectedNote) ? null : selectedNote;
    renderLlmRequestPanel(outputLlmRequestSlot, selectedGeneratedNote);
    renderRedactionDebugPanel(outputRedactionSlot, selectedGeneratedNote);
    dispatchLegacyWorkspaceSelection('note', selectedNote);
    return { preservedEditor: preserveCurrentEditorRender, selectedNote };
  };

  const renderSelectedFollowup = ({ preserveEditor = false } = {}) => {
    const state = getState();
    const selectedFollowup = selectedDocumentFromList(state.workspaceFollowupDocuments, state.selectedFollowupDocumentId);
    setState({ selectedFollowupDocumentId: selectedFollowup?.id || null });
    if (latestFollowupOutput) {
      latestFollowupOutput.dataset.latestFollowupStatus = selectedFollowup?.status || "";
      latestFollowupOutput.dataset.latestFollowupId = selectedFollowup?.id || "";
      latestFollowupOutput.dataset.latestFollowupUpdatedAt = selectedFollowup?.updated_at || "";
      if (!preserveEditor && !shouldPreserveFollowupEditorRender?.(selectedFollowup?.id || '')) {
        renderFollowupOutput(selectedFollowup);
      }
    }
    if (followupOutputTitle && !preserveEditor) {
      const title = selectedFollowup ? followupDocumentLabel(selectedFollowup) : "Custom follow-up";
      if (followupOutputTitle instanceof window.HTMLInputElement || followupOutputTitle instanceof window.HTMLTextAreaElement) {
        followupOutputTitle.value = title;
        followupOutputTitle.disabled = selectedFollowup?.status !== "ready";
      } else {
        followupOutputTitle.textContent = title;
      }
    }
    if (followupOutputSubtitle) {
      followupOutputSubtitle.textContent = selectedFollowup
        ? (selectedFollowup.created_at ? `Created ${formatWorkspaceCreatedAt(selectedFollowup.created_at)}` : "Created")
        : "No follow-up selected";
    }
    if (followupMeta) {
      followupMeta.textContent = selectedFollowup
        ? `${followupDocumentLabel(selectedFollowup)} · ${selectedFollowup.status} · ${formatWorkspaceCreatedAt(selectedFollowup.created_at)}`
        : "No follow-ups yet";
    }
    renderDocumentSelector({
      wrap: followupSelectorWrap,
      container: followupSelector,
      countNode: followupSelectorCount,
      documents: state.workspaceFollowupDocuments,
      selectedId: selectedFollowup?.id || null,
      kind: "followup",
    });
    renderFollowupHistory(state.workspaceFollowupDocuments, selectedFollowup?.id || null);
    renderRedactionDebugPanel(followupRedactionSlot, selectedFollowup);
    dispatchLegacyWorkspaceSelection('followup', selectedFollowup);
  };

  const selectDocumentFromUi = async (kind, documentId) => {
    if (!documentId) return false;
    if (kind === "note") {
      const state = getState();
      if (state.selectedNoteDocumentId === documentId) {
        return true;
      }
      if (hasPendingGeneratedNoteEdits?.()) {
        const savedDocument = await persistNoteEditsSilently?.();
        if (!savedDocument) {
          return false;
        }
      }
      clearNoteEditorDirty?.();
      const selectedDocument = noteTargets(state).find((document) => document.id === documentId) || null;
      setState({ selectedNoteDocumentId: documentId });
      setState({ selectedNoteSlotKey: selectedDocument?.split_slot_key || null });
      renderSelectedNote();
      setTab("output");
      window.document.dispatchEvent(new window.CustomEvent('transcribe:document-selected', { detail: { kind: 'note', documentId } }));
      return true;
    }
    const state = getState();
    if (state.selectedFollowupDocumentId === documentId) {
      return true;
    }
    if (hasPendingGeneratedFollowupEdits?.()) {
      const savedDocument = await persistFollowupEditsSilently?.();
      if (!savedDocument) {
        return false;
      }
    }
    clearFollowupEditorDirty?.();
    setState({ selectedFollowupDocumentId: documentId });
    renderSelectedFollowup();
    setTab("followups");
    window.document.dispatchEvent(new window.CustomEvent('transcribe:document-selected', { detail: { kind: 'followup', documentId } }));
    return true;
  };

  return {
    selectedDocumentFromList,
    selectDocumentFromUi,
    renderSelectedNote,
    renderSelectedFollowup,
  };
}
