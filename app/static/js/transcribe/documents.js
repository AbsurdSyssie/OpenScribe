export function createDocumentNavigator({
  dom,
  helpers,
  getState,
  setState,
  clearNoteEditorDirty,
  hasPendingGeneratedNoteEdits,
  persistNoteEditsSilently,
  shouldPreserveNoteEditorRender,
}) {
  const {
    noteSelectorWrap,
    noteSelector,
    noteSelectorCount,
    followupSelectorWrap,
    followupSelector,
    followupSelectorCount,
    noteMeta,
    followupMeta,
    noteHistory,
    followupHistory,
    latestGeneratedOutput,
    latestFollowupOutput,
    outputRedactionSlot,
    followupRedactionSlot,
  } = dom;
  const {
    escapeHtml,
    renderGeneratedOutput,
    renderFollowupOutput,
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
    document?.generator_type === "quick_action"
      ? (document?.source_quick_action_name || document?.title || "Quick action")
      : (document?.follow_up_prompt_text || document?.title || "Follow-up")
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

  const renderDocumentSelector = ({ wrap, container, countNode, documents, selectedId, kind }) => {
    if (!wrap || !container) return;
    wrap.hidden = !documents.length;
    if (countNode) {
      countNode.textContent = `${documents.length} item${documents.length === 1 ? "" : "s"}`;
    }
    container.innerHTML = "";
    documents.forEach((item) => {
      const button = window.document.createElement("button");
      button.type = "button";
      button.className = `document-switcher-button${item.id === selectedId ? " active" : ""}`;
      button.dataset.documentId = item.id;
      button.dataset.documentKind = kind;
      const label = kind === "note" ? noteDocumentLabel(item) : followupDocumentLabel(item);
      button.title = label;
      button.innerHTML = `
        <span class="document-switcher-label">${escapeHtml(truncateSwitcherLabel(label))}</span>
        <span class="document-switcher-meta">${escapeHtml(item.status || "")} · ${escapeHtml(item.created_at || "")}</span>
      `;
      container.appendChild(button);
    });
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
      followupHistory.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">
            <i class="w-12 h-12 text-slate/40" data-lucide="message-square-more"></i>
          </div>
          <div class="empty-state__text">Select a quick action or describe what you need to create a follow-up message.</div>
        </div>
      `;
      refreshIcons?.(followupHistory);
      return;
    }
    documents.forEach((item) => {
      const card = window.document.createElement("div");
      card.className = `followup-card${item.id === selectedId ? " followup-card--active" : ""}`;
      card.role = "button";
      card.tabIndex = 0;
      card.dataset.documentId = item.id;
      card.dataset.documentKind = "followup";
      const body = item.status === "ready" && item.edited_output_text_encrypted
        ? `<div class="followup-card__content" data-followup-copy-body>${escapeHtml(item.edited_output_text_encrypted)}</div>`
        : item.status === "queued"
          ? '<div class="followup-card__content followup-card__content--placeholder">Waiting to be written...</div>'
          : item.status === "processing"
            ? '<div class="followup-card__content followup-card__content--placeholder">Being written...</div>'
            : item.status === "failed"
              ? `<div class="followup-card__content followup-card__content--error">Failed${item.error_message ? `: ${escapeHtml(item.error_message)}` : ""}</div>`
              : "";
      const typeLabel = item.generator_type === "quick_action" ? "Quick action" : "Custom";
      const title = item.generator_type === "quick_action"
        ? (item.source_quick_action_name || item.title || "Quick action")
        : followupDocumentLabel(item);
      const actions = `
        <div class="followup-card__actions">
          ${item.status === "ready" && item.edited_output_text_encrypted ? `
            <button type="button" class="btn-icon" data-followup-copy title="Copy to clipboard">
              <i class="w-4 h-4" data-lucide="copy"></i>
            </button>
          ` : ""}
          <button type="button" class="btn-icon" data-followup-delete data-generated-document-id="${escapeHtml(item.id || "")}" title="Delete permanently">
            <i class="w-4 h-4" data-lucide="trash-2"></i>
          </button>
        </div>
      `;
      card.innerHTML = `
        <div class="followup-card__header">
          <div class="followup-card__meta">
            <span class="followup-card__type">${escapeHtml(typeLabel)}</span>
            <span class="followup-card__name">${escapeHtml(title)}</span>
          </div>
          ${actions}
        </div>
        <div class="followup-card__status">
          <span class="followup-status followup-status--${escapeHtml(item.status || "")}">${escapeHtml(item.status || "")}</span>
          <span class="followup-card__date">${escapeHtml(item.created_at || "")}</span>
        </div>
        ${body}
      `;
      followupHistory.appendChild(card);
    });
    refreshIcons?.(followupHistory);
  };

  const renderSelectedNote = ({ preserveEditor = false } = {}) => {
    const state = getState();
    const selectedNote = selectedDocumentFromList(state.workspaceNoteDocuments, state.selectedNoteDocumentId);
    setState({ selectedNoteDocumentId: selectedNote?.id || null });
    if (latestGeneratedOutput) {
      latestGeneratedOutput.dataset.latestGeneratedStatus = selectedNote?.status || "";
      latestGeneratedOutput.dataset.latestGeneratedId = selectedNote?.id || "";
      latestGeneratedOutput.dataset.latestGeneratedMode = selectedNote?.document_mode || "";
      latestGeneratedOutput.dataset.latestGeneratedUpdatedAt = selectedNote?.updated_at || "";
      if (!preserveEditor && !shouldPreserveNoteEditorRender?.(selectedNote?.id || '')) {
        renderGeneratedOutput(selectedNote, state.workspaceStructuredContext || {});
      }
    }
    if (noteMeta) {
      noteMeta.textContent = selectedNote
        ? `${noteDocumentLabel(selectedNote)} · ${selectedNote.model_used || "model not shown"} · ${selectedNote.status} · ${selectedNote.created_at}`
        : "No note generated yet";
    }
    renderDocumentSelector({
      wrap: noteSelectorWrap,
      container: noteSelector,
      countNode: noteSelectorCount,
      documents: state.workspaceNoteDocuments,
      selectedId: selectedNote?.id || null,
      kind: "note",
    });
    renderNoteHistory(state.workspaceNoteDocuments, selectedNote?.id || null);
    renderRedactionDebugPanel(outputRedactionSlot, selectedNote);
    dispatchLegacyWorkspaceSelection('note', selectedNote);
  };

  const renderSelectedFollowup = () => {
    const state = getState();
    const selectedFollowup = selectedDocumentFromList(state.workspaceFollowupDocuments, state.selectedFollowupDocumentId);
    setState({ selectedFollowupDocumentId: selectedFollowup?.id || null });
    if (latestFollowupOutput) {
      latestFollowupOutput.dataset.latestFollowupStatus = selectedFollowup?.status || "";
      latestFollowupOutput.dataset.latestFollowupId = selectedFollowup?.id || "";
      renderFollowupOutput(selectedFollowup);
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
