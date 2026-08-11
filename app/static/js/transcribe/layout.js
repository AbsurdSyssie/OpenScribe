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
      trigger.setAttribute('aria-selected', isActive ? 'true' : 'false');
      trigger.tabIndex = isActive ? 0 : -1;
    });
    dom.panels.forEach((panel) => {
      panel.hidden = panel.dataset.tabPanel !== target;
    });
    dom.tabActions?.forEach((action) => {
      action.hidden = action.dataset.tabAction !== target;
    });
  };

  const setPaneState = (state) => {
    const nextState = ['collapsed', 'normal', 'expanded'].includes(state) ? state : 'normal';
    dom.shell.dataset.layoutState = nextState;
    window.localStorage.setItem(paneStorageKey, nextState);
  };

  const attach = () => {
    dom.triggers.forEach((trigger) => {
      trigger.addEventListener('click', () => setTab(trigger.dataset.tabTrigger));
      trigger.addEventListener('keydown', (event) => {
        const currentIndex = dom.triggers.indexOf(trigger);
        if (currentIndex < 0) return;
        let nextIndex = null;
        if (event.key === 'ArrowLeft') {
          nextIndex = (currentIndex - 1 + dom.triggers.length) % dom.triggers.length;
        } else if (event.key === 'ArrowRight') {
          nextIndex = (currentIndex + 1) % dom.triggers.length;
        } else if (event.key === 'Home') {
          nextIndex = 0;
        } else if (event.key === 'End') {
          nextIndex = dom.triggers.length - 1;
        }
        if (nextIndex === null) return;
        event.preventDefault();
        const nextTrigger = dom.triggers[nextIndex];
        setTab(nextTrigger.dataset.tabTrigger);
        nextTrigger.focus();
      });
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
  };

  return {
    attach,
    setPaneState,
    setTab,
  };
}
