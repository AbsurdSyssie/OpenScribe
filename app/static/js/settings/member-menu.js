function menuIsOpen(panel) {
  if (typeof panel.showPopover === 'function') return panel.matches(':popover-open');
  return !panel.hidden;
}

function showMenu(panel) {
  if (typeof panel.showPopover === 'function') panel.showPopover();
  else {
    panel.hidden = false;
    panel.dataset.fallbackOpen = '';
  }
}

function hideMenu(panel) {
  if (typeof panel.hidePopover === 'function') {
    if (panel.matches(':popover-open')) panel.hidePopover();
  } else {
    delete panel.dataset.fallbackOpen;
    panel.hidden = true;
  }
}

function positionMenu(trigger, panel) {
  const viewportGap = 8;
  const triggerGap = 6;
  const triggerRect = trigger.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  const roomBelow = window.innerHeight - triggerRect.bottom;
  const roomAbove = triggerRect.top;
  const opensAbove = roomBelow < panelRect.height + triggerGap && roomAbove > roomBelow;
  const idealTop = opensAbove
    ? triggerRect.top - panelRect.height - triggerGap
    : triggerRect.bottom + triggerGap;
  const maxTop = Math.max(viewportGap, window.innerHeight - panelRect.height - viewportGap);
  const top = Math.min(Math.max(viewportGap, idealTop), maxTop);
  const idealLeft = triggerRect.right - panelRect.width;
  const maxLeft = Math.max(viewportGap, window.innerWidth - panelRect.width - viewportGap);
  const left = Math.min(Math.max(viewportGap, idealLeft), maxLeft);

  panel.style.top = `${Math.round(top)}px`;
  panel.style.left = `${Math.round(left)}px`;
  panel.dataset.placement = opensAbove ? 'top-end' : 'bottom-end';
}

export function initMemberMenus(root = document) {
  const menus = Array.from(root.querySelectorAll('[data-member-menu]')).map((element) => ({
    element,
    trigger: element.querySelector('[data-member-menu-trigger]'),
    panel: element.querySelector('[data-member-menu-panel]'),
  })).filter(({ trigger, panel }) => trigger && panel);
  if (!menus.length) return;

  let activeMenu = null;
  let dialogReturnFocus = null;

  menus.forEach((menu) => {
    if (typeof menu.panel.showPopover !== 'function') menu.panel.hidden = true;
    document.body.append(menu.panel);
  });

  const closeActiveMenu = ({ restoreFocus = false } = {}) => {
    if (!activeMenu) return;
    const { trigger, panel } = activeMenu;
    hideMenu(panel);
    trigger.setAttribute('aria-expanded', 'false');
    activeMenu = null;
    if (restoreFocus && trigger.isConnected) trigger.focus();
  };

  const openMenu = (menu) => {
    closeActiveMenu();
    menu.panel.style.visibility = 'hidden';
    showMenu(menu.panel);
    positionMenu(menu.trigger, menu.panel);
    menu.panel.style.visibility = '';
    menu.trigger.setAttribute('aria-expanded', 'true');
    activeMenu = menu;
    menu.panel.querySelector('[data-member-menu-item]')?.focus();
  };

  const openDialog = (button, menu) => {
    const dialogId = button.getAttribute('aria-controls');
    const action = button.dataset.memberDialogAction;
    const dialog = dialogId ? document.getElementById(dialogId) : null;
    const section = Array.from(dialog?.querySelectorAll('[data-member-dialog-section]') || [])
      .find((candidate) => candidate.dataset.memberDialogSection === action);
    if (!dialog || !section) return;

    root.querySelectorAll('[data-member-action-dialog][open]').forEach((openDialogElement) => openDialogElement.close());
    dialog.querySelectorAll('[data-member-dialog-section]').forEach((candidate) => { candidate.hidden = candidate !== section; });
    dialogReturnFocus = menu.trigger;
    closeActiveMenu();
    dialog.showModal();
    section.querySelector('[data-member-dialog-cancel]')?.focus();
  };

  menus.forEach((menu) => {
    menu.trigger.addEventListener('click', () => {
      if (activeMenu === menu && menuIsOpen(menu.panel)) closeActiveMenu({ restoreFocus: true });
      else openMenu(menu);
    });

    menu.panel.addEventListener('click', (event) => {
      const dialogButton = event.target.closest('[data-member-dialog-action]');
      if (dialogButton) openDialog(dialogButton, menu);
    });

    menu.panel.addEventListener('submit', () => closeActiveMenu());
    menu.panel.addEventListener('keydown', (event) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      const items = Array.from(menu.panel.querySelectorAll('[data-member-menu-item]'));
      if (!items.length) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement);
      let next = 0;
      if (event.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length;
      if (event.key === 'ArrowUp') next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length;
      if (event.key === 'End') next = items.length - 1;
      items[next].focus();
    });
  });

  document.addEventListener('pointerdown', (event) => {
    if (!activeMenu) return;
    if (activeMenu.trigger.contains(event.target) || activeMenu.panel.contains(event.target)) return;
    closeActiveMenu();
  });

  document.addEventListener('focusin', (event) => {
    if (!activeMenu) return;
    if (activeMenu.trigger.contains(event.target) || activeMenu.panel.contains(event.target)) return;
    closeActiveMenu();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && activeMenu) {
      event.preventDefault();
      closeActiveMenu({ restoreFocus: true });
    }
  });

  window.addEventListener('resize', () => closeActiveMenu());
  window.addEventListener('scroll', (event) => {
    if (event.target instanceof Node && activeMenu?.panel.contains(event.target)) return;
    closeActiveMenu();
  }, true);

  root.querySelectorAll('[data-member-action-dialog]').forEach((dialog) => {
    dialog.querySelectorAll('[data-member-dialog-close]').forEach((button) => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener('close', () => {
      dialog.querySelectorAll('form').forEach((form) => form.reset());
      dialog.querySelectorAll('[data-member-dialog-section]').forEach((section) => { section.hidden = true; });
      if (dialogReturnFocus?.isConnected) dialogReturnFocus.focus();
      dialogReturnFocus = null;
    });
  });
}
