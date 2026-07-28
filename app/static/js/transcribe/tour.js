const SCRIBE_TOUR_STORAGE_PREFIX = 'openscribe:tour:transcribe:';
const SCRIBE_WORKFLOW_VERSION = 'workflow-v1';

const nextFrame = () => new Promise((resolve) => window.requestAnimationFrame(resolve));

const activateTab = (tabName) => {
  const trigger = document.querySelector(`[data-tab-trigger="${tabName}"]`);
  if (trigger && !trigger.classList.contains('active')) {
    trigger.click();
  }
};

const isVisible = (element) => {
  if (!element || element.hidden) return false;
  const style = window.getComputedStyle(element);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
};

const findTarget = (target) => {
  const selectors = Array.isArray(target) ? target : [target];
  const matches = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
  return matches.find(isVisible) || matches[0] || null;
};

export function createScribeWorkflowSteps() {
  const showNote = () => activateTab('output');
  const showFollowUps = () => activateTab('followups');

  return [
    {
      target: '[data-new-session-button]',
      title: 'Start a new consultation',
      body: 'Create a new consultation for each consult. Do not add new audio to an old consult.',
    },
    {
      target: '[data-template-picker-button]',
      title: 'Choose the note template',
      body: 'Choose the template before you record. Check it again before you create the note.',
      prepare: showNote,
    },
    {
      target: '[data-tour-target="record-controls"]',
      title: 'Record the consult',
      body: 'Open the arrow and choose Recorded upload. Select Start recording when the consult begins.',
    },
    {
      target: [
        '[data-note-selector-wrap]',
        '[data-tab-panel="output"] .structured-workspace__body',
      ],
      title: 'Write the working note',
      body: 'Add brief points here during the consult. The working note stays separate from the transcript.',
      prepare: showNote,
    },
    {
      target: '[data-record-toggle]',
      title: 'Stop the recording',
      body: 'Select Stop when the consult ends. Wait while OpenScribe turns the audio into text.',
    },
    {
      target: '[data-dictation-cta]',
      title: 'Add your dictation',
      body: 'Select Add dictation. Record your summary, then stop and save it.',
    },
    {
      target: '[data-template-picker-button]',
      title: 'Check the template',
      body: 'Make sure the right template is still selected before you create the note.',
      prepare: showNote,
    },
    {
      target: '[data-generate-output-form]',
      title: 'Create the note',
      body: 'Select Create. OpenScribe uses the transcript, working note, and saved dictation.',
      prepare: showNote,
    },
    {
      target: [
        '[data-generated-structured-panel]',
        '[data-generated-freeform-panel]',
        '[data-latest-generated-output]',
      ],
      title: 'Edit the note',
      body: 'Read the whole draft. Edit any line that is wrong, unclear, or in the wrong place.',
      prepare: showNote,
    },
    {
      target: [
        '[data-note-editor-toolbar]',
        '.note-header-right',
      ],
      title: 'Choose, move, and copy lines',
      body: 'Select or clear lines. Drag lines up or down. Copy one section or all selected lines.',
      prepare: showNote,
    },
    {
      target: '[data-tab-trigger="followups"]',
      title: 'Open Follow Ups',
      body: 'Use Follow Ups for letters, messages, tasks, and other text based on this consult.',
      prepare: showNote,
    },
    {
      target: '[data-quick-action-context-input]',
      title: 'Add context',
      body: 'Type what you need, or select Record context and dictate it.',
      prepare: showFollowUps,
    },
    {
      target: [
        '[data-quick-action-quick-picks]',
        '[data-quick-action-search]',
      ],
      title: 'Use a quick action if useful',
      body: 'Choose a quick action to add its saved instructions. Leave it blank for a plain follow-up.',
      prepare: showFollowUps,
    },
    {
      target: '[data-run-quick-action-trigger]',
      title: 'Generate the follow-up',
      body: 'Select Generate. OpenScribe uses the transcript and the context you added.',
      prepare: showFollowUps,
    },
    {
      target: [
        '[data-followup-recent-list]',
        '[data-latest-followup-output]',
      ],
      title: 'Make another version',
      body: 'Select a result from Recent. Change the context or quick action, then select Generate again.',
      prepare: showFollowUps,
    },
  ];
}

