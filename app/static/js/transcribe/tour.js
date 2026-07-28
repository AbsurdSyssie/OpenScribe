const SCRIBE_TOUR_STORAGE_PREFIX = 'openscribe:tour:transcribe:';
const SCRIBE_WORKFLOW_VERSION = 'workflow-v3';
const TUTORIAL_TEMPLATE_NAME = 'Tutorial sectioned note';

const nextFrame = () => new Promise((resolve) => window.requestAnimationFrame(resolve));

const activateTab = (tabName) => {
  const trigger = document.querySelector(`[data-tab-trigger="${tabName}"]`);
  if (trigger && !trigger.classList.contains('active')) {
    trigger.click();
  }
};

const clickFirst = (selectors, predicate = () => true) => {
  for (const selector of selectors) {
    const match = [...document.querySelectorAll(selector)].find(predicate);
    if (match) {
      match.click();
      return match;
    }
  }
  return null;
};

const selectTutorialTemplate = () => {
  const select = document.querySelector('[data-template-select]');
  if (!select) return;
  const option = [...select.options].find((item) => item.dataset.templateName === TUTORIAL_TEMPLATE_NAME);
  if (!option || select.value === option.value) return;
  select.value = option.value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
};

const selectWorkingNote = () => {
  activateTab('output');
  selectTutorialTemplate();
  clickFirst(
    ['[data-note-document-select]'],
    (button) => String(button.dataset.documentId || '').startsWith('working:'),
  );
};

const selectGeneratedNote = () => {
  activateTab('output');
  selectTutorialTemplate();
  clickFirst(
    ['[data-note-document-select]'],
    (button) => !String(button.dataset.documentId || '').startsWith('working:'),
  );
};

const showTranscript = () => activateTab('history');
const showFollowUps = () => activateTab('followups');

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
  return [
    {
      target: '[data-new-session-button]',
      title: 'Tutorial example',
      body: 'This is a tutorial example. For real work, select New consultation for each consult.',
      prepare: selectWorkingNote,
    },
    {
      target: '[data-template-picker-button]',
      title: 'Choose the note template',
      body: 'Choose the template before you record. The template tells the AI how to write the note and what information should go where.',
      prepare: selectWorkingNote,
    },
    {
      target: '[data-tour-target="record-controls"]',
      title: 'Record the consult',
      body: 'Choose Recorded upload, then select Start recording.',
      prepare: selectWorkingNote,
    },
    {
      target: [
        '[data-note-selector-wrap]',
        '[data-tab-panel="output"] .structured-workspace__body',
      ],
      title: 'Write the working note',
      body: 'Add short points during the consult. Put each point in the section where it belongs.',
      prepare: selectWorkingNote,
    },
    {
      target: '[data-record-toggle]',
      title: 'Stop the recording',
      body: 'Select Stop when the consult ends. OpenScribe then turns the recording into text.',
      prepare: selectWorkingNote,
    },
    {
      target: [
        '[data-active-draft]',
        '[data-transcript-review-grid]',
      ],
      title: 'Review the transcript',
      body: 'Read the transcript and check names, dates, medicines, and other key facts.',
      prepare: showTranscript,
    },
    {
      target: '[data-dictation-compact]',
      title: 'Add your dictation',
      body: 'Record any extra details after the consult, then stop and save the dictation.',
      prepare: showTranscript,
    },
    {
      target: '[data-template-picker-button]',
      title: 'Check the template',
      body: 'Make sure the right template is selected before you create the note.',
      prepare: selectWorkingNote,
    },
    {
      target: '[data-generate-output-form]',
      title: 'Create the note',
      body: 'Select Create. OpenScribe uses the transcript, working note, dictation, and chosen template.',
      prepare: selectWorkingNote,
    },
    {
      target: [
        '[data-generated-structured-panel]',
        '[data-generated-freeform-panel]',
        '[data-latest-generated-output]',
      ],
      title: 'Edit the note',
      body: 'Read the whole draft. Edit any line that is wrong, unclear, or in the wrong section.',
      prepare: selectGeneratedNote,
    },
    {
      target: [
        '[data-note-editor-toolbar]',
        '.note-header-right',
      ],
      title: 'Choose, move, and copy lines',
      body: 'Select or clear lines. Drag lines up or down. Copy one section or all selected lines.',
      prepare: selectGeneratedNote,
    },
    {
      target: '[data-tab-trigger="followups"]',
      title: 'Open Follow Ups',
      body: 'Use Follow Ups for letters, messages, tasks, and other text based on this consult.',
      prepare: selectGeneratedNote,
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
      body: 'Choose a quick action to add saved instructions. Leave it blank for a plain follow-up.',
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
      body: 'Select a result from Recent, change the request, then select Generate again.',
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
  let tutorialConsultationRequested = false;

  const hasActiveTranscript = () => Boolean(document.querySelector('[data-transcript-title-form]'));
  const recentConsultationLink = () => document.querySelector('[data-session-link]');

  const hasCompletedTour = () => {
    if (!effectiveStorageKey) return false;
    try {
      return window.localStorage.getItem(effectiveStorageKey) === 'done';
    } catch (_) {
      return false;
    }
  };

  const requestTutorialConsultation = () => {
    if (tutorialConsultationRequested) return;
    const form = document.querySelector('[data-tutorial-consultation-form]');
    if (!form) return;
    tutorialConsultationRequested = true;
    if (typeof form.requestSubmit === 'function') form.requestSubmit();
    else form.submit();
  };

  const openRecentConsultationForTour = () => {
    const link = recentConsultationLink();
    if (!link?.href) return false;
    const url = new URL(link.href, window.location.href);
    url.searchParams.set('tutorial', '1');
    window.location.assign(url.toString());
    return true;
  };

  const openConsultationForTour = () => {
    if (hasActiveTranscript()) return true;
    if (openRecentConsultationForTour()) return false;
    requestTutorialConsultation();
    return false;
  };

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
    if (!force && hasCompletedTour()) return;
    if (isScribeTour && !openConsultationForTour()) return;

    previousFocus = document.activeElement;
    initialTabName = document.querySelector('[data-tab-trigger].active')?.dataset.tabTrigger || null;
    activeTourStepIndex = 0;
    tourOverlay.hidden = false;
    tourOverlay.setAttribute('aria-hidden', 'false');
    tourCard.setAttribute('role', 'dialog');
    tourCard.setAttribute('aria-modal', 'true');
    void renderTourStep();
  };

  const clearTutorialQuery = () => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has('tutorial')) return;
    url.searchParams.delete('tutorial');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
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

    const tutorialRequested = isScribeTour && new URLSearchParams(window.location.search).get('tutorial') === '1';
    window.setTimeout(() => {
      startTour({ force: tutorialRequested });
      if (tutorialRequested && hasActiveTranscript()) clearTutorialQuery();
    }, tutorialRequested ? 0 : 500);
  };

  return {
    attach,
    closeTour,
    renderTourStep,
    startTour,
  };
}
