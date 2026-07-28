(() => {
  const EU_BASE_URL = "https://api.eu.deepgram.com";
  const GLOBAL_BASE_URL = "https://api.deepgram.com";

  const initialise = () => {
    const wizard = document.getElementById("stt-wizard");
    const form = document.getElementById("stt-wizard-form");
    const presetInput = document.getElementById("stt-provider-preset");
    const baseUrlInput = document.getElementById("stt-base-url");
    const baseUrlField = document.getElementById("stt-base-url-field");
    const nextButton = document.getElementById("stt-wizard-next");
    if (!wizard || !form || !presetInput || !baseUrlInput || !baseUrlField || !nextButton) {
      return;
    }

    const regionField = document.createElement("div");
    regionField.className = "field full";
    regionField.id = "stt-deepgram-region-field";
    regionField.hidden = true;

    const regionLabel = document.createElement("label");
    regionLabel.htmlFor = "stt-deepgram-region";
    regionLabel.textContent = "Deepgram processing endpoint";

    const regionSelect = document.createElement("select");
    regionSelect.id = "stt-deepgram-region";
    regionSelect.name = "deepgram_region";
    regionSelect.append(
      new Option("EU endpoint (recommended)", "eu", true, true),
      new Option("Global endpoint", "global"),
    );

    const regionHelp = document.createElement("p");
    regionHelp.className = "field-help";
    regionHelp.textContent =
      "EU routing is selected by default. Choose Global only after confirming that it is permitted for this deployment.";

    const warning = document.createElement("div");
    warning.className = "operation-result error";
    warning.id = "stt-deepgram-global-warning";
    warning.setAttribute("role", "alert");
    warning.hidden = true;

    const warningHeading = document.createElement("strong");
    warningHeading.textContent = "Are you sure the Global endpoint is compliant?";

    const warningText = document.createElement("p");
    warningText.textContent =
      "The Global endpoint may process personal data outside the EU or UK. Confirm that its use complies with applicable local data protection law, controller instructions, contracts, and international-transfer requirements.";

    const acknowledgementLabel = document.createElement("label");
    acknowledgementLabel.className = "checkbox-row";
    const acknowledgement = document.createElement("input");
    acknowledgement.type = "checkbox";
    acknowledgement.id = "stt-deepgram-global-acknowledgement";
    acknowledgement.setAttribute("aria-describedby", "stt-deepgram-global-warning");
    const acknowledgementText = document.createElement("span");
    acknowledgementText.textContent =
      "I have confirmed that the Global endpoint is permitted for this deployment.";
    acknowledgementLabel.append(acknowledgement, acknowledgementText);

    warning.append(warningHeading, warningText, acknowledgementLabel);
    regionField.append(regionLabel, regionSelect, regionHelp, warning);
    baseUrlField.insertAdjacentElement("afterend", regionField);

    const providerChoice = wizard.querySelector('[data-provider-choice="Deepgram"]');
    const providerDescription = providerChoice?.querySelector("small");
    if (providerDescription) {
      providerDescription.textContent =
        "Managed STT with EU routing by default; Global routing requires a compliance confirmation.";
    }

    const isDeepgram = () => presetInput.value === "deepgram";

    const applyRegion = ({ preserveAcknowledgement = false } = {}) => {
      const globalSelected = regionSelect.value === "global";
      baseUrlInput.value = globalSelected ? GLOBAL_BASE_URL : EU_BASE_URL;
      warning.hidden = !globalSelected;
      if (!globalSelected || !preserveAcknowledgement) {
        acknowledgement.checked = false;
      }
      acknowledgement.setCustomValidity("");
    };

    const syncFromWizard = () => {
      const deepgramSelected = isDeepgram();
      regionField.hidden = !deepgramSelected;
      if (!deepgramSelected) {
        warning.hidden = true;
        acknowledgement.checked = false;
        acknowledgement.setCustomValidity("");
        return;
      }

      regionSelect.value =
        baseUrlInput.value.trim().toLowerCase() === GLOBAL_BASE_URL ? "global" : "eu";
      applyRegion({ preserveAcknowledgement: true });
    };

    regionSelect.addEventListener("change", () => applyRegion());
    acknowledgement.addEventListener("change", () => {
      if (acknowledgement.checked) {
        acknowledgement.setCustomValidity("");
      }
    });

    wizard.querySelectorAll("[data-provider-choice]").forEach((choice) => {
      choice.addEventListener("click", () => queueMicrotask(syncFromWizard));
    });
    document.getElementById("add-stt-provider-button")?.addEventListener("click", () =>
      queueMicrotask(syncFromWizard),
    );
    document.querySelectorAll("[data-edit-stt-provider]").forEach((button) => {
      button.addEventListener("click", () => queueMicrotask(syncFromWizard));
    });

    nextButton.addEventListener(
      "click",
      (event) => {
        const connectionStep = wizard.querySelector('[data-wizard-step="2"]');
        if (
          connectionStep?.hidden !== false ||
          !isDeepgram() ||
          regionSelect.value !== "global" ||
          acknowledgement.checked
        ) {
          return;
        }

        event.preventDefault();
        event.stopImmediatePropagation();
        warning.hidden = false;
        acknowledgement.setCustomValidity(
          "Confirm that the Global endpoint is compliant before continuing.",
        );
        acknowledgement.reportValidity();
        acknowledgement.focus();
      },
      true,
    );

    syncFromWizard();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialise, { once: true });
  } else {
    initialise();
  }
})();
