export function createDocumentNavigator({
  dom,
  helpers,
  getState,
  setState,
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
      ? (document?.source_quick_action_name || document?.title || "Saved instruction")
      : (document?.follow_up_prompt_text || document?.title || "Message")
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
    documents.forEach((document) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `assistant-subsection block w-full rounded-lg px-3 py-3 text-left transition ${document.id === selectedId ? "bg-teal-pale/35 border border-teal-muted/35" : "hover:bg-parchment/50"}`;
      card.dataset.documentId = document.id;
      card.dataset.documentKind = "note";
      card.innerHTML = `
        <div class="flex items-center justify-between gap-4">
          <div class="min-w-0">
            <div class="text-sm font-medium text-ink">${escapeHtml(noteDocumentLabel(document))}</div>
            <div class="text-xs text-slate mt-1">${escapeHtml(document.source_template_name || "Note layout output")} · ${escapeHtml(document.model_used || "model not shown")}</div>
          </div>
          <div class="text-xs text-slate text-right">${escapeHtml(document.status || "")}<br>${escapeHtml(document.created_at || "")}</div>
        </div>
      `;
      noteHistory.appendChild(card);
    });
  };

  const renderFollowupHistory = (documents, selectedId) => {
    if (!followupHistory) return;
    followupHistory.innerHTML = "";
    if (!documents.length) {
      followupHistory.innerHTML = '<div class="text-sm text-slate">No previous messages yet.</div>';
      return;
    }
    documents.forEach((document) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `assistant-subsection block w-full rounded-lg px-3 py-3 text-left transition ${document.id === selectedId ? "bg-teal-pale/35 border border-teal-muted/35" : "hover:bg-parchment/50"}`;
      card.dataset.documentId = document.id;
      card.dataset.documentKind = "followup";
      card.innerHTML = `
        <div class="flex items-center justify-between gap-4">
          <div class="min-w-0">
            <div class="text-sm font-medium text-ink">${escapeHtml(followupDocumentLabel(document))}</div>
            <div class="text-xs text-slate mt-1">${escapeHtml(document.model_used || "model not shown")} · ${escapeHtml((document.generator_type || "").replaceAll("_", " "))}</div>
          </div>
          <div class="text-xs text-slate text-right">${escapeHtml(document.status || "")}<br>${escapeHtml(document.created_at || "")}</div>
        </div>
      `;
      followupHistory.appendChild(card);
    });
  };

  const renderSelectedNote = () => {
    const state = getState();
    const selectedNote = selectedDocumentFromList(state.workspaceNoteDocuments, state.selectedNoteDocumentId);
    setState({ selectedNoteDocumentId: selectedNote?.id || null });
    if (latestGeneratedOutput) {
      latestGeneratedOutput.dataset.latestGeneratedStatus = selectedNote?.status || "";
      latestGeneratedOutput.dataset.latestGeneratedId = selectedNote?.id || "";
      renderGeneratedOutput(selectedNote, state.workspaceStructuredContext || {});
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
        : "No messages yet";
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
  };

  const selectDocumentFromUi = (kind, documentId) => {
    if (!documentId) return;
    if (kind === "note") {
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
