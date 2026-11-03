function isOpen(panel) {
  if (typeof panel.showPopover === 'function') return panel.matches(':popover-open');
  return !panel.hidden;
}

function showPanel(panel) {
  if (typeof panel.showPopover === 'function') {
    panel.hidden = false;
    panel.showPopover();
  } else {
    panel.hidden = false;
  }
}

function hidePanel(panel) {
  if (typeof panel.hidePopover === 'function' && isOpen(panel)) panel.hidePopover();
  panel.hidden = true;
}

function positionPanel(trigger, panel) {
  const rect = trigger.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  const gap = 6;
  const below = window.innerHeight - rect.bottom;
  const above = rect.top;
  const opensAbove = below < panelRect.height + gap && above > below;
  const idealTop = opensAbove ? rect.top - panelRect.height - gap : rect.bottom + gap;
  panel.style.top = `${Math.round(Math.max(8, Math.min(window.innerHeight - panelRect.height - 8, idealTop)))}px`;
  panel.style.left = `${Math.round(Math.max(8, Math.min(window.innerWidth - panelRect.width - 8, rect.right - panelRect.width)))}px`;
}

export function initProviderRowMenus(root = document) {
  const menus = Array.from(root.querySelectorAll('[data-provider-menu]')).map((wrapper) => ({
    trigger: wrapper.querySelector('[data-provider-menu-trigger]'),
    panel: wrapper.querySelector('[data-provider-menu-panel]'),
  })).filter(({ trigger, panel }) => trigger && panel);
  if (!menus.length) return;

  let active = null;
  menus.forEach(({ panel }) => {
    if (typeof panel.showPopover !== 'function') panel.hidden = true;
    document.body.append(panel);
  });

  const close = ({ restoreFocus = false } = {}) => {
    if (!active) return;
    const menu = active;
    hidePanel(menu.panel);
    menu.trigger.setAttribute('aria-expanded', 'false');
    active = null;
    if (restoreFocus && menu.trigger.isConnected) menu.trigger.focus();
  };

  const open = (menu) => {
    close();
    menu.panel.style.visibility = 'hidden';
    showPanel(menu.panel);
    positionPanel(menu.trigger, menu.panel);
    menu.panel.style.visibility = '';
    menu.trigger.setAttribute('aria-expanded', 'true');
    active = menu;
    menu.panel.querySelector('button:not([disabled])')?.focus();
  };

  menus.forEach((menu) => menu.trigger.addEventListener('click', () => {
    if (active === menu && isOpen(menu.panel)) close({ restoreFocus: true });
    else open(menu);
  }));

  document.addEventListener('pointerdown', (event) => {
    if (!active || active.trigger.contains(event.target) || active.panel.contains(event.target)) return;
    close();
  });
  document.addEventListener('focusin', (event) => {
    if (!active || active.trigger.contains(event.target) || active.panel.contains(event.target)) return;
    close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !active) return;
    event.preventDefault();
    close({ restoreFocus: true });
  });
  document.addEventListener('submit', (event) => {
    const menu = menus.find((candidate) => candidate.panel.contains(event.target));
    if (!menu) return;
    queueMicrotask(() => {
      if (event.defaultPrevented) {
        if (active === menu) close();
        else {
          hidePanel(menu.panel);
          menu.trigger.setAttribute('aria-expanded', 'false');
        }
        if (menu.trigger.isConnected) menu.trigger.focus();
      } else if (active === menu) close();
    });
  });
  window.addEventListener('resize', () => close());
  window.addEventListener('scroll', (event) => {
    if (event.target instanceof Node && active?.panel.contains(event.target)) return;
    close();
  }, true);
}
