(() => {
  const LENGTH = 12;
  const GROUPS = [
    "ABCDEFGHJKLMNPQRSTUVWXYZ",
    "abcdefghijkmnopqrstuvwxyz",
    "23456789",
    "!@#$%&*+-=?",
  ];
  const ALL_CHARACTERS = GROUPS.join("");

  function secureIndex(max) {
    const limit = Math.floor(256 / max) * max;
    const value = new Uint8Array(1);
    do {
      window.crypto.getRandomValues(value);
    } while (value[0] >= limit);
    return value[0] % max;
  }

  function pick(characters) {
    return characters[secureIndex(characters.length)];
  }

  function generatePassword() {
    const characters = GROUPS.map(pick);
    while (characters.length < LENGTH) characters.push(pick(ALL_CHARACTERS));
    for (let index = characters.length - 1; index > 0; index -= 1) {
      const swapIndex = secureIndex(index + 1);
      [characters[index], characters[swapIndex]] = [characters[swapIndex], characters[index]];
    }
    return characters.join("");
  }

  async function copyPassword(password) {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(password);
  }

  function icon(name) {
    const paths = {
      generate: '<path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5"/>',
      copy: '<rect width="13" height="13" x="9" y="9" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
      eye: '<path d="M2.1 12s3.6-7 9.9-7 9.9 7 9.9 7-3.6 7-9.9 7-9.9-7-9.9-7Z"/><circle cx="12" cy="12" r="3"/>',
      hidden: '<path d="m2 2 20 20M10.6 10.7a2 2 0 0 0 2.7 2.7M9.9 4.2A9.7 9.7 0 0 1 12 4c6.3 0 9.9 8 9.9 8a17.5 17.5 0 0 1-2.1 3.2M6.6 6.6C3.6 8.6 2.1 12 2.1 12s3.6 8 9.9 8a9.7 9.7 0 0 0 3.3-.6"/>',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths[name]}</svg>`;
  }

  document.querySelectorAll('[data-generated-password]').forEach((input, index) => {
    const controls = document.createElement("span");
    controls.className = "generated-password-controls";

    const field = document.createElement("span");
    field.className = "generated-password-field";

    const buttonClass = document.querySelector(".app-shell") ? "btn small" : "btn-ghost-sm";

    const generateButton = document.createElement("button");
    generateButton.type = "button";
    generateButton.className = buttonClass;
    generateButton.innerHTML = icon("generate");
    generateButton.title = "Generate password";
    generateButton.setAttribute("aria-label", "Generate and copy a secure temporary password");

    const visibilityButton = document.createElement("button");
    visibilityButton.type = "button";
    visibilityButton.className = buttonClass;
    visibilityButton.innerHTML = icon("eye");
    visibilityButton.title = "Show password";
    visibilityButton.setAttribute("aria-label", "Show temporary password");
    visibilityButton.setAttribute("aria-pressed", "false");

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = buttonClass;
    copyButton.innerHTML = icon("copy");
    copyButton.title = "Copy password";
    copyButton.setAttribute("aria-label", "Copy generated temporary password");
    copyButton.hidden = true;

    if (!input.id) input.id = `generated-password-${index + 1}`;
    controls.append(generateButton, copyButton, visibilityButton);
    input.insertAdjacentElement("beforebegin", field);
    field.append(input, controls);

    input.addEventListener("input", () => {
      if (input.value !== input.dataset.generatedValue) {
        delete input.dataset.generatedValue;
        copyButton.hidden = true;
      }
    });

    generateButton.addEventListener("click", async () => {
      const manuallyEntered = input.value && input.value !== input.dataset.generatedValue;
      if (manuallyEntered && !window.confirm("Replace the password currently entered?")) return;

      const password = generatePassword();
      input.value = password;
      input.dataset.generatedValue = password;
      copyButton.hidden = false;
      input.focus();
      input.select();

      try {
        await copyPassword(password);
        window.showToast?.("Password generated and copied.", "success");
      } catch (_error) {
        window.showToast?.("Password generated. Copy it manually.", "warning");
      }
    });

    copyButton.addEventListener("click", async () => {
      if (!input.dataset.generatedValue || input.value !== input.dataset.generatedValue) return;
      try {
        await copyPassword(input.value);
        window.showToast?.("Password copied.", "success");
      } catch (_error) {
        input.focus();
        input.select();
        window.showToast?.("Could not copy password. Copy it manually.", "warning");
      }
    });

    visibilityButton.addEventListener("click", () => {
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      visibilityButton.innerHTML = icon(showing ? "eye" : "hidden");
      visibilityButton.title = `${showing ? "Show" : "Hide"} password`;
      visibilityButton.setAttribute("aria-label", `${showing ? "Show" : "Hide"} temporary password`);
      visibilityButton.setAttribute("aria-pressed", String(!showing));
      input.focus();
    });
  });
})();
