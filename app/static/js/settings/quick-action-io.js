import { initLibraryIO } from './library-io.js?v=20260917-library-io';
import { initLibraryHelp } from './library-help.js?v=20260918-library-help';

const QUICK_ACTION_IO = {
  prefix: 'quick-action',
  asset: 'quick action',
  assetLabel: 'Quick action',
  exportEndpoint: '/api/v1/quick-actions/export',
  exportIdsKey: 'quick_action_ids',
  exportFilename: 'openscribe-quick-actions.json',
  preflightEndpoint: '/api/v1/quick-actions/import/preflight',
  importEndpoint: '/api/v1/quick-actions/import',
  libraryUrl: '/workspace/library/quick-actions',
  pastedFilename: 'pasted-openscribe-quick-actions.json',
  emptyPasteMessage: 'Paste a JSON quick action bundle first.',
  invalidPasteMessage: 'The pasted text is not valid JSON. Ask your AI assistant to check and resend it as valid JSON. A common cause is quotation marks inside quick action text that have not been escaped.',
};

function quickActionMakerPrompt(schema) {
  return `You are helping a lay user create an OpenScribe quick action bundle.

Treat any description the user supplies as the brief. Ask only the questions needed to resolve information that is missing or unclear. Depending on the brief, clarify what the action should produce, who will read it, the desired tone and structure, required content, what to omit, and how it should handle missing consultation information. Skip questions the brief has already answered.

Create one quick action unless the user explicitly asks for several or clearly describes a set.

A quick action is a reusable instruction that OpenScribe applies to the current consultation. Write prompt_text as the instruction OpenScribe should follow, not as an example output. It may tell OpenScribe to create a document, summary, message, checklist, or other useful result. It must use only information supported by the consultation, must not invent facts, and should say how to handle missing information when that matters.

Every latest_version must have mode "freeform". Give each action a clear name and either a short description or null.

Do not ask for or include patient information, transcripts, clinical notes, credentials, or other confidential data. Use fictional or generic examples only.

When the brief is complete, return one complete OpenScribe quick action bundle conforming exactly to the JSON Schema below. Return raw JSON only: no Markdown code fence, explanation, preamble, or trailing text. Ensure the JSON parses and all required fields are present. Do not add ownership, scope, IDs, timestamps, active state, or other non-portable fields.

JSON validity rules are mandatory:
- Before replying, check the entire output with JSON.parse or an equivalent strict JSON parser and correct every error.
- Use double quotes around JSON property names and string values.
- Never place an unescaped double quote inside a string value. Prefer wording that does not need quotation marks. If a double quote is essential, encode it as \\".
- Encode a line break inside a string as \\n. Never put a literal line break inside a JSON string.
- Do not return the bundle until the complete response passes the parse check.

OpenScribe quick action bundle JSON Schema:
${JSON.stringify(schema, null, 2)}`;
}

export function initQuickActionIO() {
  document.querySelectorAll('[data-quick-action-library]').forEach((library) => {
    initLibraryIO(library, QUICK_ACTION_IO);
    initLibraryHelp(library, {
      prefix: 'quick-action',
      schemaUrl: '/static/schemas/openscribe-quick-action-bundle-v1.schema.json?v=20260724-ai-instructions',
      errorMessage: 'The quick action instructions could not be loaded.',
      buildPrompt: quickActionMakerPrompt,
    });
  });
}
