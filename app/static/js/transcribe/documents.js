import { workingNoteTargetId } from './noteTargets.js?v=20260520-working-note-template-guard';

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
    followupLlmRequestSlot,
  } = dom;
  const {
    escapeHtml,
    renderGeneratedOutput,
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

  const followupDocumentLabel = (document) => (
    document?.title || document?.source_quick_action_name || document?.follow_up_prompt_text || (
      document?.generator_type === "quick_action" ? "Quick action" : "Follow-up"
    )
  );

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
    container.innerHTML = "";
    documents.forEach((item) => {
      const button = window.document.createElement("button");
      button.type = "button";
      button.className = `document-switcher-button${item.id === selectedId ? " active" : ""}`;
      button.dataset.documentId = item.id;
      button.dataset.documentKind = kind;
      const label = item.kind === "working_note" ? "Working note" : (kind === "note" ? noteDocumentLabel(item) : followupDocumentLabel(item));
      button.title = label;
      const meta = item.kind === "working_note" ? "Your own notes used as context" : `${escapeHtml(item.status || "")} · ${escapeHtml(item.created_at || "")}`;
      button.innerHTML = `
        <span class="document-switcher-label">${escapeHtml(truncateSwitcherLabel(label))}</span>
        <span class="document-switcher-meta">${meta}</span>
      `;
      container.appendChild(button);
    });
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
          <div class="text-xs text-slate text-right">${escapeHtml(item.status || "")}<br>${escapeHtml(item.created_at || "")}</div>
        </div>
      `;
      noteHistory.appendChild(card);
    });
  };

  const renderFollowupHistory = (documents, selectedId) => {
    if (!followupHistory) return;
    followupHistory.innerHTML = "";
    if (!documents.length) {
      followupHistory.innerHTML = '<div class="followup-empty-v2">No follow-ups for this transcript yet.</div>';
      refreshIcons?.(followupHistory);
      return;
    }
    documents.forEach((item) => {
      const card = window.document.createElement("button");
      card.type = "button";
      card.className = `followup-recent-item-v2${item.id === selectedId ? " is-selected" : ""}`;
      card.dataset.documentId = item.id;
      card.dataset.documentKind = "followup";
      const title = followupDocumentLabel(item);
      const detail = item.follow_up_prompt_text || item.source_quick_action_name || "";
      card.innerHTML = `
        <span>${escapeHtml(title)}${detail && detail !== title ? `<small>${escapeHtml(detail)}</small>` : ""}</span>
        <span>${escapeHtml(item.created_at || "")} <i data-lucide="chevron-right"></i></span>
      `;
      followupHistory.appendChild(card);
    });
    refreshIcons?.(followupHistory);
  };

  const renderSelectedNote = ({ forcePreserveEditor = false } = {}) => {
    const state = getState();
    const documents = noteTargets(state);
    const selectedNote = selectedDocumentFromList(documents, state.selectedNoteDocumentId);
    const selectedNoteId = selectedNote?.id || '';
    const selectedEditorId = selectedNoteId || (state.hasActiveTranscript ? workingNoteTargetId(state.activeTranscriptId || '') : null);
    const preserveCurrentEditorRender = Boolean(
      forcePreserveEditor || shouldPreserveNoteEditorRender?.(selectedEditorId)
    );
    setState({ selectedNoteDocumentId: selectedEditorId });
    if (latestGeneratedOutput) {
      latestGeneratedOutput.dataset.latestGeneratedStatus = selectedNote?.status || "";
      latestGeneratedOutput.dataset.latestGeneratedId = selectedNoteId;
      latestGeneratedOutput.dataset.latestGeneratedMode = selectedNote?.document_mode || "";
      latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = selectedNote?.updated_at || "";
      latestGeneratedOutput.dataset.latestGeneratedKind = selectedNote?.kind || "generated_note";
      if (!preserveCurrentEditorRender) {
        renderGeneratedOutput(selectedNote, selectedNote?.kind === "working_note" ? {} : (state.workspaceStructuredContext || {}));
      }
    }
    if (noteMeta) {
      noteMeta.textContent = selectedNote?.kind === "working_note"
        ? "Working note · Your own notes used as context for generation."
        : (selectedNote
          ? `${noteDocumentLabel(selectedNote)} · ${selectedNote.model_used || "model not shown"} · ${selectedNote.status} · ${selectedNote.hallucination_check_bucket || "not_applicable"} · ${selectedNote.created_at}`
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
    renderNoteHistory(state.workspaceNoteDocuments, selectedNote?.id || null);
    const selectedGeneratedNote = selectedNote?.kind === "working_note" ? null : selectedNote;
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
      const title = selectedFollowup ? followupDocumentLabel(selectedFollowup) : "Generated follow-up";
      if (followupOutputTitle instanceof window.HTMLInputElement || followupOutputTitle instanceof window.HTMLTextAreaElement) {
        followupOutputTitle.value = title;
        followupOutputTitle.disabled = selectedFollowup?.status !== "ready";
      } else {
        followupOutputTitle.textContent = title;
      }
    }
    if (followupOutputSubtitle) {
      const kind = selectedFollowup?.generator_type === "quick_action" ? "Quick action" : "Follow-up";
      followupOutputSubtitle.textContent = selectedFollowup
        ? [kind, selectedFollowup.created_at || ""].filter(Boolean).join(", ")
        : "Select or generate a follow-up";
    }
    if (followupMeta) {
      followupMeta.textContent = selectedFollowup
        ? `${selectedFollowup.model_used || "model not shown"} · ${selectedFollowup.status} · ${selectedFollowup.created_at}`
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
    renderLlmRequestPanel(followupLlmRequestSlot, selectedFollowup);
    renderRedactionDebugPanel(followupRedactionSlot, selectedFollowup);
    dispatchLegacyWorkspaceSelection('followup', selectedFollowup);
  };

  const selectDocumentFromUi = async (kind, documentId) => {
    if (!documentId) return;
    if (kind === "note") {
      const state = getState();
      if (state.selectedNoteDocumentId === documentId) {
        return;
      }
      if (hasPendingGeneratedNoteEdits?.()) {
        const savedDocument = await persistNoteEditsSilently?.();
        if (!savedDocument) {
          return;
        }
      }
      clearNoteEditorDirty?.();
      setState({ selectedNoteDocumentId: documentId });
      renderSelectedNote();
      setTab("output");
      return;
    }
    const state = getState();
    if (state.selectedFollowupDocumentId === documentId) {
      return;
    }
    if (hasPendingGeneratedFollowupEdits?.()) {
      const savedDocument = await persistFollowupEditsSilently?.();
      if (!savedDocument) {
        return;
      }
    }
    clearFollowupEditorDirty?.();
    setState({ selectedFollowupDocumentId: documentId });
    renderSelectedFollowup();
    setTab("followups");
  };

  return {
    selectedDocumentFromList,
    selectDocumentFromUi,
    renderSelectedNote,
    renderSelectedFollowup,
  };
}
