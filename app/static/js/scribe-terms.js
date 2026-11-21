const dialog = document.querySelector("[data-scribe-terms-dialog]");
if (dialog) {
  const shell = document.querySelector("[data-workspace-shell]");
  for (const child of shell?.children || []) {
    if (!child.contains(dialog)) child.inert = true;
  }
  const focusable = () => [...dialog.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), [tabindex="0"]')];
  const initial = dialog.querySelector("[data-scribe-terms-initial-focus]") || dialog;
  initial.focus();
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const currentIndex = items.indexOf(document.activeElement);
    if (event.shiftKey && (currentIndex <= 0 || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (currentIndex === items.length - 1 || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });
}
