export function createGuidedTour({
  dom,
  steps,
  getCurrentTab,
  setTab,
  isBlocked = () => false,
  onBlocked = () => {},
}) {
  const {
    guideStartButtons = [],
    tourDialog,
    tourHighlight,
    tourCard,
    tourTitle,
    tourBody,
    tourProgress,
    tourAnnouncement,
    tourBackButton,
    tourNextButton,
    tourCloseButtons = [],
  } = dom;

  let activeTourStepIndex = 0;
  let originalTab = null;
  let returnFocusTarget = null;

  const visibleTarget = (selector) => {
    if (!selector) return null;
    const target = document.querySelector(selector);
    if (!target || !target.isConnected || target.closest('[hidden], [inert]')) return null;
    const style = window.getComputedStyle(target);
    if (style.display === 'none' || style.visibility === 'hidden') return null;
    const rect = target.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    if (rect.bottom <= 0 || rect.right <= 0 || rect.top >= window.innerHeight || rect.left >= window.innerWidth) return null;
    return { target, rect };
  };

  const positionHighlight = (rect) => {
    if (!tourHighlight) return;
    const padding = 8;
    const left = Math.max(4, Math.min(window.innerWidth - 4, rect.left - padding));
    const top = Math.max(4, Math.min(window.innerHeight - 4, rect.top - padding));
    const right = Math.max(left + 1, Math.min(window.innerWidth - 4, rect.right + padding));
    const bottom = Math.max(top + 1, Math.min(window.innerHeight - 4, rect.bottom + padding));
    tourHighlight.style.left = `${left}px`;
    tourHighlight.style.top = `${top}px`;
    tourHighlight.style.width = `${right - left}px`;
    tourHighlight.style.height = `${bottom - top}px`;
  };

  const positionCard = (rect) => {
    if (!tourCard) return;
    tourCard.style.transform = '';
    const margin = 12;
    const cardHeight = tourCard.getBoundingClientRect().height || 260;
    const below = rect.bottom + margin;
    const top = below + cardHeight <= window.innerHeight - margin
      ? below
      : Math.max(margin, rect.top - cardHeight - margin);
    const cardWidth = Math.min(tourCard.getBoundingClientRect().width || 420, window.innerWidth - margin * 2);
    const left = Math.max(margin, Math.min(window.innerWidth - cardWidth - margin, rect.left));
    tourCard.style.top = `${top}px`;
    tourCard.style.left = `${left}px`;
  };

  const renderTourStep = ({ focusHeading = false } = {}) => {
    if (!tourDialog?.open || !steps.length) return;
    const step = steps[activeTourStepIndex];
    if (!step) return;

    if (step.tab && getCurrentTab?.() !== step.tab) {
      setTab?.(step.tab);
    }

    const match = visibleTarget(step.target);
    const fallback = match ? null : visibleTarget('[data-tour-fallback]');
    const activeTarget = match || fallback;
    tourDialog.dataset.tourStep = String(activeTourStepIndex + 1);
    tourDialog.dataset.tourTargetState = match ? 'matched' : 'fallback';
    if (tourTitle) tourTitle.textContent = step.title;
    if (tourBody) tourBody.textContent = step.body;
    if (tourProgress) tourProgress.textContent = `Step ${activeTourStepIndex + 1} of ${steps.length}`;
    if (tourAnnouncement) {
      tourAnnouncement.textContent = `Step ${activeTourStepIndex + 1} of ${steps.length}: ${step.title}. ${step.body}`;
    }
    if (tourBackButton) tourBackButton.disabled = activeTourStepIndex === 0;
    if (tourNextButton) tourNextButton.textContent = activeTourStepIndex === steps.length - 1 ? 'Finish' : 'Next';

    if (activeTarget) {
      positionHighlight(activeTarget.rect);
      positionCard(activeTarget.rect);
      tourHighlight?.removeAttribute('hidden');
    } else {
      tourHighlight?.setAttribute('hidden', '');
      if (tourCard) {
        tourCard.style.top = '50%';
        tourCard.style.left = '50%';
        tourCard.style.transform = 'translate(-50%, -50%)';
      }
    }
    if (focusHeading) tourTitle?.focus();
  };

  const closeTour = () => {
    if (!tourDialog?.open) return;
    const tabToRestore = originalTab;
    originalTab = null;
    tourDialog.close();
    if (tabToRestore) setTab?.(tabToRestore);
    const target = returnFocusTarget;
    returnFocusTarget = null;
    if (target?.isConnected && !target.disabled && target.getClientRects().length) {
      requestAnimationFrame(() => target.focus());
    }
  };

  const startTour = (opener) => {
    if (!tourDialog || tourDialog.open) return false;
    if (isBlocked()) {
      onBlocked();
      return false;
    }
    if (typeof tourDialog.showModal !== 'function') {
      onBlocked('The guide is unavailable in this browser.');
      return false;
    }

    activeTourStepIndex = 0;
    originalTab = getCurrentTab?.() || null;
    returnFocusTarget = opener || document.activeElement;
    const firstStep = steps[0];
    if (firstStep && tourTitle && tourBody) {
      tourTitle.textContent = firstStep.title;
      tourBody.textContent = firstStep.body;
      if (tourAnnouncement) {
        tourAnnouncement.textContent = `Step 1 of ${steps.length}: ${firstStep.title}. ${firstStep.body}`;
      }
    }
    tourDialog.showModal();
    renderTourStep();
    tourCard?.focus();
    return true;
  };

  const attach = () => {
    guideStartButtons.forEach((button) => {
      button.addEventListener('click', () => startTour(button));
    });

    tourBackButton?.addEventListener('click', () => {
      activeTourStepIndex = Math.max(0, activeTourStepIndex - 1);
      renderTourStep({ focusHeading: true });
    });

    tourNextButton?.addEventListener('click', () => {
      if (activeTourStepIndex >= steps.length - 1) {
        closeTour();
        return;
      }
      activeTourStepIndex += 1;
      renderTourStep({ focusHeading: true });
    });

    tourCloseButtons.forEach((button) => button.addEventListener('click', closeTour));
    tourDialog?.addEventListener('cancel', (event) => {
      event.preventDefault();
      closeTour();
    });
    tourDialog?.addEventListener('click', (event) => {
      if (event.target === tourDialog) closeTour();
    });

    window.addEventListener('keydown', (event) => {
      if (!tourDialog?.open) return;
      if (event.key === 'Escape') {
        // Prevent the workspace drawer's Escape handler from also closing.
        event.stopPropagation();
        return;
      }
      if (event.key === 'Tab') {
        const tabbable = [...tourDialog.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )].filter((element) => (
          !element.closest('[hidden], [inert], [aria-hidden="true"]')
          && element.getClientRects().length > 0
          && window.getComputedStyle(element).visibility !== 'hidden'
        ));
        if (!tabbable.length) {
          event.preventDefault();
          tourCard?.focus();
          return;
        }
        const first = tabbable[0];
        const last = tabbable[tabbable.length - 1];
        const active = document.activeElement;
        if (!tourDialog.contains(active) || !tabbable.includes(active)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && active === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }, true);
    window.addEventListener('resize', () => {
      if (tourDialog?.open) renderTourStep();
    });
  };

  return { attach, closeTour, renderTourStep, startTour };
}
