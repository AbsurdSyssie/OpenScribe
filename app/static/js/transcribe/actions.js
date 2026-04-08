export function attachTranscribeActions({
  dom,
  routeBase,
  getTranscriptId,
  getActiveIngestionMode,
  getIsLiveCaptureUiActive,
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
  setMicButtons,
  setTab,
  structuredEditor,
}) {
  dom.noteSelector?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-document-id]');
    if (!button) return;
    selectDocumentFromUi('note', button.dataset.documentId || '');
  });

  dom.followupSelector?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-document-id]');
    if (!button) return;
    selectDocumentFromUi('followup', button.dataset.documentId || '');
  });

  if (dom.copyTranscriptButton) {
    dom.copyTranscriptButton.addEventListener('click', async () => {
      const text = dom.activeDraft?.textContent?.trim() || '';
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
      const checkedRows = [...document.querySelectorAll('[data-generated-structured-section] [data-structured-statement-row]')]
        .map((row) => {
          const checkbox = row.querySelector('[data-structured-line-checkbox]');
          const textarea = row.querySelector('[data-structured-line-input]');
          return {
            checked: Boolean(checkbox?.checked),
            label: checkbox?.dataset.sectionLabel || textarea?.dataset.sectionLabel || '',
            text: textarea?.value?.trim() || '',
          };
        })
        .filter((row) => row.checked && row.text.length > 0);
      if (checkedRows.length === 0) {
        if (dom.structuredCopyStatus) {
          dom.structuredCopyStatus.textContent = 'Select at least one statement to copy.';
        }
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

  if (dom.clearStructuredSelectionButton) {
    dom.clearStructuredSelectionButton.addEventListener('click', () => {
      structuredEditor.clearStructuredSelection();
    });
  }

  if (dom.generateOutputTemplateSelect) {
    dom.generateOutputTemplateSelect.addEventListener('change', () => structuredEditor.syncStructuredTemplateUi());
  }

  dom.sessionLinks.forEach((link) => {
    link.addEventListener('click', async (event) => {
      event.preventDefault();
      const nextTranscriptId = link.dataset.transcriptId;
      if (!nextTranscriptId || nextTranscriptId === getTranscriptId()) {
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
      try {
        const response = await fetch('/api/v1/transcripts/start', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: 'Untitled session', ingestion_mode: 'whole_file' }),
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
        const response = await fetch(`/api/v1/transcripts/${transcriptId}`, {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ingestion_mode: targetMode }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not change the recording mode for this consultation.'));
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
      const selectedIds = dom.selectionBoxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
      if (selectedIds.length === 0) return;
      try {
        await Promise.all(selectedIds.map(async (selectedId) => {
          const response = await fetch(`/api/v1/transcripts/${selectedId}`, {
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
        const response = await fetch(`/api/v1/transcripts/${transcriptId}/audio-file`, {
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
      const templateId = dom.generateOutputForm.querySelector('select[name="template_id"]')?.value || '';
      if (!templateId) return;
      try {
        await saveStructuredContext({ silent: true });
        const response = await fetch(`/api/v1/transcripts/${transcriptId}/generate-output`, {
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
      const promptText = dom.generateFollowupForm.querySelector('textarea[name="prompt_text"]')?.value?.trim() || '';
      if (!promptText) return;
      try {
        const response = await fetch(`/api/v1/transcripts/${transcriptId}/generate-followup`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt_text: promptText }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not create the message.'));
        }
        setTab('followups');
        showFlash('Message request sent.', 'success');
        await fetchWorkspace();
        scheduleWorkspaceRefreshBurst();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not create the message.', 'error');
      }
    });
  }

  if (dom.runQuickActionForm) {
    dom.runQuickActionForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const transcriptId = getTranscriptId();
      if (!transcriptId) return;
      const quickActionId = dom.runQuickActionForm.querySelector('select[name="quick_action_id"]')?.value || '';
      if (!quickActionId) return;
      try {
        const response = await fetch(`/api/v1/transcripts/${transcriptId}/run-quick-action`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quick_action_id: quickActionId }),
        });
        if (!response.ok) {
          throw new Error(await parseErrorMessage(response, 'Could not run the saved instruction.'));
        }
        setTab('followups');
        showFlash('Saved instruction started.', 'success');
        await fetchWorkspace();
        scheduleWorkspaceRefreshBurst();
      } catch (error) {
        showFlash(error instanceof Error ? error.message : 'Could not run the saved instruction.', 'error');
      }
    });
  }
}
