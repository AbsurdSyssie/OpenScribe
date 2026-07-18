(function () {
  const MOBILE_MEDIA_QUERY = '(max-width: 767px)';
  const shell = document.querySelector('[data-workspace-endpoint]');
  const sidebar = shell?.querySelector('aside');
  const headerTitleRow = document.querySelector('main > header > div:first-child');

  if (!shell || !sidebar || !headerTitleRow) return;

  const mediaQuery = window.matchMedia(MOBILE_MEDIA_QUERY);

  const scrim = document.createElement('button');
  scrim.type = 'button';
  scrim.className = 'mobile-sidebar-scrim';
  scrim.setAttribute('aria-label', 'Close consultation list');
  scrim.setAttribute('data-mobile-sidebar-close', '');

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'mobile-sidebar-toggle';
  toggle.setAttribute('aria-label', 'Open consultation list');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'transcribe-consultation-list');
  toggle.setAttribute('data-mobile-sidebar-toggle', '');
  toggle.innerHTML = '<i class="w-4 h-4" data-lucide="menu" aria-hidden="true"></i>';

  sidebar.id = sidebar.id || 'transcribe-consultation-list';
  sidebar.setAttribute('aria-label', 'Consultation list');
  sidebar.setAttribute('tabindex', '-1');

  shell.insertBefore(scrim, shell.firstChild);
  headerTitleRow.insertBefore(toggle, headerTitleRow.firstChild);

  const setOpen = (open) => {
    document.body.classList.toggle('mobile-sidebar-open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');

    if (open) {
      sidebar.removeAttribute('inert');
      sidebar.focus?.();
      return;
    }

    if (mediaQuery.matches) {
      sidebar.setAttribute('inert', '');
    } else {
      sidebar.removeAttribute('inert');
    }
  };

  const syncMode = () => {
    setOpen(false);
    if (mediaQuery.matches) {
      sidebar.setAttribute('inert', '');
    } else {
      sidebar.removeAttribute('inert');
    }
  };

  toggle.addEventListener('click', () => {
    setOpen(!document.body.classList.contains('mobile-sidebar-open'));
  });

  scrim.addEventListener('click', () => setOpen(false));

  document.addEventListener('transcribe:mobile-sidebar-close', () => setOpen(false));

  sidebar.addEventListener('click', (event) => {
    if (!mediaQuery.matches) return;
    if (event.target.closest('[data-session-link]')) {
      setOpen(false);
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && document.body.classList.contains('mobile-sidebar-open')) {
      setOpen(false);
      toggle.focus();
    }
  });

  mediaQuery.addEventListener?.('change', syncMode);
  syncMode();

  window.refreshLucideIcons?.(toggle);
})();
