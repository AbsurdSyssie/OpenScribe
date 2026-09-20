import { initLibraryIO } from './library-io.js?v=20260917-library-io';
import { initLibraryHelp } from './library-help.js?v=20260918-library-help';

const TEMPLATE_IO = {
  prefix: 'template',
  asset: 'template',
  assetLabel: 'Template',
  exportEndpoint: '/api/v1/templates/export',
  exportIdsKey: 'template_ids',
  exportFilename: 'openscribe-templates.json',
  preflightEndpoint: '/api/v1/templates/import/preflight',
  importEndpoint: '/api/v1/templates/import',
  libraryUrl: '/workspace/library/templates',
  pastedFilename: 'pasted-openscribe-templates.json',
  emptyPasteMessage: 'Paste a JSON template bundle first.',
  invalidPasteMessage: 'The pasted text is not valid JSON. Ask your AI assistant to check and resend it as valid JSON. A common cause is quotation marks inside the template text that have not been escaped.',
};

function templateMakerPrompt(schema) {
  return `You are helping a lay user create an OpenScribe template bundle.

Treat any description the user supplies as the brief. Ask only the questions needed to resolve information that is missing or unclear. Depending on the brief, clarify the purpose and intended output, headings or EMIS sections, detail and formatting, tone and audience, what to include or omit, and how missing information should be handled. Skip questions the brief has already answered.

Create one template unless the user explicitly asks for several or clearly describes a set.

Choose "freeform" when the user wants one formatted document. Choose "structured" only when they explicitly want separate EMIS-compatible sections. If that choice is not obvious, ask before generating the bundle. Structured templates may use only the section keys, ordering, and "emis" profile permitted by the JSON Schema. Never invent a profile or section key.

List structured sections in the intended display order. Set section_order to consecutive integers starting at 1 in that same array order, and never repeat a section_key. OpenScribe derives each user-facing section label from section_key, so do not add a section_label field. Put any more specific user-facing emphasis in the section instruction.

Do not ask for or include patient information, transcripts, clinical notes, credentials, or other confidential data. Use fictional or generic examples only.

When the brief is complete, return one complete OpenScribe template bundle conforming exactly to the JSON Schema below. Return raw JSON only: no Markdown code fence, explanation, preamble, or trailing text. Ensure the JSON parses and all required fields are present. Do not add ownership, scope, IDs, timestamps, active state, or other non-portable fields.

JSON validity rules are mandatory:
- Before replying, check the entire output with JSON.parse or an equivalent strict JSON parser and correct every error.
- Use double quotes around JSON property names and string values.
- Never place an unescaped double quote inside a string value. Prefer wording that does not need quotation marks. If a double quote is essential, encode it as \\".
- Encode a line break inside a string as \\n. Never put a literal line break inside a JSON string.
- Do not return the bundle until the complete response passes the parse check.

OpenScribe template bundle JSON Schema:
${JSON.stringify(schema, null, 2)}`;
}

export function initTemplateIO() {
  document.querySelectorAll('[data-template-library]').forEach((library) => {
    initLibraryIO(library, TEMPLATE_IO);
    initLibraryHelp(library, {
      prefix: 'template',
      schemaUrl: '/static/schemas/openscribe-template-bundle-v1.schema.json?v=20260725-section-keys',
      errorMessage: 'The template instructions could not be loaded.',
      buildPrompt: templateMakerPrompt,
    });
  });
}
