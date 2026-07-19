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
  const toggle = document.querySelector('[data-session-panel-toggle]');
  if (toggle?.getAttribute('aria-expanded') !== 'true') toggle?.click();
  url.searchParams.delete('open_recent');
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  if (window.matchMedia('(max-width: 767px)').matches) {
    window.requestAnimationFrame(() => document.querySelector('[data-session-panel-header], [data-session-panel-close]')?.focus?.());
  }
}

let recordingActive = false;
const previousTabIndexes = new WeakMap();
function warnBeforeUnload(event) { event.preventDefault(); event.returnValue = ''; }
function setRecordingLock(locked) {
  recordingActive = Boolean(locked);
  document.querySelectorAll('[data-recording-navigation]').forEach((element) => {
    if (recordingActive && element.dataset.recordingOriginalTitle === undefined) element.dataset.recordingOriginalTitle = element.getAttribute('title') || '';
    element.classList.toggle('workspace-navigation-disabled', recordingActive);
    if (recordingActive) element.title = RECORDING_MESSAGE;
    else if (element.dataset.recordingOriginalTitle !== undefined) element.title = element.dataset.recordingOriginalTitle;
    if (element instanceof HTMLButtonElement) element.disabled = recordingActive;
    if (element instanceof HTMLAnchorElement) {
      if (recordingActive) {
        previousTabIndexes.set(element, element.getAttribute('tabindex'));
        element.setAttribute('aria-disabled', 'true'); element.setAttribute('tabindex', '-1');
      } else {
        element.removeAttribute('aria-disabled');
        const previous = previousTabIndexes.get(element);
        if (previous === null || previous === undefined) element.removeAttribute('tabindex'); else element.setAttribute('tabindex', previous);
      }
    }
  });
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

function initDrawer() {
  const drawer = document.querySelector('#workspace-sidebar');
  const toggle = document.querySelector('[data-workspace-drawer-toggle]');
  const close = document.querySelector('[data-workspace-drawer-close]');
  if (!shell || !drawer || !toggle || !close) return;
  const mobile = window.matchMedia('(max-width: 767px)');
  const setOpen = (open, restoreFocus = false) => {
    const actual = Boolean(open && mobile.matches);
    document.body.classList.toggle('workspace-drawer-open', actual);
    toggle.setAttribute('aria-expanded', String(actual));
    drawer.toggleAttribute('inert', mobile.matches && !actual);
    if (actual) drawer.focus(); else if (restoreFocus) toggle.focus();
  };
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  close.addEventListener('click', () => setOpen(false, true));
  drawer.addEventListener('click', (event) => { if (mobile.matches && event.target.closest('a, button[type="submit"]')) setOpen(false); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') setOpen(false, true); });
  mobile.addEventListener?.('change', () => setOpen(false));
  setOpen(false);
}

function initSidebarSizing() {
  if (!shell || shell.hasAttribute('data-workspace-scribe-shell')) return;
  const sidebar = shell.querySelector('[data-primary-sidebar]');
  const resize = shell.querySelector('[data-sidebar-resize]');
  const toggles = shell.querySelectorAll('[data-sidebar-collapse-toggle]');
  if (!sidebar || !resize) return;
  const desktop = window.matchMedia('(min-width: 768px)');
  const key = 'openscribe:workspace:sidebar-width';
  const clamp = (width) => width <= 112 ? 64 : Math.min(384, Math.max(192, width));
  const apply = (width, persist = false) => { if (!desktop.matches) return; const next = clamp(width); const collapsed = next === 64; sidebar.style.width = `${next}px`; sidebar.classList.toggle('workspace-sidebar--collapsed', collapsed); resize.setAttribute('aria-valuenow', String(next)); toggles.forEach((button) => { const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar'; button.setAttribute('aria-label', label); button.title = label; }); if (persist) try { localStorage.setItem(key, String(next)); } catch (_) {} };
  let stored = 288; try { stored = Number(localStorage.getItem(key)) || 288; } catch (_) {}
  apply(stored);
  toggles.forEach((button) => button.addEventListener('click', () => apply(sidebar.getBoundingClientRect().width === 64 ? 288 : 64, true)));
  let startX = 0; let startWidth = 0;
  resize.addEventListener('pointerdown', (event) => { if (!desktop.matches) return; startX = event.clientX; startWidth = sidebar.getBoundingClientRect().width; resize.setPointerCapture?.(event.pointerId); });
  resize.addEventListener('pointermove', (event) => { if (resize.hasPointerCapture?.(event.pointerId)) apply(startWidth + event.clientX - startX); });
  resize.addEventListener('pointerup', (event) => { if (!resize.hasPointerCapture?.(event.pointerId)) return; apply(sidebar.getBoundingClientRect().width, true); resize.releasePointerCapture?.(event.pointerId); });
}

rememberActiveTranscript(); initDrawer(); initSidebarSizing(); setRecordingLock(false);
window.requestAnimationFrame(openRecentFromQuery);
window.lucide?.createIcons();

export { LAST_TRANSCRIPT_KEY, RECORDING_MESSAGE, backToScribeUrl, setRecordingLock };
