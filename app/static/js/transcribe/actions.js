import { csrfFetch } from '../csrf.js';

export function attachTranscribeActions({
  dom,
  routeBase,
  getTranscriptId,
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
  saveStructuredContext,
  setVisibleStatus,
  setSessionProgress,
  setRetryAvailability,
  reflectBackendStatus,
  persistUserAppPreferences,
  setMicButtons,
  setTab,
  structuredEditor,
}) {
  const syncQuickActionQuickPickState = () => {
    const selectedId = dom.runQuickActionSelect?.value || '';
    dom.quickActionQuickPicks?.forEach((button) => {
      const isSelected = (button.dataset.quickActionId || '') === selectedId;
      button.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
    });
    const hiddenIdInput = dom.runQuickActionForm?.querySelector('[data-quick-action-id-input]');
    if (hiddenIdInput) {
      hiddenIdInput.value = selectedId;
    }
  };

  dom.noteSelector?.addEventListener('click', (event) => {
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

  dom.noteDeleteButton?.addEventListener('click', async (event) => {
    event.preventDefault();
    const generatedDocumentId = dom.latestGeneratedOutput?.dataset.latestGeneratedId || '';
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
  });

  dom.followupHistory?.addEventListener('click', async (event) => {
    const copyButton = event.target.closest('[data-followup-copy]');
    if (copyButton) {
      event.preventDefault();
      event.stopPropagation();
      const card = copyButton.closest('[data-document-id]');
      const text = card?.querySelector('[data-followup-copy-body]')?.textContent?.trim() || '';
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
      if (!window.confirm('Delete this follow-up permanently?')) {
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

    const button = event.target.closest('[data-document-id]');
    if (!button) return;
    selectDocumentFromUi('followup', button.dataset.documentId || '');
  });

  if (dom.copyTranscriptButton) {
    dom.copyTranscriptButton.addEventListener('click', async () => {
      const text = (
        dom.activeDraft instanceof HTMLTextAreaElement || dom.activeDraft instanceof HTMLInputElement
          ? dom.activeDraft.value
          : dom.activeDraft?.textContent
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
      const body = lines.join('\n');
      const textToCopy = label ? `${label}:\n${body}` : body;
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
      structuredEditor.syncStructuredTemplateUi();
      const templateId = dom.generateOutputTemplateSelect.value || '';
      if (!templateId || !persistUserAppPreferences) {
        return;
      }
      try {
        await persistUserAppPreferences({ default_template_id: templateId });
      } catch (_) {}
    });
  }

  dom.sessionLinks.forEach((link) => {
    link.addEventListener('click', async (event) => {
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
  });

  if (dom.newSessionForm) {
    dom.newSessionForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (getIsRecordingSwitchBlocked?.()) {
        showFlash('Stop recording before creating a new consultation.', 'warning');
        return;
      }
      try {
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
      const selectedBoxes = dom.selectionBoxes.filter((checkbox) => checkbox.checked);
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
      const templateId = dom.generateOutputTemplateSelect?.value || dom.generateOutputForm.querySelector('[data-generate-template-id]')?.value || '';
      if (!templateId) return;
      try {
        await saveStructuredContext({ silent: true });
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/generate-output`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            structuredEditor.selectedOutputTemplateMode() === 'structured'
              ? { template_id: templateId, structured_context: structuredEditor.collectStructuredContext() }
              : { template_id: templateId }
          ),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not enqueue note generation.'));
        }
        setTab('output');
        showFlash('Queued note generation.', 'success');
        await fetchWorkspace();
        scheduleWorkspaceRefreshBurst();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not enqueue note generation.', 'error');
      }
    });
  }

  if (dom.generateFollowupForm) {
    dom.generateFollowupForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const transcriptId = getTranscriptId();
      if (!transcriptId) return;
      const promptText = dom.generateFollowupPromptInput?.value?.trim()
        || dom.generateFollowupForm.querySelector('[data-followup-prompt-hidden]')?.value?.trim()
        || '';
      if (!promptText) return;
      try {
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/generate-followup`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt_text: promptText }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not create the follow-up.'));
        }
        setTab('followups');
        showFlash('Follow-up request sent.', 'success');
        await fetchWorkspace();
        scheduleWorkspaceRefreshBurst();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not create the follow-up.', 'error');
      }
    });
  }

  if (dom.runQuickActionSelect) {
    dom.runQuickActionSelect.addEventListener('change', () => {
      syncQuickActionQuickPickState();
    });
  }

  if (dom.quickActionQuickPicks?.length) {
    dom.quickActionQuickPicks.forEach((button) => {
      button.addEventListener('click', () => {
        if (!dom.runQuickActionSelect || button.disabled) return;
        dom.runQuickActionSelect.value = button.dataset.quickActionId || '';
        syncQuickActionQuickPickState();
        dom.quickActionContextInput?.focus();
      });
    });
  }

  if (dom.runQuickActionForm) {
    syncQuickActionQuickPickState();
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
      if (!quickActionId) return;
      const hiddenIdInput = dom.runQuickActionForm.querySelector('[data-quick-action-id-input]');
      if (hiddenIdInput) {
        hiddenIdInput.value = quickActionId;
      }
      const hiddenContextInput = dom.runQuickActionForm.querySelector('[data-quick-action-context-hidden]');
      if (hiddenContextInput) {
        hiddenContextInput.value = quickActionContextText;
      }
      try {
        const response = await csrfFetch(`/api/v1/transcripts/${transcriptId}/run-quick-action`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quick_action_id: quickActionId, context_text: quickActionContextText || null }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not run the quick action.'));
        }
        setTab('followups');
        showFlash('Quick action started.', 'success');
        await fetchWorkspace();
        scheduleWorkspaceRefreshBurst();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not run the quick action.', 'error');
      }
    });
  }
}