export function createGuidedTour({
  dom,
  steps,
  storageKey,
}) {
  const {
    guideStartButtons,
    tourOverlay,
    tourHighlight,
    tourCard,
    tourScrims,
    tourTitle,
    tourBody,
    tourProgress,
    tourBackButton,
    tourNextButton,
    tourCloseButtons,
  } = dom;

  const isScribeTour = storageKey?.startsWith(SCRIBE_TOUR_STORAGE_PREFIX);
  const tourSteps = isScribeTour ? createScribeWorkflowSteps() : steps;
  const effectiveStorageKey = isScribeTour
    ? `${storageKey}:${SCRIBE_WORKFLOW_VERSION}`
    : storageKey;

  let activeTourStepIndex = 0;
  let renderVersion = 0;
  let previousFocus = null;
  let initialTabName = null;

  const setScrim = (panel, { top, left, width, height }) => {
    if (!panel) return;
    panel.style.top = `${top}px`;
    panel.style.left = `${left}px`;
    panel.style.width = `${Math.max(0, width)}px`;
    panel.style.height = `${Math.max(0, height)}px`;
  };

  const restoreInitialTab = () => {
    if (!initialTabName) return;
    activateTab(initialTabName);
    initialTabName = null;
  };

  const closeTour = ({ remember = true, restoreTab = true } = {}) => {
    renderVersion += 1;
    if (tourOverlay) {
      tourOverlay.hidden = true;
      tourOverlay.setAttribute('aria-hidden', 'true');
    }
    if (remember && effectiveStorageKey) {
      try {
        window.localStorage.setItem(effectiveStorageKey, 'done');
      } catch (_) {
        // The guide still works when browser privacy settings block localStorage.
      }
    }
    if (restoreTab) restoreInitialTab();
    if (previousFocus?.isConnected) previousFocus.focus();
    previousFocus = null;
  };

  const positionCard = ({ highlightTop, highlightLeft, highlightRight, highlightBottom }) => {
    if (!tourCard) return;
    const gap = 16;
    const margin = 16;
    const cardRect = tourCard.getBoundingClientRect();
    const cardWidth = cardRect.width || Math.min(384, window.innerWidth - margin * 2);
    const cardHeight = cardRect.height || 220;
    const candidates = [
      { top: highlightBottom + gap, left: highlightLeft },
      { top: highlightTop - cardHeight - gap, left: highlightLeft },
      { top: highlightTop, left: highlightRight + gap },
      { top: highlightTop, left: highlightLeft - cardWidth - gap },
    ];
    const fitting = candidates.find(({ top, left }) => (
      top >= margin
      && left >= margin
      && top + cardHeight <= window.innerHeight - margin
      && left + cardWidth <= window.innerWidth - margin
    ));
    const chosen = fitting || candidates[0];
    const top = Math.min(
      window.innerHeight - cardHeight - margin,
      Math.max(margin, chosen.top),
    );
    const left = Math.min(
      window.innerWidth - cardWidth - margin,
      Math.max(margin, chosen.left),
    );
    tourCard.style.top = `${top}px`;
    tourCard.style.left = `${left}px`;
  };

  const positionTourStep = async (target, version) => {
    target.scrollIntoView?.({ block: 'center', inline: 'nearest' });
    await nextFrame();
    await nextFrame();
    if (version !== renderVersion || tourOverlay?.hidden) return;

    const rect = target.getBoundingClientRect();
    const padding = 10;
    const highlightTop = Math.max(8, rect.top - padding);
    const highlightLeft = Math.max(8, rect.left - padding);
    const highlightWidth = Math.min(
      window.innerWidth - highlightLeft - 8,
      Math.max(32, rect.width + padding * 2),
    );
    const highlightHeight = Math.min(
      window.innerHeight - highlightTop - 8,
      Math.max(32, rect.height + padding * 2),
    );
    const highlightRight = Math.min(window.innerWidth - 8, highlightLeft + highlightWidth);
    const highlightBottom = Math.min(window.innerHeight - 8, highlightTop + highlightHeight);

    tourHighlight.style.top = `${highlightTop}px`;
    tourHighlight.style.left = `${highlightLeft}px`;
    tourHighlight.style.width = `${highlightWidth}px`;
    tourHighlight.style.height = `${highlightHeight}px`;

    setScrim(tourScrims?.top, {
      top: 0,
      left: 0,
      width: window.innerWidth,
      height: highlightTop,
    });
    setScrim(tourScrims?.right, {
      top: highlightTop,
      left: highlightRight,
      width: window.innerWidth - highlightRight,
      height: highlightHeight,
    });
    setScrim(tourScrims?.bottom, {
      top: highlightBottom,
      left: 0,
      width: window.innerWidth,
      height: window.innerHeight - highlightBottom,
    });
    setScrim(tourScrims?.left, {
      top: highlightTop,
      left: 0,
      width: highlightLeft,
      height: highlightHeight,
    });

    positionCard({ highlightTop, highlightLeft, highlightRight, highlightBottom });
  };

  const renderTourStep = async ({ direction = 1 } = {}) => {
    if (!tourOverlay || !tourHighlight || !tourCard || !tourSteps?.length) return;
    const version = ++renderVersion;
    let attempts = 0;
    let step = null;
    let target = null;

    while (attempts < tourSteps.length) {
      step = tourSteps[activeTourStepIndex];
      step?.prepare?.();
      await nextFrame();
      if (version !== renderVersion || tourOverlay.hidden) return;
      target = step ? findTarget(step.target) : null;
      if (target && isVisible(target)) break;
      activeTourStepIndex += direction;
      if (activeTourStepIndex < 0 || activeTourStepIndex >= tourSteps.length) {
        closeTour({ remember: false });
        return;
      }
      attempts += 1;
    }

    if (!step || !target) {
      closeTour({ remember: false });
      return;
    }

    if (tourTitle) tourTitle.textContent = step.title;
    if (tourBody) tourBody.textContent = step.body;
    if (tourProgress) tourProgress.textContent = `${activeTourStepIndex + 1} of ${tourSteps.length}`;
    if (tourBackButton) tourBackButton.disabled = activeTourStepIndex === 0;
    if (tourNextButton) {
      tourNextButton.textContent = activeTourStepIndex === tourSteps.length - 1 ? 'Finish' : 'Next';
    }

    await positionTourStep(target, version);
  };

  const startTour = ({ force = false } = {}) => {
    if (!tourOverlay || !tourSteps?.length) return;
    if (!force && effectiveStorageKey) {
      try {
        if (window.localStorage.getItem(effectiveStorageKey) === 'done') return;
      } catch (_) {
        // The guide still works when browser privacy settings block localStorage.
      }
    }

    previousFocus = document.activeElement;
    initialTabName = document.querySelector('[data-tab-trigger].active')?.dataset.tabTrigger || null;
    activeTourStepIndex = 0;
    tourOverlay.hidden = false;
    tourOverlay.setAttribute('aria-hidden', 'false');
    tourCard.setAttribute('role', 'dialog');
    tourCard.setAttribute('aria-modal', 'true');
    void renderTourStep();
  };

  const attach = () => {
    guideStartButtons.forEach((button) => {
      button.addEventListener('click', () => startTour({ force: true }));
    });

    tourBackButton?.addEventListener('click', () => {
      activeTourStepIndex = Math.max(0, activeTourStepIndex - 1);
      void renderTourStep({ direction: -1 });
    });

    tourNextButton?.addEventListener('click', () => {
      if (activeTourStepIndex >= tourSteps.length - 1) {
        closeTour({ remember: true });
        return;
      }
      activeTourStepIndex += 1;
      void renderTourStep({ direction: 1 });
    });

    tourCloseButtons.forEach((button) => {
      button.addEventListener('click', () => closeTour({ remember: true }));
    });

    document.addEventListener('keydown', (event) => {
      if (tourOverlay?.hidden) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        closeTour({ remember: true });
      } else if (event.key === 'ArrowLeft' && activeTourStepIndex > 0) {
        event.preventDefault();
        activeTourStepIndex -= 1;
        void renderTourStep({ direction: -1 });
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        if (activeTourStepIndex >= tourSteps.length - 1) {
          closeTour({ remember: true });
        } else {
          activeTourStepIndex += 1;
          void renderTourStep({ direction: 1 });
        }
      }
    });

    window.addEventListener('resize', () => {
      if (!tourOverlay?.hidden) void renderTourStep();
    });

    window.setTimeout(() => {
      startTour();
    }, 500);
  };

  return {
    attach,
    closeTour,
    renderTourStep,
    startTour,
  };
}
