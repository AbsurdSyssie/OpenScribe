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

  let activeTourStepIndex = 0;

  const closeTour = ({ remember = true } = {}) => {
    if (tourOverlay) {
      tourOverlay.hidden = true;
    }
    if (remember) {
      window.localStorage.setItem(storageKey, "done");
    }
  };

  const renderTourStep = () => {
    if (!tourOverlay || !tourHighlight || !tourCard) return;
    const step = steps[activeTourStepIndex];
    const target = step ? document.querySelector(step.target) : null;
    if (!step || !target) {
      closeTour({ remember: true });
      return;
    }
    const rect = target.getBoundingClientRect();
    const padding = 10;
    const highlightTop = Math.max(8, rect.top - padding);
    const highlightLeft = Math.max(8, rect.left - padding);
    const highlightWidth = Math.min(window.innerWidth - highlightLeft - 8, rect.width + padding * 2);
    const highlightHeight = Math.min(window.innerHeight - highlightTop - 8, rect.height + padding * 2);
    const highlightRight = Math.min(window.innerWidth - 8, highlightLeft + highlightWidth);
    const highlightBottom = Math.min(window.innerHeight - 8, highlightTop + highlightHeight);
    tourHighlight.style.top = `${highlightTop}px`;
    tourHighlight.style.left = `${highlightLeft}px`;
    tourHighlight.style.width = `${highlightWidth}px`;
    tourHighlight.style.height = `${highlightHeight}px`;
    if (tourScrims?.top) {
      tourScrims.top.style.top = '0px';
      tourScrims.top.style.left = '0px';
      tourScrims.top.style.width = '100vw';
      tourScrims.top.style.height = `${highlightTop}px`;
    }
    if (tourScrims?.right) {
      tourScrims.right.style.top = `${highlightTop}px`;
      tourScrims.right.style.left = `${highlightRight}px`;
      tourScrims.right.style.width = `${Math.max(0, window.innerWidth - highlightRight)}px`;
      tourScrims.right.style.height = `${highlightHeight}px`;
    }
    if (tourScrims?.bottom) {
      tourScrims.bottom.style.top = `${highlightBottom}px`;
      tourScrims.bottom.style.left = '0px';
      tourScrims.bottom.style.width = '100vw';
      tourScrims.bottom.style.height = `${Math.max(0, window.innerHeight - highlightBottom)}px`;
    }
    if (tourScrims?.left) {
      tourScrims.left.style.top = `${highlightTop}px`;
      tourScrims.left.style.left = '0px';
      tourScrims.left.style.width = `${highlightLeft}px`;
      tourScrims.left.style.height = `${highlightHeight}px`;
    }
    if (tourTitle) {
      tourTitle.textContent = step.title;
    }
    if (tourBody) {
      tourBody.textContent = step.body;
    }
    if (tourProgress) {
      tourProgress.textContent = `${activeTourStepIndex + 1} of ${steps.length}`;
    }
    if (tourBackButton) {
      tourBackButton.disabled = activeTourStepIndex === 0;
    }
    if (tourNextButton) {
      tourNextButton.textContent = activeTourStepIndex === steps.length - 1 ? "Finish" : "Next";
    }
    const cardTop = Math.min(window.innerHeight - 220, highlightTop + highlightHeight + 16);
    const cardLeft = Math.min(window.innerWidth - 380, Math.max(16, highlightLeft));
    tourCard.style.top = `${Math.max(16, cardTop)}px`;
    tourCard.style.left = `${Math.max(16, cardLeft)}px`;
  };

  const startTour = ({ force = false } = {}) => {
    if (!tourOverlay) return;
    if (!force && window.localStorage.getItem(storageKey) === "done") {
      return;
    }
    activeTourStepIndex = 0;
    tourOverlay.hidden = false;
    renderTourStep();
  };

  const attach = () => {
    guideStartButtons.forEach((button) => {
      button.addEventListener("click", () => startTour({ force: true }));
    });

    tourBackButton?.addEventListener("click", () => {
      activeTourStepIndex = Math.max(0, activeTourStepIndex - 1);
      renderTourStep();
    });

    tourNextButton?.addEventListener("click", () => {
      if (activeTourStepIndex >= steps.length - 1) {
        closeTour({ remember: true });
        return;
      }
      activeTourStepIndex += 1;
      renderTourStep();
    });

    tourCloseButtons.forEach((button) => {
      button.addEventListener("click", () => closeTour({ remember: true }));
    });

    window.addEventListener("resize", () => {
      if (!tourOverlay?.hidden) {
        renderTourStep();
      }
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
