/** Shared behaviour for Settings form controls. */
document.addEventListener('change', (event) => {
  const control = event.target;
  if (!(control instanceof HTMLSelectElement) || !control.closest('.select-wrap')) return;

  control.blur();
});

function formSnapshot(form) {
  const data = new FormData(form);
  data.delete('_csrf_token');
  return new URLSearchParams(data).toString();
}

function initialiseDirtySaveState(form, buttonSelector, stateSelector) {
  const initial = formSnapshot(form);
  const button = form.querySelector(buttonSelector);
  const state = form.querySelector(stateSelector);
  if (!(button instanceof HTMLButtonElement) || !state) return;

  const sync = () => {
    const hasChanges = formSnapshot(form) !== initial;
    button.disabled = !hasChanges;
    state.textContent = hasChanges ? 'Unsaved changes' : '';
  };

  form.addEventListener('input', sync);
  form.addEventListener('change', sync);
  sync();
}

document.querySelectorAll('[data-writing-style-form]').forEach((form) => {
  initialiseDirtySaveState(form, '[data-save-writing-style]', '[data-writing-style-save-state]');
});

document.querySelectorAll('[data-model-preference-form]').forEach((form) => {
  initialiseDirtySaveState(form, '[data-save-model]', '[data-model-save-state]');
});

function initialiseAccountDialogs(root = document) {
  const dialogs = Array.from(root.querySelectorAll('[data-account-dialog]'));
  if (!dialogs.length) return;

  const returnFocus = new WeakMap();

  const openDialog = (dialog, trigger = null) => {
    if (!(dialog instanceof HTMLDialogElement)) return;
    if (trigger instanceof HTMLElement) returnFocus.set(dialog, trigger);
    if (!dialog.open) dialog.showModal();
    dialog.querySelector('input:not([type="hidden"])')?.focus();
  };

  root.querySelectorAll('[data-account-modal-open]').forEach((trigger) => {
    trigger.addEventListener('click', () => {
      const key = trigger.dataset.accountModalOpen;
      const dialog = key ? root.querySelector(`[data-account-dialog="${CSS.escape(key)}"]`) : null;
      openDialog(dialog, trigger);
    });
  });

  root.querySelectorAll('[data-account-name-form]').forEach((form) => {
    initialiseDirtySaveState(form, '[data-account-name-save]', '[data-account-name-state]');
  });

  root.querySelectorAll('[data-account-sensitive-form]').forEach((form) => {
    const submit = form.querySelector('[data-account-sensitive-submit]');
    if (!(submit instanceof HTMLButtonElement)) return;
    const email = form.querySelector('[data-account-email-input]');
    const currentPassword = form.querySelector('input[name="current_password"]');
    const newPassword = form.querySelector('input[name="new_password"]');
    const confirmation = form.querySelector('input[name="confirm_password"]');
    const mfaCode = form.querySelector('input[name="mfa_code"]');
    const passwordStatus = form.querySelector('[data-account-password-status]');

    const passwordGuidance = () => {
      if (!(currentPassword instanceof HTMLInputElement) || !currentPassword.value) return 'Enter your current password.';
      if (!(newPassword instanceof HTMLInputElement) || !newPassword.value) return 'Enter a new password.';
      if (newPassword.value === currentPassword.value) return 'Choose a different new password.';
      if (newPassword.value.length < 12) return 'Use at least 12 characters.';
      if (!/[A-Z]/.test(newPassword.value)) return 'Add an uppercase letter.';
      if (!/[a-z]/.test(newPassword.value)) return 'Add a lowercase letter.';
      if (!/[0-9]/.test(newPassword.value)) return 'Add a number.';
      if (!(confirmation instanceof HTMLInputElement) || !confirmation.value) return 'Confirm your new password.';
      if (newPassword.value !== confirmation.value) return 'New passwords do not match.';
      if (mfaCode instanceof HTMLInputElement && mfaCode.required && !mfaCode.value.trim()) return 'Enter your authenticator code.';
      return 'Ready to change password.';
    };

    const sync = () => {
      const emailChanged = !(email instanceof HTMLInputElement) || email.value !== email.dataset.accountSavedEmail;
      const passwordsMatch = !(newPassword instanceof HTMLInputElement) || !(confirmation instanceof HTMLInputElement)
        || newPassword.value === confirmation.value;
      const guidance = passwordStatus ? passwordGuidance() : '';
      if (passwordStatus) passwordStatus.textContent = guidance;
      const passwordReady = !passwordStatus || guidance === 'Ready to change password.';
      submit.disabled = !form.checkValidity() || !emailChanged || !passwordsMatch || !passwordReady;
    };

    form.addEventListener('input', sync);
    form.addEventListener('change', sync);
    form.addEventListener('submit', () => {
      if (submit.disabled) return;
      submit.disabled = true;
      const status = form.querySelector('[data-account-oidc-status]');
      if (status) {
        status.hidden = false;
        status.textContent = form.dataset.oidcAction === 'link'
          ? `Opening ${form.dataset.providerName}…`
          : `Disconnecting ${form.dataset.providerName}…`;
      }
    });
    sync();
  });

  dialogs.forEach((dialog) => {
    dialog.querySelectorAll('[data-account-dialog-close]').forEach((button) => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener('close', () => {
      dialog.querySelectorAll('form').forEach((form) => form.reset());
      dialog.querySelectorAll('[data-account-oidc-status]').forEach((status) => {
        status.hidden = true;
        status.textContent = '';
      });
      dialog.querySelectorAll('[data-account-sensitive-form]').forEach((form) => {
        form.dispatchEvent(new Event('change', { bubbles: true }));
      });
      const trigger = returnFocus.get(dialog);
      if (trigger?.isConnected) trigger.focus();
      returnFocus.delete(dialog);
    });
    if (dialog.hasAttribute('data-account-modal-auto-open')) openDialog(dialog);
  });
}

initialiseAccountDialogs();
