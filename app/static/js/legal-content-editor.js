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
  let previewRequestId = 0;
  const tabs = [writeTab, previewTab];
  const views = ["write", "preview"];

  const selectView = (view, { focus = false } = {}) => {
    const selectedIndex = views.indexOf(view) === 1 ? 1 : 0;
    const previewing = selectedIndex === 1;
    tabs.forEach((tab, index) => {
      const selected = index === selectedIndex;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    writePanel.hidden = previewing;
    previewPanel.hidden = !previewing;
    writePanel.setAttribute("aria-hidden", String(previewing));
    previewPanel.setAttribute("aria-hidden", String(!previewing));
    if (focus) tabs[selectedIndex].focus();
  };

  const handleTabKeydown = (event) => {
    const currentIndex = tabs.indexOf(event.currentTarget);
    if (currentIndex < 0) return;
    let nextIndex = null;
    if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    } else if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = tabs.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    if (views[nextIndex] === "preview") {
      void preview();
    } else {
      selectView("write", { focus: true });
    }
  };

  const preview = async () => {
    selectView("preview", { focus: true });
    const submittedSource = source.value;
    if (submittedSource === previewedSource && previewBody.childElementCount > 0) {
      return;
    }

    const requestId = ++previewRequestId;
    previewTab.disabled = true;
    previewMessage.className = "legal-preview-message";
    previewMessage.textContent = "Generating preview…";
    const csrfToken =
      form.querySelector("input[name='_csrf_token']")?.value || "";
    const body = new FormData();
    body.set("markdown_source", submittedSource);
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
      if (requestId !== previewRequestId || source.value !== submittedSource) {
        return;
      }
      previewBody.innerHTML = result;
      previewedSource = submittedSource;
      const scrubbedFormatting =
        response.headers.get("X-OpenScribe-Legal-Formatting") === "scrubbed";
      previewMessage.className = scrubbedFormatting
        ? "legal-preview-message warning"
        : "legal-preview-message";
      previewMessage.textContent = scrubbedFormatting
        ? "Some unsupported formatting was removed. Check the preview before publishing."
        : "Preview reflects the current editor text. Save the draft to keep it.";
    } catch (error) {
      if (requestId !== previewRequestId || source.value !== submittedSource) {
        return;
      }
      previewBody.replaceChildren();
      previewMessage.className = "legal-preview-message error";
      previewMessage.textContent =
        error instanceof Error
          ? error.message
          : "Preview could not be generated";
    } finally {
      if (requestId === previewRequestId) {
        previewTab.disabled = false;
      }
    }
  };

  source.addEventListener("input", () => {
    previewRequestId += 1;
    previewedSource = "";
    previewBody.replaceChildren();
    previewTab.disabled = false;
    previewMessage.className = "legal-preview-message";
    previewMessage.textContent = "Editor changed. Generate a new preview.";
    if (status) status.textContent = "Unsaved changes";
  });
  tabs.forEach((tab) => tab.addEventListener("keydown", handleTabKeydown));
  writeTab.addEventListener("click", () => selectView("write", { focus: true }));
  previewTab.addEventListener("click", () => void preview());
})();
