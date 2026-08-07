(() => {
  "use strict";

  const form = document.querySelector("[data-legal-markdown-form]");
  if (!(form instanceof HTMLFormElement)) return;

  const source = form.querySelector("[data-legal-markdown-source]");
  const writeTab = form.querySelector("[data-legal-write-tab]");
  const previewTab = form.querySelector("[data-legal-preview-tab]");
  const writePanel = form.querySelector("[data-legal-write-panel]");
  const previewPanel = form.querySelector("[data-legal-preview-panel]");
  const previewBody = form.querySelector("[data-legal-preview-body]");
  const previewMessage = form.querySelector("[data-legal-preview-message]");
  const status = form.querySelector("[data-legal-editor-status]");

  if (
    !(source instanceof HTMLTextAreaElement) ||
    !(writeTab instanceof HTMLButtonElement) ||
    !(previewTab instanceof HTMLButtonElement) ||
    !(writePanel instanceof HTMLElement) ||
    !(previewPanel instanceof HTMLElement) ||
    !(previewBody instanceof HTMLElement) ||
    !(previewMessage instanceof HTMLElement)
  ) {
    return;
  }

  let previewedSource = "";

  const selectView = (view) => {
    const previewing = view === "preview";
    writeTab.classList.toggle("active", !previewing);
    previewTab.classList.toggle("active", previewing);
    writeTab.setAttribute("aria-selected", String(!previewing));
    previewTab.setAttribute("aria-selected", String(previewing));
    writePanel.hidden = previewing;
    previewPanel.hidden = !previewing;
  };

  const preview = async () => {
    selectView("preview");
    if (source.value === previewedSource && previewBody.childElementCount > 0) {
      return;
    }

    previewTab.disabled = true;
    previewMessage.className = "legal-preview-message";
    previewMessage.textContent = "Generating preview…";
    const csrfToken =
      form.querySelector("input[name='_csrf_token']")?.value || "";
    const body = new FormData();
    body.set("markdown_source", source.value);
    body.set("_csrf_token", csrfToken);

    try {
      const response = await fetch("/admin/legal-content/preview", {
        method: "POST",
        body,
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "same-origin",
      });
      const result = await response.text();
      if (!response.ok)
        throw new Error(result || "Preview could not be generated");
      previewBody.innerHTML = result;
      previewedSource = source.value;
      const scrubbedFormatting =
        response.headers.get("X-OpenScribe-Legal-Formatting") === "scrubbed";
      previewMessage.className = scrubbedFormatting
        ? "legal-preview-message warning"
        : "legal-preview-message";
      previewMessage.textContent = scrubbedFormatting
        ? "Some unsupported formatting was removed. Check the preview before publishing."
        : "Preview reflects the current editor text. Save the draft to keep it.";
    } catch (error) {
      previewBody.replaceChildren();
      previewMessage.className = "legal-preview-message error";
      previewMessage.textContent =
        error instanceof Error
          ? error.message
          : "Preview could not be generated";
    } finally {
      previewTab.disabled = false;
    }
  };

  source.addEventListener("input", () => {
    if (status) status.textContent = "Unsaved changes";
  });
  writeTab.addEventListener("click", () => selectView("write"));
  previewTab.addEventListener("click", () => void preview());
})();
