import { csrfFetch } from '../csrf.js';

export function attachTranscribeActions({
  dom,
  routeBase,
  getTranscriptId,
  getTranscriptText,
  getActiveIngestionMode,
  getIsLiveCaptureUiActive,
  getIsRecordingSwitchBlocked,
  selectDocumentFromUi,
  showFlash,
  showCopyToast,
  parseErrorMessage,
  fetchWorkspace,
  pollWorkspace,
  scheduleWorkspaceRefreshBurst,
  syncTranscriptTitleIfNeeded,
  persistPendingEditorsBeforeWorkspaceSwitch,
  enqueueTemplateGeneration,
  setVisibleStatus,
  setSessionProgress,
  setRetryAvailability,
  reflectBackendStatus,
  syncGenerationAvailability,
  persistUserAppPreferences,
  handleOutputTemplateChange,
  setMicButtons,
  setTab,
  structuredEditor,
  saveWorkingNoteBeforeGeneration,
  saveDictationBeforeGeneration,
  clearWorkingNote,
}) {
  const FOLLOWUP_HISTORY_OPEN_KEY = 'openscribe:transcribe:followup-history-open';
  let followupSubmitting = false;
  let followupRegenerating = false;
  let quickActionComboboxOpen = false;

  const followupCopyText = () => {
    const node = dom.latestFollowupOutput?.querySelector('[data-followup-copy-body]');
    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
      return node.value.trim();
    }
    return node?.textContent?.trim() || '';
  };

  const filteredQuickActionOptions = () => (dom.quickActionOptions || []).filter((option) => !option.hidden);

  const setQuickActionComboboxOpen = (open, { focusSearch = false } = {}) => {
    const next = Boolean(open && dom.quickActionComboboxPanel && !dom.quickActionComboboxToggle?.disabled);
    quickActionComboboxOpen = next;
    if (dom.quickActionComboboxPanel) dom.quickActionComboboxPanel.hidden = !next;
    dom.quickActionComboboxToggle?.setAttribute('aria-expanded', next ? 'true' : 'false');
    dom.quickActionSearchInput?.setAttribute('aria-expanded', next ? 'true' : 'false');
    if (!next) dom.quickActionSearchInput?.removeAttribute('aria-activedescendant');
    if (next && focusSearch) {
      dom.quickActionSearchInput?.focus();
      dom.quickActionSearchInput?.select();
    }
  };

  const filterQuickActionOptions = () => {
    const query = String(dom.quickActionSearchInput?.value || '').trim().toLowerCase();
    let visibleCount = 0;
    (dom.quickActionOptions || []).forEach((option) => {
      const haystack = `${option.dataset.quickActionName || ''} ${option.dataset.quickActionDescription || ''}`.toLowerCase();
      option.hidden = Boolean(query && !haystack.includes(query));
      if (!option.hidden) visibleCount += 1;
    });
    if (dom.quickActionNoResults) dom.quickActionNoResults.hidden = visibleCount > 0 || !query;
  };

  const syncQuickActionControl = () => {
    const selectedId = dom.runQuickActionSelect?.value || '';
    const selectedOption = dom.runQuickActionSelect?.selectedOptions?.[0] || null;
    const selectedLabel = selectedId
      ? (selectedOption?.dataset.quickActionName || selectedOption?.textContent?.trim() || 'Quick action')
      : 'Choose a quick action';
    if (dom.quickActionComboboxLabel) dom.quickActionComboboxLabel.textContent = selectedLabel;
    if (dom.clearQuickActionButton) dom.clearQuickActionButton.hidden = !selectedId;
    (dom.quickActionOptions || []).forEach((option) => {
      const isSelected = (option.dataset.quickActionId || '') === selectedId;
      option.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      option.classList.toggle('is-selected', isSelected);
    });
    const hiddenIdInput = dom.runQuickActionForm?.querySelector('[data-quick-action-id-input]');
    if (hiddenIdInput) hiddenIdInput.value = selectedId;
    syncGenerationAvailability?.();
  };

  const setFollowupSubmitting = (submitting) => {
    followupSubmitting = Boolean(submitting);
    if (dom.followupGenerateLabel) dom.followupGenerateLabel.textContent = followupSubmitting ? 'Starting…' : 'Generate';
    syncGenerationAvailability?.();
    if (followupSubmitting && dom.runQuickActionTrigger) dom.runQuickActionTrigger.disabled = true;
  };

  const clearSteeringAfterQueue = () => {
    if (!dom.quickActionContextInput) return;
    dom.quickActionContextInput.value = '';
    dom.quickActionContextInput.dispatchEvent(new Event('input', { bubbles: true }));
    dom.quickActionContextInput.focus();
  };

  let quickActionContextRecorder = null;
  let quickActionContextStream = null;
  let quickActionContextChunks = [];
  let quickActionContextRecording = false;
  let quickActionContextTranscribing = false;

  const setQuickActionContextStatus = (message = '') => {
    if (dom.quickActionContextStatus) {
      dom.quickActionContextStatus.textContent = message;
    }
  };

  const syncQuickActionContextRecorderControls = () => {
    if (dom.quickActionContextRecordButton) {
      const voiceUnavailable = dom.quickActionContextRecordButton.dataset.voiceUnavailable === 'true';
      dom.quickActionContextRecordButton.disabled = !quickActionContextRecording && (
        voiceUnavailable || quickActionContextTranscribing || !getTranscriptId() || Boolean(dom.quickActionContextInput?.disabled)
      );
      dom.quickActionContextRecordButton.dataset.state = quickActionContextTranscribing
        ? 'transcribing'
        : (quickActionContextRecording ? 'recording' : 'idle');
      const icon = dom.quickActionContextRecordButton.querySelector('[data-lucide]');
      if (icon) icon.setAttribute('data-lucide', quickActionContextTranscribing ? 'loader-circle' : (quickActionContextRecording ? 'square' : 'mic'));
      dom.quickActionContextRecordButton.setAttribute('aria-label', quickActionContextRecording ? 'Stop recording' : (voiceUnavailable ? 'Voice input unavailable' : 'Record context'));
      dom.quickActionContextRecordButton.title = voiceUnavailable ? 'Voice input unavailable' : (quickActionContextRecording ? 'Stop recording' : 'Record context');
    }
    if (dom.quickActionContextRecordLabel) {
      dom.quickActionContextRecordLabel.textContent = quickActionContextRecording ? 'Stop recording' : 'Record context';
    }
    window.lucide?.createIcons?.({ attrs: { 'aria-hidden': 'true' } });
    if (!dom.quickActionContextInput && quickActionContextRecording) stopQuickActionContextRecording();
  };

  const appendTextToField = (field, text) => {
    if (!field) return;
    const next = String(text || '').trim();
    if (!next) return;
    const start = Number.isInteger(field.selectionStart) ? field.selectionStart : field.value.length;
    const end = Number.isInteger(field.selectionEnd) ? field.selectionEnd : start;
    const before = field.value.slice(0, start);
    const after = field.value.slice(end);
    const prefix = before && !/\s$/.test(before) ? ' ' : '';
    const suffix = after && !/^\s/.test(after) ? ' ' : '';
    field.value = `${before}${prefix}${next}${suffix}${after}`;
    const caret = before.length + prefix.length + next.length;
    field.setSelectionRange?.(caret, caret);
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.focus();
  };

  const uploadQuickActionContextAudio = async (blob) => {
    const transcriptId = getTranscriptId();
    if (!transcriptId || !blob) return;
    const formData = new FormData();
    formData.append('audio', blob, 'quick-action-context.webm');
    quickActionContextTranscribing = true;
    setQuickActionContextStatus('Turning recording into text…');
    syncQuickActionContextRecorderControls();
    const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/quick-action-context/preview-audio-file`, {
      method: 'POST',
      credentials: 'include',
      body: formData,
    });
    if (!response.ok) {
      throw new Error(await parseErrorMessage(response, 'Could not transcribe quick-action context.'));
    }
    const body = await response.json();
    const text = String(body.text || '').trim();
    if (text) {
      appendTextToField(dom.quickActionContextInput, text);
      setQuickActionContextStatus('Text added.');
    } else {
      setQuickActionContextStatus('No speech heard.');
    }
    quickActionContextTranscribing = false;
    syncQuickActionContextRecorderControls();
  };

  const stopQuickActionContextStream = () => {
    quickActionContextStream?.getTracks?.().forEach((track) => track.stop());
    quickActionContextStream = null;
  };

  const startQuickActionContextRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      showFlash('Context recording is not supported in this browser.', 'error');
      return;
    }
    try {
      quickActionContextChunks = [];
      quickActionContextStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      quickActionContextRecorder = new MediaRecorder(quickActionContextStream);
      quickActionContextRecorder.addEventListener('dataavailable', (event) => {
        if (event.data && event.data.size > 0) {
          quickActionContextChunks.push(event.data);
        }
      });
      quickActionContextRecorder.addEventListener('stop', async () => {
        quickActionContextRecording = false;
        syncQuickActionContextRecorderControls();
        const type = quickActionContextRecorder?.mimeType || 'audio/webm';
        const blob = quickActionContextChunks.length ? new Blob(quickActionContextChunks, { type }) : null;
        stopQuickActionContextStream();
        if (!blob) {
          setQuickActionContextStatus('No speech heard.');
          return;
        }
        try {
          await uploadQuickActionContextAudio(blob);
        } catch (error) {
          quickActionContextTranscribing = false;
          const message = error instanceof Error ? error.message : 'Could not transcribe quick-action context.';
          setQuickActionContextStatus('');
          showFlash(message, 'error');
        } finally {
          syncQuickActionContextRecorderControls();
        }
      });
      quickActionContextRecording = true;
      quickActionContextRecorder.start();
      setQuickActionContextStatus('Recording…');
      syncQuickActionContextRecorderControls();
    } catch (_) {
      quickActionContextRecording = false;
      stopQuickActionContextStream();
      setQuickActionContextStatus('');
      syncQuickActionContextRecorderControls();
      showFlash('Microphone access was denied or unavailable.', 'error');
    }
  };

  const stopQuickActionContextRecording = () => {
    if (!quickActionContextRecorder || quickActionContextRecorder.state === 'inactive') return;
    setQuickActionContextStatus('Turning recording into text…');
    quickActionContextRecorder.stop();
  };

  const deleteSelectedNote = async () => {
    const generatedDocumentId = dom.latestGeneratedOutput?.dataset.latestGeneratedId || '';
    const selectedKind = dom.latestGeneratedOutput?.dataset.latestGeneratedKind || '';
    if (selectedKind === 'working_note') {
      await clearWorkingNote?.();
      return;
    }
    if (!generatedDocumentId) return;
    if (!window.confirm('Delete this note permanently?')) {
      return;
    }
    try {
      const response = await csrfFetch(`/api/v1/generated-documents/${generatedDocumentId}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(await parseErrorMessage(response, 'Could not delete the note.'));
      }
      showFlash('Note deleted.', 'success');
      await fetchWorkspace();
    } catch (error) {
      showFlash(error instanceof Error ? error.message : 'Could not delete the note.', 'error');
    }
  };

  dom.noteSelector?.addEventListener('click', (event) => {
    const hoverDeleteButton = event.target.closest('[data-note-hover-delete]');
    if (hoverDeleteButton) {
      event.preventDefault();
      event.stopPropagation();
      const documentId = hoverDeleteButton.dataset.documentId || '';
      void selectDocumentFromUi('note', documentId).then((selected) => {
        if (selected !== false) {
          void deleteSelectedNote();
        }
      });
      return;
    }
    const button = event.target.closest('[data-document-id]');
    if (!button) return;
    selectDocumentFromUi('note', button.dataset.documentId || '');
  });

  dom.noteHistory?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-document-id]');
    if (!button) return;
    selectDocumentFromUi('note', button.dataset.documentId || '');
  });

  dom.followupSelector?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-document-id]');
    if (!button) return;
    selectDocumentFromUi('followup', button.dataset.documentId || '');
  });

  dom.followupHistory?.addEventListener('click', async (event) => {
    const copyButton = event.target.closest('[data-followup-copy]');
    if (copyButton) {
      event.preventDefault();
      event.stopPropagation();
      const card = copyButton.closest('[data-document-id]');
      const documentId = card?.dataset.documentId || '';
      if (!documentId || !(await selectDocumentFromUi('followup', documentId))) return;
      const text = followupCopyText();
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        showCopyToast();
      } catch (_) {
        showFlash('Could not copy the follow-up.', 'error');
      }
      return;
    }

    const deleteButton = event.target.closest('[data-followup-delete]');
    if (deleteButton) {
      event.preventDefault();
      event.stopPropagation();
      const generatedDocumentId = deleteButton.dataset.generatedDocumentId || '';
      if (!generatedDocumentId) return;
      if (!window.confirm('Delete this follow-up permanently? This cannot be undone.')) {
        return;
      }
      try {
        const response = await csrfFetch(`/api/v1/generated-documents/${generatedDocumentId}`, {
          method: 'DELETE',
          credentials: 'include',
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not delete the follow-up.'));
        }
        showFlash('Follow-up deleted.', 'success');
        await fetchWorkspace();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not delete the follow-up.', 'error');
      }
      return;
    }

    const button = event.target.closest('[data-followup-history-select]');
    if (!button) return;
    const card = button.closest('[data-document-id]');
    selectDocumentFromUi('followup', card?.dataset.documentId || '');
  });

  dom.copyLatestFollowupButton?.addEventListener('click', async () => {
    const text = followupCopyText();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showCopyToast();
    } catch (_) {
      showFlash('Could not copy the follow-up.', 'error');
    }
  });

  dom.deleteLatestFollowupButton?.addEventListener('click', async () => {
    const generatedDocumentId = dom.latestFollowupOutput?.dataset.latestFollowupId || '';
    if (!generatedDocumentId) return;
    if (!window.confirm('Delete this follow-up permanently? This cannot be undone.')) return;
    try {
      const response = await csrfFetch(`/api/v1/generated-documents/${generatedDocumentId}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(await parseErrorMessage(response, 'Could not delete the follow-up.'));
      }
      showFlash('Follow-up deleted.', 'success');
      await fetchWorkspace();
    } catch (error) {
      showFlash(error instanceof Error ? error.message : 'Could not delete the follow-up.', 'error');
    }
  });

  dom.regenerateLatestFollowupButton?.addEventListener('click', async () => {
    const generatedDocumentId = dom.latestFollowupOutput?.dataset.latestFollowupId || '';
    if (!generatedDocumentId || followupRegenerating) return;
    const label = dom.regenerateLatestFollowupButton.querySelector('[data-followup-regenerate-label]');
    followupRegenerating = true;
    dom.regenerateLatestFollowupButton.disabled = true;
    if (label) label.textContent = 'Starting…';
    try {
      if (persistPendingEditorsBeforeWorkspaceSwitch
        && !(await persistPendingEditorsBeforeWorkspaceSwitch())) return;
      await saveWorkingNoteBeforeGeneration?.();
      await saveDictationBeforeGeneration?.();
      const steeringText = dom.quickActionContextInput?.value?.trim() || '';
      const response = await csrfFetch(`/api/v1/generated-documents/${generatedDocumentId}/regenerate`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ steering_text: steeringText || null }),
      });
      if (!response.ok) throw new Error(await parseErrorMessage(response, 'We couldn’t regenerate the follow-up.'));
      await finishQueuedFollowup(await response.json());
    } catch (error) {
      showFlash(error instanceof Error ? error.message : 'We couldn’t regenerate the follow-up.', 'error');
    } finally {
      followupRegenerating = false;
      if (label) label.textContent = 'Regenerate';
      const status = dom.latestFollowupOutput?.dataset.latestFollowupStatus || '';
      dom.regenerateLatestFollowupButton.disabled = !['ready', 'failed'].includes(status);
    }
  });

  if (dom.copyTranscriptButton) {
    dom.copyTranscriptButton.addEventListener('click', async () => {
      const text = (
        getTranscriptText?.()
        || (dom.activeDraft instanceof HTMLTextAreaElement || dom.activeDraft instanceof HTMLInputElement
          ? dom.activeDraft.value
          : dom.activeDraft?.textContent)
      )?.trim() || '';
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        showCopyToast();
      } catch (_) {
        showFlash('Could not copy the transcript.', 'error');
      }
    });
  }

  if (dom.copyStructuredLinesButton) {
    dom.copyStructuredLinesButton.addEventListener('click', async () => {
      const checkedRows = structuredEditor.collectSelectedNoteLines();
      if (checkedRows.length === 0) {
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = 'Select at least one note line to copy.';
        }
        return;
      }
      const copyReviewBlocker = structuredEditor.noteCopyReviewBlocker?.({ lines: checkedRows });
      if (copyReviewBlocker) {
        showFlash(copyReviewBlocker, 'error');
        return;
      }
      const grouped = new Map();
      checkedRows.forEach(({ label, text }) => {
        const lines = grouped.get(label) || [];
        lines.push(text);
        grouped.set(label, lines);
      });
      const textToCopy = [...grouped.entries()].map(([label, lines]) => {
        const body = lines.join('\n');
        return label ? `${label}:\n${body}` : body;
      }).join('\n\n');
      try {
        await navigator.clipboard.writeText(textToCopy);
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = `Copied ${checkedRows.length} selected statement${checkedRows.length === 1 ? '' : 's'} to the clipboard.`;
        }
        showCopyToast();
      } catch (_) {
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = 'Could not copy the selected statements.';
        } else {
          showFlash('Could not copy the selected statements.', 'error');
        }
      }
    });
  }

  if (dom.generatedStructuredPanel) {
    dom.generatedStructuredPanel.addEventListener('click', async (event) => {
      const copyButton = event.target.closest('[data-copy-structured-section]');
      if (!copyButton) return;
      event.preventDefault();
      event.stopPropagation();

      const section = copyButton.closest('[data-generated-structured-section]');
      const label = section?.dataset?.sectionLabel || '';
      const copyReviewBlocker = structuredEditor.noteCopyReviewBlocker?.({ section });
      if (copyReviewBlocker) {
        showFlash(copyReviewBlocker, 'error');
        return;
      }
      const lines = structuredEditor.collectStructuredSectionLines(section);
      if (lines.length === 0) {
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = label ? `No ${label} lines to copy.` : 'No section lines to copy.';
        }
        return;
      }
      const textToCopy = lines.join('\n');
      try {
        await navigator.clipboard.writeText(textToCopy);
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = `Copied ${label || 'section'} section to the clipboard.`;
        }
        showCopyToast();
      } catch (_) {
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = 'Could not copy the section.';
        } else {
          showFlash('Could not copy the section.', 'error');
        }
      }
    });
  }

  if (dom.clearStructuredSelectionButton) {
    dom.clearStructuredSelectionButton.addEventListener('click', () => {
      structuredEditor.clearStructuredSelection();
    });
  }

  if (dom.selectStructuredSelectionButton) {
    dom.selectStructuredSelectionButton.addEventListener('click', () => {
      structuredEditor.selectStructuredSelection();
    });
  }

  if (dom.generateOutputTemplateSelect) {
    dom.generateOutputTemplateSelect.addEventListener('change', async () => {
      const canContinue = await handleOutputTemplateChange?.();
      if (canContinue === false) {
        return;
      }
      const templateId = dom.generateOutputTemplateSelect.value || '';
      if (!templateId || !persistUserAppPreferences) {
        return;
      }
      try {
        await persistUserAppPreferences({ default_template_id: templateId });
      } catch (_) {}
    });
  }

  dom.sessionList?.addEventListener('click', async (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const link = target?.closest('[data-session-link]');
    if (!link || !dom.sessionList.contains(link)) return;
    if (window.document.querySelector('[data-legacy-note-workspace][data-inline-controller="true"]')) {
      return;
    }
    event.preventDefault();
    const nextTranscriptId = link.dataset.transcriptId;
    if (!nextTranscriptId || nextTranscriptId === getTranscriptId()) {
      return;
    }
    if (getIsRecordingSwitchBlocked?.()) {
      showFlash('Stop recording before switching consultations.', 'warning');
      return;
    }
    if (persistPendingEditorsBeforeWorkspaceSwitch
      && !(await persistPendingEditorsBeforeWorkspaceSwitch())) return;
    const workspace = await fetchWorkspace(nextTranscriptId);
    if (!workspace) {
      window.location.assign(link.href);
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('transcript_id', nextTranscriptId);
    window.history.pushState({}, '', url.toString());
    showFlash('', 'success');
  });

  if (dom.newSessionForm) {
    dom.newSessionForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (getIsRecordingSwitchBlocked?.()) {
        showFlash('Stop recording before creating a new consultation.', 'warning');
        return;
      }
      try {
        if (persistPendingEditorsBeforeWorkspaceSwitch
          && !(await persistPendingEditorsBeforeWorkspaceSwitch())) return;
        const preferredMode = dom.newSessionForm.querySelector('input[name="ingestion_mode"]')?.value || 'whole_file';
        const response = await csrfFetch('/api/v1/transcripts/start', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: 'Untitled session', ingestion_mode: preferredMode }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not create a new consultation.'));
        }
        const transcript = await response.json();
        window.location.assign(`${routeBase}?transcript_id=${transcript.id}`);
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not create a new consultation.', 'error');
      }
    });
  }

  if (dom.recordingModeSelect) {
    dom.recordingModeSelect.addEventListener('change', async () => {
      const transcriptId = getTranscriptId();
      const targetMode = dom.recordingModeSelect.value || 'whole_file';
      const previousMode = getActiveIngestionMode() || 'whole_file';
      if (!transcriptId || targetMode === previousMode) {
        dom.recordingModeSelect.value = previousMode;
        return;
      }
        try {
          await syncTranscriptTitleIfNeeded();
          const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}`, {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ingestion_mode: targetMode }),
        });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not change the recording mode for this consultation.'));
          }
          if (persistUserAppPreferences) {
            try {
              await persistUserAppPreferences({ preferred_recording_mode: targetMode });
            } catch (_) {}
          }
          await fetchWorkspace();
        setMicButtons(getIsLiveCaptureUiActive());
        showFlash(`Recording mode changed to ${targetMode === 'live_chunked' ? 'live capture' : 'recorded upload'}.`, 'success');
      } catch (error) {
        dom.recordingModeSelect.value = previousMode;
        setMicButtons(getIsLiveCaptureUiActive());
        showFlash(error instanceof Error ? error.message : 'Could not change the recording mode for this consultation.', 'error');
      }
    });
  }

  if (dom.bulkDeleteForm) {
    dom.bulkDeleteForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const selectedBoxes = [
        ...(dom.sessionList || window.document).querySelectorAll('[data-session-select]'),
      ].filter((checkbox) => checkbox.checked);
      const selectedIds = selectedBoxes.map((checkbox) => checkbox.value);
      if (selectedIds.length === 0) return;
      if (selectedBoxes.some((checkbox) => checkbox.dataset.hasTranscriptContent === 'true')) {
        const message = selectedIds.length === 1
          ? 'This consultation has transcript text. Delete it permanently?'
          : 'One or more selected consultations have transcript text. Delete them permanently?';
        if (!window.confirm(message)) return;
      }
      try {
        await Promise.all(selectedIds.map(async (selectedId) => {
          const response = await csrfFetch(`/api/v1/transcripts/${selectedId}`, {
            method: 'DELETE',
            credentials: 'include',
          });
          if (!response.ok) {
            throw new Error(await parseErrorMessage(response, 'Could not delete the selected sessions.'));
          }
        }));
        showFlash(`Deleted ${selectedIds.length} consultation${selectedIds.length === 1 ? '' : 's'}.`, 'success');
        window.location.assign(routeBase);
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not delete the selected consultations.', 'error');
      }
    });
  }

  if (dom.titleForm) {
    dom.titleForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await syncTranscriptTitleIfNeeded();
        showFlash('Consultation title updated.', 'success');
        await fetchWorkspace();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not update the consultation title.', 'error');
      }
    });
  }

  if (dom.renameTitleInput) {
    dom.renameTitleInput.addEventListener('blur', async () => {
      try {
        await syncTranscriptTitleIfNeeded();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not update the consultation title.', 'error');
      }
    });
    dom.renameTitleInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        dom.titleForm?.requestSubmit();
      }
    });
  }

  if (dom.uploadForm) {
    dom.uploadForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const transcriptId = getTranscriptId();
      if (!transcriptId || !dom.fileInput?.files || dom.fileInput.files.length === 0) return;
      setVisibleStatus('uploading');
      setSessionProgress('Uploading your recording...');
      setRetryAvailability(false);
      try {
        await syncTranscriptTitleIfNeeded();
        const formData = new FormData();
        formData.append('audio', dom.fileInput.files[0], dom.fileInput.files[0].name);
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
          method: 'POST',
          body: formData,
          credentials: 'include',
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not send the recording.'));
        }
        showFlash('Recording sent to be turned into text.', 'success');
        dom.fileInput.value = '';
        await fetchWorkspace();
        scheduleWorkspaceRefreshBurst();
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Could not send the recording.';
        showFlash(message, 'error');
        reflectBackendStatus('failed', message);
        setRetryAvailability(false);
      }
    });
  }

  if (dom.generateOutputForm) {
    dom.generateOutputForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const transcriptId = getTranscriptId();
      if (!transcriptId) return;
      if (!enqueueTemplateGeneration) return;
      const templateId = dom.generateOutputTemplateSelect?.value || dom.generateOutputForm.querySelector('[data-generate-template-id]')?.value || '';
      if (!templateId) return;
      try {
        const queued = await enqueueTemplateGeneration({ templateId });
        if (!queued) return;
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not enqueue note generation.', 'error');
      }
    });
  }

  const finishQueuedFollowup = async (queued) => {
    clearSteeringAfterQueue();
    setTab('followups');
    showFlash('Follow-up started.', 'success');
    await fetchWorkspace();
    if (queued?.id) await selectDocumentFromUi('followup', queued.id);
    scheduleWorkspaceRefreshBurst();
  };

  if (dom.generateFollowupForm) {
    dom.generateFollowupForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const transcriptId = getTranscriptId();
      const promptText = dom.quickActionContextInput?.value?.trim() || '';
      if (!transcriptId || !promptText || followupSubmitting) return;
      setFollowupSubmitting(true);
      try {
        await saveWorkingNoteBeforeGeneration?.();
        await saveDictationBeforeGeneration?.();
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/generate-followup`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt_text: promptText }),
        });
        if (!response.ok) throw new Error(await parseErrorMessage(response, 'Could not create the follow-up.'));
        await finishQueuedFollowup(await response.json());
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not create the follow-up.', 'error');
      } finally {
        setFollowupSubmitting(false);
      }
    });
  }

  dom.runQuickActionSelect?.addEventListener('change', syncQuickActionControl);
  dom.quickActionComboboxToggle?.addEventListener('click', () => {
    setQuickActionComboboxOpen(!quickActionComboboxOpen, { focusSearch: !quickActionComboboxOpen });
  });
  dom.quickActionSearchInput?.addEventListener('input', filterQuickActionOptions);
  const chooseQuickActionOption = (option) => {
    if (!option || !dom.runQuickActionSelect) return;
    dom.runQuickActionSelect.value = option.dataset.quickActionId || '';
    dom.runQuickActionSelect.dispatchEvent(new Event('change', { bubbles: true }));
    setQuickActionComboboxOpen(false);
    dom.quickActionContextInput?.focus();
  };
  dom.quickActionOptions?.forEach((option) => {
    option.addEventListener('click', () => chooseQuickActionOption(option));
  });
  const moveQuickActionSearchActiveOption = (delta) => {
    const options = filteredQuickActionOptions();
    if (!options.length) return;
    const activeId = dom.quickActionSearchInput?.getAttribute('aria-activedescendant') || '';
    const currentIndex = options.findIndex((option) => option.id === activeId);
    const nextIndex = currentIndex < 0
      ? (delta > 0 ? 0 : options.length - 1)
      : (currentIndex + delta + options.length) % options.length;
    const nextOption = options[nextIndex];
    if (!nextOption?.id) return;
    dom.quickActionSearchInput?.setAttribute('aria-activedescendant', nextOption.id);
    nextOption.scrollIntoView?.({ block: 'nearest' });
  };
  dom.quickActionSearchInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setQuickActionComboboxOpen(false);
      dom.quickActionComboboxToggle?.focus();
      return;
    }
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      if (!quickActionComboboxOpen) setQuickActionComboboxOpen(true);
      moveQuickActionSearchActiveOption(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (event.key === 'Enter') {
      const activeId = dom.quickActionSearchInput?.getAttribute('aria-activedescendant') || '';
      const activeOption = (dom.quickActionOptions || []).find((option) => option.id === activeId && !option.hidden);
      if (!activeOption) return;
      event.preventDefault();
      event.stopPropagation();
      chooseQuickActionOption(activeOption);
    }
  });
  dom.quickActionCombobox?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setQuickActionComboboxOpen(false);
      dom.quickActionComboboxToggle?.focus();
      return;
    }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    if (!quickActionComboboxOpen) setQuickActionComboboxOpen(true);
    const options = filteredQuickActionOptions();
    if (!options.length) return;
    if (event.target === dom.quickActionComboboxToggle) {
      dom.quickActionSearchInput?.focus();
      moveQuickActionSearchActiveOption(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    const currentIndex = options.indexOf(window.document.activeElement);
    const delta = event.key === 'ArrowDown' ? 1 : -1;
    const nextOption = options[(currentIndex + delta + options.length) % options.length];
    nextOption.focus();
    dom.quickActionSearchInput?.removeAttribute('aria-activedescendant');
  });
  window.document.addEventListener('click', (event) => {
    if (quickActionComboboxOpen && !dom.quickActionCombobox?.contains(event.target)) {
      setQuickActionComboboxOpen(false);
    }
  });

  const bindCharacterCounter = (input, counter, max) => {
    if (!input || !counter) return;
    const limit = Number(input.getAttribute('maxlength')) || max;
    const update = () => {
      counter.textContent = `${input.value.length} / ${limit}`;
    };
    input.addEventListener('input', update);
    update();
  };
  bindCharacterCounter(dom.quickActionContextInput, dom.contextCharCount, 4000);

  dom.clearQuickActionButton?.addEventListener('click', () => {
    if (dom.runQuickActionSelect) dom.runQuickActionSelect.value = '';
    dom.runQuickActionSelect?.dispatchEvent(new Event('change', { bubbles: true }));
    dom.quickActionContextInput?.focus();
  });

  dom.quickActionContextInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.isComposing || !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    dom.runQuickActionTrigger?.click();
  });

  dom.generateFollowupTrigger?.addEventListener('click', () => {
    dom.generateFollowupForm?.requestSubmit?.();
  });

  dom.runQuickActionTrigger?.addEventListener('click', () => {
    if (dom.runQuickActionSelect?.value) {
      dom.runQuickActionForm?.requestSubmit?.();
      return;
    }
    if (!dom.quickActionContextInput?.value?.trim()) {
      showFlash('Choose a quick action or add context.', 'warning');
      return;
    }
    dom.generateFollowupForm?.requestSubmit?.();
  });

  if (dom.runQuickActionForm) {
    syncQuickActionControl();
    dom.runQuickActionForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const transcriptId = getTranscriptId();
      if (!transcriptId) return;
      const quickActionId = dom.runQuickActionSelect?.value
        || dom.runQuickActionForm.querySelector('[data-quick-action-id-input]')?.value
        || '';
      const quickActionContextText = dom.quickActionContextInput?.value?.trim()
        || dom.runQuickActionForm.querySelector('[data-quick-action-context-hidden]')?.value?.trim()
        || '';
      if (!quickActionId) {
        showFlash('Choose a quick action or add context.', 'warning');
        return;
      }
      const hiddenIdInput = dom.runQuickActionForm.querySelector('[data-quick-action-id-input]');
      if (hiddenIdInput) {
        hiddenIdInput.value = quickActionId;
      }
      const hiddenContextInput = dom.runQuickActionForm.querySelector('[data-quick-action-context-hidden]');
      if (hiddenContextInput) {
        hiddenContextInput.value = quickActionContextText;
      }
      if (followupSubmitting) return;
      setFollowupSubmitting(true);
      try {
        await saveWorkingNoteBeforeGeneration?.();
        await saveDictationBeforeGeneration?.();
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/run-quick-action`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quick_action_id: quickActionId, context_text: quickActionContextText || null }),
        });
        if (!response.ok) throw new Error(await parseErrorMessage(response, 'Could not create the follow-up.'));
        await finishQueuedFollowup(await response.json());
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not create the follow-up.', 'error');
      } finally {
        setFollowupSubmitting(false);
      }
    });
  }

  dom.quickActionContextRecordButton?.addEventListener('click', () => {
    if (quickActionContextRecording) {
      stopQuickActionContextRecording();
    } else {
      void startQuickActionContextRecording();
    }
  });

  const setHistoryRailOpen = (open, { persist = true } = {}) => {
    const next = Boolean(open);
    dom.followupWorkspace?.setAttribute('data-history-collapsed', next ? 'false' : 'true');
    if (dom.followupHistoryRail) {
      dom.followupHistoryRail.hidden = !next;
      dom.followupHistoryRail.inert = !next;
      if (historyMobileMedia.matches && next) {
        dom.followupHistoryRail.setAttribute('role', 'dialog');
        dom.followupHistoryRail.setAttribute('aria-modal', 'true');
      } else {
        dom.followupHistoryRail.removeAttribute('role');
        dom.followupHistoryRail.removeAttribute('aria-modal');
      }
    }
    if (dom.followupHistoryOpenButton) {
      dom.followupHistoryOpenButton.hidden = next;
      dom.followupHistoryOpenButton.setAttribute('aria-expanded', next ? 'true' : 'false');
    }
    if (dom.followupHistoryScrim) dom.followupHistoryScrim.hidden = !next;
    dom.followupHistoryToggle?.setAttribute('aria-expanded', next ? 'true' : 'false');
    if (persist && !historyMobileMedia.matches) {
      try { window.localStorage.setItem(FOLLOWUP_HISTORY_OPEN_KEY, next ? 'true' : 'false'); } catch (_) {}
    }
  };
  const historyMobileMedia = window.matchMedia('(max-width: 1180px)');
  const storedDesktopHistoryOpen = () => {
    try {
      const saved = window.localStorage.getItem(FOLLOWUP_HISTORY_OPEN_KEY);
      return saved === null ? true : saved === 'true';
    } catch (_) {
      return true;
    }
  };
  const initialHistoryOpen = historyMobileMedia.matches ? false : storedDesktopHistoryOpen();
  let historyRailReturnFocus = null;
  setHistoryRailOpen(initialHistoryOpen, { persist: false });
  const openHistoryRail = () => {
    historyRailReturnFocus = window.document.activeElement;
    setHistoryRailOpen(true);
    if (historyMobileMedia.matches) dom.followupHistorySearch?.focus();
  };
  const closeHistoryRail = ({ restoreFocus = historyMobileMedia.matches } = {}) => {
    setHistoryRailOpen(false);
    if (restoreFocus) (historyRailReturnFocus || dom.followupHistoryOpenButton)?.focus?.();
  };
  dom.followupHistoryToggle?.addEventListener('click', () => closeHistoryRail());
  dom.followupHistoryOpenButton?.addEventListener('click', openHistoryRail);
  dom.followupHistoryScrim?.addEventListener('click', () => closeHistoryRail());
  historyMobileMedia.addEventListener?.('change', (event) => {
    setHistoryRailOpen(event.matches ? false : storedDesktopHistoryOpen(), { persist: false });
  });
  window.document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (quickActionComboboxOpen) {
      setQuickActionComboboxOpen(false);
      dom.quickActionComboboxToggle?.focus();
      return;
    }
    if (dom.followupHistory?.querySelector('[data-followup-history-menu][open]')) return;
    if (window.matchMedia?.('(max-width: 1180px)').matches && !dom.followupHistoryRail?.hidden) {
      closeHistoryRail();
    }
  });
  window.document.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || !historyMobileMedia.matches || dom.followupHistoryRail?.hidden) return;
    const focusable = [...dom.followupHistoryRail.querySelectorAll('button:not(:disabled), input:not(:disabled), summary, [tabindex]:not([tabindex="-1"])')]
      .filter((element) => {
        if (element.closest('[hidden]')) return false;
        const closedDetails = element.closest('details:not([open])');
        return !closedDetails || element.tagName === 'SUMMARY';
      });
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && window.document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && window.document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  const filterFollowupHistory = () => {
    const query = String(dom.followupHistorySearch?.value || '').trim().toLowerCase();
    let visible = 0;
    dom.followupHistory?.querySelectorAll('[data-followup-search-text]').forEach((item) => {
      item.hidden = Boolean(query && !String(item.dataset.followupSearchText || '').toLowerCase().includes(query));
      if (!item.hidden) visible += 1;
    });
    const noResults = dom.followupHistory?.querySelector('[data-followup-history-no-results]') || dom.followupHistoryNoResults;
    if (noResults) noResults.hidden = !query || visible > 0;
  };
  dom.followupHistorySearch?.addEventListener('input', filterFollowupHistory);
  window.document.addEventListener('transcribe:followup-history-rendered', filterFollowupHistory);
  const positionFollowupHistoryMenu = (details) => {
    const summary = details?.querySelector('summary');
    const menu = details?.querySelector('[role="menu"]');
    if (!details?.open || !summary || !menu) return;
    const viewportPadding = 8;
    const summaryRect = summary.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const menuWidth = menuRect.width;
    const menuHeight = menuRect.height;
    let left = summaryRect.right - menuWidth;
    let top = summaryRect.bottom + 4;
    if (left < viewportPadding) left = viewportPadding;
    if (left + menuWidth > window.innerWidth - viewportPadding) {
      left = Math.max(viewportPadding, window.innerWidth - menuWidth - viewportPadding);
    }
    if (top + menuHeight > window.innerHeight - viewportPadding) {
      top = Math.max(viewportPadding, summaryRect.top - menuHeight - 4);
    }
    details.style.setProperty('--followup-history-menu-top', `${Math.round(top)}px`);
    details.style.setProperty('--followup-history-menu-left', `${Math.round(left)}px`);
    details.classList.add('is-positioned');
  };
  const positionOpenFollowupHistoryMenus = () => {
    dom.followupHistory?.querySelectorAll('[data-followup-history-menu][open]').forEach(positionFollowupHistoryMenu);
  };
  dom.followupHistory?.addEventListener('toggle', (event) => {
    const details = event.target;
    if (!details?.matches?.('[data-followup-history-menu]')) return;
    const summary = details.querySelector('summary');
    if (!details.open) {
      details.classList.remove('is-positioned');
      summary?.setAttribute('aria-expanded', 'false');
      return;
    }
    dom.followupHistory?.querySelectorAll('[data-followup-history-menu][open]').forEach((other) => {
      if (other !== details) other.open = false;
    });
    summary?.setAttribute('aria-expanded', 'true');
    positionFollowupHistoryMenu(details);
  }, true);
  window.addEventListener('resize', positionOpenFollowupHistoryMenus);
  window.document.addEventListener('scroll', positionOpenFollowupHistoryMenus, true);
  window.document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const openMenu = dom.followupHistory?.querySelector('[data-followup-history-menu][open]');
    if (!openMenu) return;
    event.preventDefault();
    openMenu.open = false;
    const summary = openMenu.querySelector('summary');
    summary?.setAttribute('aria-expanded', 'false');
    summary?.focus();
  });
  syncQuickActionContextRecorderControls();
}
