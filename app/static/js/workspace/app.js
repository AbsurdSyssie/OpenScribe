const LAST_TRANSCRIPT_KEY = 'openscribe.workspace.lastTranscriptId';
const RECORDING_MESSAGE = 'Finish or cancel the recording before leaving Scribe.';
const shell = document.querySelector('[data-workspace-shell]');

function rememberActiveTranscript() {
  const id = document.body.dataset.activeTranscriptId?.trim();
  if (!id) return;
  try { window.sessionStorage.setItem(LAST_TRANSCRIPT_KEY, id); } catch (_) {}
}

function rememberedTranscript() {
  try { return window.sessionStorage.getItem(LAST_TRANSCRIPT_KEY)?.trim() || ''; } catch (_) { return ''; }
}

function backToScribeUrl(anchor) {
  const url = new URL(anchor.href, window.location.href);
  const id = rememberedTranscript();
  if (id) url.searchParams.set('transcript_id', id);
  url.searchParams.set('open_recent', '1');
  return url;
}

function isVisibleSessionToggle(element) {
  return Boolean(
    element
    && !element.disabled
    && !element.hidden
    && !element.closest('[hidden], [inert]')
    && element.getClientRects().length,
  );
}

function preferredSessionPanelToggle() {
  const toggles = [...document.querySelectorAll('[data-session-panel-toggle]')];
  const mobile = window.matchMedia('(max-width: 767px)').matches;
  if (mobile) {
    const mobileToggle = toggles.find((toggle) => (
      toggle.closest('.workspace-mobile-header') && isVisibleSessionToggle(toggle)
    ));
    if (mobileToggle) return mobileToggle;
  }
  return toggles.find(isVisibleSessionToggle) || null;
}

document.querySelectorAll('[data-back-to-scribe]').forEach((anchor) => {
  anchor.addEventListener('click', (event) => {
    if (anchor.getAttribute('aria-disabled') === 'true') return;
    event.preventDefault();
    window.location.assign(backToScribeUrl(anchor));
  });
});

function openRecentFromQuery() {
  if (document.body.dataset.workspaceSection !== 'scribe') return;
  const url = new URL(window.location.href);
  if (url.searchParams.get('open_recent') !== '1') return;
  const toggle = preferredSessionPanelToggle();
  if (toggle?.getAttribute('aria-expanded') !== 'true') toggle?.click();
  url.searchParams.delete('open_recent');
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  if (window.matchMedia('(max-width: 767px)').matches) {
    window.requestAnimationFrame(() => document.querySelector('[data-session-panel-header], [data-session-panel-close]')?.focus?.());
  }
}

let recordingActive = false;
const recordingNavigationStates = new WeakMap();
function warnBeforeUnload(event) { event.preventDefault(); event.returnValue = ''; }
function applyRecordingLock(element, locked = recordingActive) {
  if (!element?.matches?.('[data-recording-navigation]')) return;
  if (locked) {
    if (!recordingNavigationStates.has(element)) {
      recordingNavigationStates.set(element, {
        title: element.getAttribute('title'),
        ariaDisabled: element.getAttribute('aria-disabled'),
        tabIndex: element.getAttribute('tabindex'),
        disabled: element instanceof HTMLButtonElement ? element.disabled : null,
      });
    }
    element.classList.add('workspace-navigation-disabled');
    element.title = RECORDING_MESSAGE;
    if (element instanceof HTMLButtonElement) element.disabled = true;
    if (element instanceof HTMLAnchorElement) {
      element.setAttribute('aria-disabled', 'true');
      element.setAttribute('tabindex', '-1');
    }
    return;
  }
  const original = recordingNavigationStates.get(element);
  if (!original) return;
  element.classList.remove('workspace-navigation-disabled');
  if (original.title === null) element.removeAttribute('title'); else element.setAttribute('title', original.title);
  if (element instanceof HTMLButtonElement) element.disabled = original.disabled;
  if (element instanceof HTMLAnchorElement) {
    if (original.ariaDisabled === null) element.removeAttribute('aria-disabled'); else element.setAttribute('aria-disabled', original.ariaDisabled);
    if (original.tabIndex === null) element.removeAttribute('tabindex'); else element.setAttribute('tabindex', original.tabIndex);
  }
  recordingNavigationStates.delete(element);
}
function setRecordingLock(locked) {
  recordingActive = Boolean(locked);
  document.querySelectorAll('[data-recording-navigation]').forEach((element) => applyRecordingLock(element));
  window.removeEventListener('beforeunload', warnBeforeUnload);
  if (recordingActive) window.addEventListener('beforeunload', warnBeforeUnload);
}
document.addEventListener('click', (event) => {
  if (recordingActive && event.target.closest('[data-recording-navigation]')) { event.preventDefault(); event.stopImmediatePropagation(); }
}, true);
document.addEventListener('openscribe:recording-started', () => setRecordingLock(true));
document.addEventListener('openscribe:recording-stopped', () => setRecordingLock(false));
document.addEventListener('openscribe:recording-cancelled', () => setRecordingLock(false));
document.addEventListener('openscribe:recording-failed', () => setRecordingLock(false));
document.addEventListener('openscribe:recording-navigation-added', (event) => {
  applyRecordingLock(event.detail?.element);
});

