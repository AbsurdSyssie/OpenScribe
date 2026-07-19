export function createTranscribeLayout({
  dom,
  getCurrentAssistantTab,
  setCurrentAssistantTab,
  getTranscriptId,
  paneStorageKey,
  splitRatioStorageKey,
  initialPaneState,
  initialSplitRatio,
}) {
  const updateWorkspaceSettingsLink = () => {
    if (!dom.workspaceSettingsLink) return;

    if (getCurrentAssistantTab() === 'history') {
      dom.workspaceSettingsLink.hidden = true;
      return;
    }

    dom.workspaceSettingsLink.hidden = false;
    if (getCurrentAssistantTab() === 'followups') {
      const selectedOption = dom.runQuickActionSelect?.selectedOptions?.[0] || null;
      const transcriptId = getTranscriptId() || '';
      dom.workspaceSettingsLink.href = selectedOption?.dataset?.settingsUrl || '/settings?tab=quick-actions';
      dom.workspaceSettingsLink.title = 'Edit quick actions';
      dom.workspaceSettingsLink.setAttribute('aria-label', 'Edit quick actions');
      return;
    }

    const selectedOption = dom.generateOutputTemplateSelect?.selectedOptions?.[0] || null;
    const transcriptId = getTranscriptId() || '';
    dom.workspaceSettingsLink.href = selectedOption?.dataset?.settingsUrl || `/home?tab=templates&return_view=transcribe&queued_transcript_id=${encodeURIComponent(transcriptId)}&transcribe_tab=output`;
    dom.workspaceSettingsLink.title = 'Edit templates';
    dom.workspaceSettingsLink.setAttribute('aria-label', 'Edit templates');
  };

  const clampSplitRatio = (value) => {
    if (!Number.isFinite(value)) return 50;
    return Math.min(72, Math.max(28, value));
  };

  const setSplitRatio = (value) => {
    const next = clampSplitRatio(value);
    dom.shell.style.setProperty('--split-ratio', String(next));
    window.localStorage.setItem(splitRatioStorageKey, String(next));
  };

  const setTab = (target) => {
    setCurrentAssistantTab(target);
    dom.triggers.forEach((trigger) => {
      const isActive = trigger.dataset.tabTrigger === target;
      trigger.classList.toggle('active', isActive);
    });
    dom.panels.forEach((panel) => {
      panel.hidden = panel.dataset.tabPanel !== target;
    });
    dom.tabActions?.forEach((action) => {
      action.hidden = action.dataset.tabAction !== target;
    });
    updateWorkspaceSettingsLink();
  };

  const setPaneState = (state) => {
    const nextState = ['collapsed', 'normal', 'expanded'].includes(state) ? state : 'normal';
    dom.shell.dataset.layoutState = nextState;
    window.localStorage.setItem(paneStorageKey, nextState);
  };

  const attach = () => {
    dom.triggers.forEach((trigger) => {
      trigger.addEventListener('click', () => setTab(trigger.dataset.tabTrigger));
    });
    dom.paneToggles.forEach((button) => {
      button.addEventListener('click', () => setPaneState(button.dataset.paneToggle));
    });
    if (dom.dividerGrip) {
      let pointerId = null;
      let startX = 0;
      let startRatio = 50;
      let hasDragged = false;

      const stopDrag = () => {
        if (pointerId !== null) {
          dom.dividerGrip.releasePointerCapture?.(pointerId);
        }
        pointerId = null;
        hasDragged = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      dom.dividerGrip.addEventListener('pointerdown', (event) => {
        if (dom.shell.dataset.layoutState !== 'normal') return;
        const splitRect = dom.splitWorkspace?.getBoundingClientRect();
        if (!splitRect?.width) return;
        pointerId = event.pointerId;
        startX = event.clientX;
        startRatio = clampSplitRatio(Number.parseFloat(getComputedStyle(dom.shell).getPropertyValue('--split-ratio')));
        hasDragged = false;
        dom.dividerGrip.setPointerCapture?.(pointerId);
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        event.preventDefault();
      });

      dom.dividerGrip.addEventListener('pointermove', (event) => {
        if (pointerId === null || event.pointerId !== pointerId || dom.shell.dataset.layoutState !== 'normal') return;
        const splitRect = dom.splitWorkspace?.getBoundingClientRect();
        if (!splitRect?.width) return;
        const deltaX = event.clientX - startX;
        hasDragged = hasDragged || Math.abs(deltaX) > 3;
        const nextRatio = startRatio + ((deltaX / splitRect.width) * 100);
        setSplitRatio(nextRatio);
      });

      dom.dividerGrip.addEventListener('pointerup', stopDrag);
      dom.dividerGrip.addEventListener('pointercancel', stopDrag);
      dom.dividerGrip.addEventListener('click', (event) => {
        if (hasDragged) {
          event.preventDefault();
          event.stopPropagation();
        }
      });
    }
    setSplitRatio(initialSplitRatio);
    setTab(getCurrentAssistantTab());
    setPaneState(initialPaneState);
    dom.generateOutputTemplateSelect?.addEventListener('change', updateWorkspaceSettingsLink);
    dom.runQuickActionSelect?.addEventListener('change', updateWorkspaceSettingsLink);
  };

  return {
    attach,
    setPaneState,
    setTab,
  };
}
