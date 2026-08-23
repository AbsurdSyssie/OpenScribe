// Mobile Scribe presentation controller. It only coordinates the canonical
// workspace DOM; recording, documents, saving and authorisation remain in app.js.
const mobileMedia = window.matchMedia('(max-width: 767px)');
const mobileMain = document.querySelector('[data-workspace-scribe-main]');

if (mobileMain) {
  const destinations = [...document.querySelectorAll('[data-mobile-destination]')];
  const capture = document.querySelector('[data-mobile-capture-screen]');
  const panels = [...document.querySelectorAll('[data-tab-panel]')];
  const titleSource = document.querySelector('[data-session-title-display]');
  const statusSource = document.querySelector('[data-active-status]');
  const titleTarget = document.querySelector('[data-mobile-session-title]');
  const statusTarget = document.querySelector('[data-mobile-session-status]');
  const strip = document.querySelector('[data-mobile-recording-strip]');
  const stripTimer = document.querySelector('[data-mobile-recording-timer]');
  const timerSource = document.querySelector('[data-mic-timer]');
  const captureTimer = document.querySelector('[data-mobile-capture-timer]');
  const recordHero = document.querySelector('[data-record-split-button]');
  const recordingMode = document.querySelector('[data-recording-mode-select]');
  const recordingModeLabel = document.querySelector('[data-mobile-record-mode-label]');
  let destination = null;
  let recording = false;
  let sheetTrigger = null;
  let activeSheet = null;
  let sheetFocusables = [];
  let inertBackground = [];
  let activeSheetSemantics = null;

  const isMobile = () => mobileMedia.matches;
  const sheetFocusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const visibleFocusableIn = (container) => [...container.querySelectorAll(sheetFocusableSelector)].filter((element) => !element.hidden && element.getClientRects().length > 0);
  const sheetFor = (name) => (name === 'note'
    ? document.querySelector('[data-mobile-note-selector-sheet]')
    : name === 'pii'
      ? document.querySelector('[data-pii-sidebar]')
      : document.querySelector('[data-dictation-compact]'));
  const restoreSheetModalState = () => {
    inertBackground.forEach(({ element, inert }) => { element.inert = inert; });
    inertBackground = [];
    if (activeSheet && activeSheetSemantics) {
      if (activeSheetSemantics.role === null) activeSheet.removeAttribute('role');
      else activeSheet.setAttribute('role', activeSheetSemantics.role);
      if (activeSheetSemantics.ariaModal === null) activeSheet.removeAttribute('aria-modal');
      else activeSheet.setAttribute('aria-modal', activeSheetSemantics.ariaModal);
    }
    activeSheet = null;
    activeSheetSemantics = null;
    sheetFocusables = [];
  };
  const makeSheetModal = (sheet) => {
    activeSheet = sheet;
    activeSheetSemantics = {
      role: sheet.getAttribute('role'),
      ariaModal: sheet.getAttribute('aria-modal'),
    };
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    inertBackground = [...document.querySelectorAll(sheetFocusableSelector)]
      .filter((element) => !sheet.contains(element) && !element.matches('.mobile-sheet-scrim'))
      .map((element) => ({ element, inert: element.inert }));
    inertBackground.forEach(({ element }) => { element.inert = true; });
    sheetFocusables = visibleFocusableIn(sheet);
  };
  const isCaptureRequired = () => {
    const state = statusSource?.textContent?.trim().toLowerCase();
    return !state || ['idle', 'failed', 'queued', 'transcribing', 'processing', 'uploading'].includes(state);
  };
  const syncHeader = () => {
    if (titleTarget && titleSource) titleTarget.textContent = titleSource.value || titleSource.textContent || 'New consultation';
    if (statusTarget && statusSource) statusTarget.textContent = statusSource.textContent?.trim() || 'idle';
    if (stripTimer && timerSource) stripTimer.textContent = timerSource.textContent?.trim() || '00:00';
    if (captureTimer && timerSource) captureTimer.textContent = timerSource.textContent?.trim() || '00:00';
    if (recordingModeLabel && recordingMode) recordingModeLabel.textContent = recordingMode.value === 'live_chunked' ? 'Live capture' : 'Recorded upload';
  };
  const closeSheets = ({ restoreFocus = true } = {}) => {
    const trigger = sheetTrigger;
    restoreSheetModalState();
    document.body.classList.remove('mobile-note-sheet-open', 'mobile-pii-sheet-open', 'mobile-dictation-sheet-open');
    document.querySelectorAll('[data-mobile-note-versions], [data-mobile-pii-open], [data-mobile-dictation-open]').forEach((button) => button.setAttribute('aria-expanded', 'false'));
    document.querySelectorAll('[data-mobile-note-versions-close], [data-mobile-pii-close], [data-mobile-dictation-close]').forEach((button) => { if (button.matches('.mobile-sheet-scrim')) button.hidden = true; });
    if (restoreFocus && trigger?.isConnected && !trigger.inert) trigger.focus();
    sheetTrigger = null;
  };
  const openSheet = (name, trigger) => {
    if (!isMobile()) return;
    closeSheets({ restoreFocus: false });
    document.dispatchEvent(new CustomEvent('workspace:drawer-close'));
    sheetTrigger = trigger;
    const className = `mobile-${name}-sheet-open`;
    document.body.classList.add(className);
    trigger?.setAttribute('aria-expanded', 'true');
    const closeSelector = `[data-mobile-${name === 'note' ? 'note-versions' : name}-close].mobile-sheet-scrim`;
    document.querySelector(closeSelector)?.removeAttribute('hidden');
    const sheet = sheetFor(name);
    if (!sheet) return;
    makeSheetModal(sheet);
    window.requestAnimationFrame(() => (sheetFocusables[0] || sheet)?.focus());
  };
  const setDestination = (next, { initial = false } = {}) => {
    if (!isMobile()) return;
    if (!['capture', 'output', 'history', 'followups'].includes(next)) return;
    closeSheets();
    destination = next;
    mobileMain.dataset.mobileDestinationState = next;
    capture?.toggleAttribute('hidden', next !== 'capture');
    if (next === 'capture') panels.forEach((panel) => { panel.hidden = true; });
    else document.querySelector(`[data-tab-trigger="${next}"]`)?.click();
    destinations.forEach((button) => button.setAttribute('aria-current', button.dataset.mobileDestination === next ? 'page' : 'false'));
    const showRecordingStrip = Boolean(recording && next !== 'capture');
    if (strip) strip.hidden = !showRecordingStrip;
    document.body.classList.toggle('mobile-recording-strip-visible', showRecordingStrip);
  };
  const initialise = () => {
    syncHeader();
    if (!isMobile()) {
      mobileMain.removeAttribute('data-mobile-destination-state');
      capture?.removeAttribute('hidden');
      strip && (strip.hidden = true);
      document.body.classList.remove('mobile-recording-strip-visible');
      closeSheets();
      return;
    }
    const bootstrap = document.querySelector('#transcribe-bootstrap');
    let state = null;
    try { state = bootstrap ? JSON.parse(bootstrap.textContent || '{}') : null; } catch (_) { state = null; }
    const initial = state?.activeTranscriptHasContent && !isCaptureRequired() ? 'output' : 'capture';
    setDestination(initial, { initial: true });
  };

  destinations.forEach((button) => button.addEventListener('click', () => setDestination(button.dataset.mobileDestination)));
  document.querySelector('[data-mobile-note-versions]')?.addEventListener('click', (event) => openSheet('note', event.currentTarget));
  document.querySelector('[data-mobile-pii-open]')?.addEventListener('click', (event) => openSheet('pii', event.currentTarget));
  document.querySelector('[data-mobile-dictation-open]')?.addEventListener('click', (event) => openSheet('dictation', event.currentTarget));
  document.querySelectorAll('[data-mobile-note-versions-close], [data-mobile-pii-close], [data-mobile-dictation-close]').forEach((button) => button.addEventListener('click', closeSheets));
  document.addEventListener('transcribe:document-selected', (event) => {
    if (event.detail?.kind === 'note') closeSheets();
  });
  document.addEventListener('openscribe:dictation-modal-opening', () => {
    if (isMobile() && activeSheet) closeSheets({ restoreFocus: false });
  });
  document.querySelector('[data-mobile-recording-strip] [data-mobile-capture-return]')?.addEventListener('click', () => setDestination('capture'));
  document.querySelector('[data-mobile-recording-strip] [data-mobile-recording-stop]')?.addEventListener('click', () => document.querySelector('[data-record-toggle]')?.click());
  recordingMode?.addEventListener('change', syncHeader);
  document.addEventListener('keydown', (event) => {
    if (!isMobile() || !activeSheet) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeSheets();
      return;
    }
    if (event.key !== 'Tab') return;
    sheetFocusables = visibleFocusableIn(activeSheet);
    if (sheetFocusables.length === 0) {
      event.preventDefault();
      activeSheet.focus();
      return;
    }
    const first = sheetFocusables[0];
    const last = sheetFocusables[sheetFocusables.length - 1];
    if (event.shiftKey && (document.activeElement === first || !activeSheet.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !activeSheet.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });
  document.addEventListener('openscribe:recording-started', () => {
    recording = true;
    recordHero?.setAttribute('data-mobile-recording', 'true');
    setDestination('capture', { initial: true });
  });
  ['openscribe:recording-stopped', 'openscribe:recording-cancelled', 'openscribe:recording-failed'].forEach((eventName) => document.addEventListener(eventName, () => {
    recording = false;
    recordHero?.removeAttribute('data-mobile-recording');
    document.body.classList.remove('mobile-recording-strip-visible');
    if (strip) strip.hidden = true;
  }));
  titleSource?.addEventListener?.('input', syncHeader);
  new MutationObserver(syncHeader).observe(titleSource || mobileMain, { childList: true, characterData: true, subtree: Boolean(titleSource) });
  if (statusSource) new MutationObserver(syncHeader).observe(statusSource, { childList: true, characterData: true, subtree: true });
  if (timerSource) new MutationObserver(syncHeader).observe(timerSource, { childList: true, characterData: true, subtree: true });
  mobileMedia.addEventListener?.('change', initialise);
  initialise();
}