function initDrawer() {
  const drawer = document.querySelector('#workspace-sidebar');
  const toggle = document.querySelector('[data-workspace-drawer-toggle]');
  const scrim = document.querySelector('[data-workspace-drawer-close]');
  const drawerClose = document.querySelector('[data-drawer-dismiss]');
  if (!shell || !drawer || !toggle || !scrim) return;
  const mobile = window.matchMedia('(max-width: 767px)');
  const inertTargets = [
    ...shell.children,
    ...[...document.body.children].filter((element) => (
      element !== shell && element.tagName !== 'SCRIPT' && !element.matches('[data-tour-overlay]')
    )),
  ];
  const drawerInitiallyInert = drawer.hasAttribute('inert');
  let inertSnapshot = null;
  const setOpen = (open, restoreFocus = false) => {
    const actual = Boolean(open && mobile.matches);
    document.body.classList.toggle('workspace-drawer-open', actual);
    toggle.setAttribute('aria-expanded', String(actual));
    if (actual) {
      if (!inertSnapshot) inertSnapshot = new Map(inertTargets.map((element) => [element, element.hasAttribute('inert')]));
      for (const element of inertTargets) {
        if (element !== drawer && element !== scrim) element.setAttribute('inert', '');
      }
      drawer.removeAttribute('inert');
    } else if (inertSnapshot) {
      for (const [element, wasInert] of inertSnapshot) element.toggleAttribute('inert', wasInert);
      inertSnapshot = null;
      drawer.toggleAttribute('inert', mobile.matches || drawerInitiallyInert);
    } else {
      drawer.toggleAttribute('inert', mobile.matches || drawerInitiallyInert);
    }
    if (actual) drawer.focus(); else if (restoreFocus) toggle.focus();
  };
  toggle.addEventListener('click', () => {
    const opening = toggle.getAttribute('aria-expanded') !== 'true';
    if (opening) document.dispatchEvent(new CustomEvent('workspace:drawer-opening'));
    setOpen(opening);
  });
  scrim.addEventListener('click', () => setOpen(false, true));
  drawerClose?.addEventListener('click', () => setOpen(false, true));
  drawer.addEventListener('click', (event) => { if (mobile.matches && event.target.closest('a, button[type="submit"]')) setOpen(false); });
  document.addEventListener('workspace:drawer-close', () => setOpen(false));
  document.addEventListener('keydown', (event) => {
    if (toggle.getAttribute('aria-expanded') !== 'true') return;
    // A native modal owns focus and keyboard dismissal while it is open.
    if (document.querySelector('dialog:modal')) return;
    if (event.key === 'Escape') {
      setOpen(false, true);
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...drawer.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])')]
      .filter((element) => element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[hidden], [inert]') && element.getClientRects().length);
    if (!focusable.length) {
      event.preventDefault();
      drawer.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });
  mobile.addEventListener?.('change', () => setOpen(false));
  setOpen(false);
}

function initSidebarSizing() {
  if (!shell || shell.hasAttribute('data-workspace-scribe-shell')) return;
  const sidebar = shell.querySelector('[data-primary-sidebar]');
  const resize = shell.querySelector('[data-sidebar-resize]');
  const toggles = shell.querySelectorAll('[data-sidebar-collapse-toggle]');
  const stepControls = shell.querySelectorAll('[data-sidebar-step]');
  if (!sidebar || !resize) return;
  const desktop = window.matchMedia('(min-width: 768px)');
  const key = 'openscribe:workspace:sidebar-width';
  const clamp = (width) => width <= 112 ? 64 : Math.min(384, Math.max(192, width));
  const apply = (width, persist = false) => { if (!desktop.matches) return; const next = clamp(width); const collapsed = next === 64; sidebar.style.width = `${next}px`; sidebar.classList.toggle('workspace-sidebar--collapsed', collapsed); resize.setAttribute('aria-valuenow', String(next)); toggles.forEach((button) => { const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar'; button.setAttribute('aria-label', label); button.title = label; }); if (persist) try { localStorage.setItem(key, String(next)); } catch (_) {} };
  let stored = 288; try { stored = Number(localStorage.getItem(key)) || 288; } catch (_) {}
  apply(stored);
  toggles.forEach((button) => button.addEventListener('click', () => apply(sidebar.getBoundingClientRect().width === 64 ? 288 : 64, true)));
  stepControls.forEach((button) => button.addEventListener('click', () => {
    const step = Number(button.dataset.sidebarStep);
    if (Number.isFinite(step)) apply(sidebar.getBoundingClientRect().width + step, true);
  }));
  let startX = 0; let startWidth = 0;
  resize.addEventListener('pointerdown', (event) => { if (!desktop.matches) return; startX = event.clientX; startWidth = sidebar.getBoundingClientRect().width; resize.setPointerCapture?.(event.pointerId); });
  resize.addEventListener('pointermove', (event) => { if (resize.hasPointerCapture?.(event.pointerId)) apply(startWidth + event.clientX - startX); });
  resize.addEventListener('pointerup', (event) => { if (!resize.hasPointerCapture?.(event.pointerId)) return; apply(sidebar.getBoundingClientRect().width, true); resize.releasePointerCapture?.(event.pointerId); });
  resize.addEventListener('keydown', (event) => {
    if (!desktop.matches) return;
    const current = sidebar.getBoundingClientRect().width;
    if (event.key === 'ArrowLeft') apply(current <= 192 ? 64 : current - 16, true);
    else if (event.key === 'ArrowRight') apply(current <= 64 ? 192 : current + 16, true);
    else if (event.key === 'Home') apply(64, true);
    else if (event.key === 'End') apply(384, true);
    else return;
    event.preventDefault();
  });
}

rememberActiveTranscript(); initDrawer(); initSidebarSizing(); setRecordingLock(false);
window.requestAnimationFrame(openRecentFromQuery);
window.lucide?.createIcons();

export { LAST_TRANSCRIPT_KEY, RECORDING_MESSAGE, applyRecordingLock, backToScribeUrl, setRecordingLock };
