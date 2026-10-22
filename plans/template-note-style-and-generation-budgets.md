# Template note style and generation budgets

Date: 20 October 2026. Status: proposal for user review; application behavior is unchanged.

Baseline: `b28152ede41ee266b14afa2c284e5fbdd3f129bb`.

## Intended outcome

Replace the Preferences page's overlapping Length and Detail controls with one semantic Template note style control. Express the requested presentation in the prompt. Manage provider output capacity and reasoning separately, without using a small output ceiling to obtain a short note.

This proposal supersedes the narrow proposal to add a universal 1,600-token Ollama ceiling. It does not authorize deployment, model replacement, or changes to clinical response schemas. Maintained operational documents must continue to describe implemented behavior until implementation lands.

## Evidence and current behavior

The current Length values are short/normal/long, labelled as approximately one/two/four pages. Ordinary Template generation maps them to 800/1,600/3,200 output tokens but adds no corresponding semantic length instruction. Detail independently supplies concise/balanced/detailed wording guidance. Gemini always requests 30,000 output tokens, so Length currently does not shorten its request ceiling or add a semantic prompt instruction.

Length also controls checker output capacity, checker edit counts (20/50/100), and split-note planning bounds. Split requests capture both option names but do not apply the same explanatory detail guidance as ordinary notes. This is a coordinated generation change, not just a form edit.

Relevant executable sources:

- `app/templates/settings/_preferences.html`, `app/templates/home.html`, `app/static/js/transcribe/app.js`.
- `app/schemas/preferences.py`, `app/services/preferences.py`, `app/routes/web_home_transcribe.py`.
- `app/services/templates.py`: option normalization, prompt builders, queue snapshots, quota reservations, checker policy.
- `app/services/consultation_split_generation.py`, `app/services/consultation_split_intents.py`, `app/services/consultation_split_confirmation.py`: exact option validation, request construction and recovery.
- `app/services/llm_adapters/runtime.py`, `app/services/llm_adapters/gemini_enterprise.py`: actual provider limits and model-specific reasoning configuration.

Provider guidance supports separating these concerns:

- [OpenAI deployment guidance](https://developers.openai.com/api/docs/guides/deployment-checklist) distinguishes visible verbosity from reasoning effort. Native verbosity is useful where the selected model and endpoint explicitly support it; prompts still specify the task and required content.
- [OpenAI reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning) explains that an output-token allowance can include reasoning and visible text. An insufficient allowance can exhaust the request before a usable answer. Its initial experimentation headroom recommendation is not a universal production ceiling.
- [Google thinking guidance](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking) describes model-dependent thinking controls. Reasoning configuration needs model-aware handling rather than a user-facing generic slider.
- [Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md) defines `num_predict` as a generation maximum, not a requested writing style. [Ollama thinking documentation](https://github.com/ollama/ollama/blob/main/docs/capabilities/thinking.mdx) describes model-dependent thinking support.
- [GOV.UK radio guidance](https://design-system.service.gov.uk/components/radios/) supports a visible group for one choice. [W3C form grouping guidance](https://www.w3.org/WAI/tutorials/forms/grouping/) supports a fieldset, legend and associated instructions. Combining the existing two controls is our product judgment, not a provider or accessibility requirement.

## Proposed Preferences controls

Use a radio group with the legend **Template note style**:

| Choice | Hint and prompt intent |
| --- | --- |
| Brief | Compact wording with minimal repetition. Preserve required sections and clinically relevant source information. |
| Standard (default) | Clear clinical wording with the context needed to understand findings, decisions and the plan. |
| Expanded | Fuller source-supported explanations and useful context. Avoid repetition and unsupported additions. |

Shared hint: **Applies to template notes, including split notes. Template requirements and clinically relevant information take priority over brevity.**

Remove the Length and Detail selectors and page-count promises. Keep the existing approved-model selector, template-suggestion toggle, split-note toggle, and unrelated preferences. Do not add personal token, temperature or reasoning-effort controls. Follow-ups and Quick Actions retain their current style behavior.

This is a presentation preference, not permission to omit safety information. All styles must preserve relevant source-supported findings, negation, medication names/doses, uncertainty, decisions, safety-netting and required template content. Expanded must not invent facts or include quotes absent from the source. These instructions are requirements to evaluate, not a guarantee that prompting alone establishes clinical correctness. Results remain drafts for clinician review.

## Implementation

### 1. Canonical preference and compatibility

Introduce `template_note_style` with `brief`, `standard`, and `expanded` in the existing preferences JSON. No new SQL column is expected. Update normalization, schemas, serializers, HTML save handling and JavaScript together: normalization currently discards unsupported keys, and the API's whole-preference replacement semantics must remain intact.

Use Standard when no historical style exists. Convert the three aligned legacy combinations (`short/concise`, `normal/balanced`, `long/detailed`) into the corresponding new style. Do not silently choose precedence for the six mixed combinations. Retain them as a compatibility-only legacy setting until the user selects a replacement; show a concise notice explaining that their previous custom settings are active. Saving unrelated preferences must not reset them. Selecting a new style removes the obsolete active keys atomically while preserving other preferences.

The compatibility path may temporarily retain old behavior for new requests from users with mixed settings. It must be explicit and tested; the new style is the only editable presentation control. Accept legacy API input during a documented transition, reject conflicting old/new input clearly, and do not rewrite historical encrypted snapshots. No patient content belongs in preference metadata.

### 2. Shared style instructions

Create one pure, versioned style-guidance helper used by ordinary freeform and structured Template generation and every topic of split generation. Apply the captured choice in one-note continuation, confirmed regeneration and targeted recovery. Preserve existing distinctions between Working note and dictation sources, redaction boundaries, source snapshots and template instructions.

Template requirements determine content and structure; style determines how economically that content is expressed. Do not introduce section keys, clinical response profiles or weaker output validation. Do not impose a per-section word quota that can force omission.

Use portable prompt instructions in the first release. Add native verbosity only as a separately tested capability for an explicitly verified model/endpoint combination; do not infer support merely from an OpenAI-compatible API. Preserve current model-aware reasoning configuration initially. Brief must never imply less reasoning.

### 3. Independent server budget policy

Resolve technical output capacity from adapter, verified model capabilities and workflow, including split topic count and checker needs. The three new styles must receive the same cap for the same model, workflow and inputs. Retain finite safety bounds, response-byte limits, truncation detection and terminal no-replay behavior.

Do not replace 800/1,600/3,200 with another untested universal number. Before enabling the new path, establish an explicit policy table for supported model/endpoint combinations using documented model limits and synthetic calibration. Include visible JSON/note capacity and reasoning headroom where the provider counts both. Keep Gemini's existing 30,000 ceiling initially. Record remaining provider numbers and their evidence in the implementation review; they are an acceptance gate, not delegated to user preferences. Unknown/custom models need an explicit finite server fallback and compatibility tests; do not assume reasoning or verbosity fields are supported.

The exact effective adapter cap must be captured and used consistently for the provider request, quota reservation and settlement. Higher headroom can increase upfront quota reservation even when actual output is short; check quota admission with realistic synthetic workloads before rollout. Do not double-count reported reasoning usage or change ambiguous-attempt settlement.

The same technical-policy abstraction should accommodate follow-up and Quick Action limits, but changing their numerical limits is a separately stated part of implementation scope. It must not silently extend the new style preference to those workflows. Do not ship the original blanket Ollama 1,600 fix as a substitute for the policy table.

### 4. Checker and split boundaries

Decouple checker output capacity from note style. Retain exact validated patches, source constraints, attempts, timeouts and existing fail-open behavior. For new-style requests, propose the existing Standard checker policy of at most 50 edits regardless of Brief/Standard/Expanded. Compared with the old presets this changes Brief from 20 to 50 and Expanded from 100 to 50; record this deliberate change and freeze the effective checker policy with the request. Do not raise the established absolute maximum or loosen patch validation. Legacy snapshots retain their historical 20/50/100 policy. Test that an invalid patch response exceeding the bound is retried under the existing rules, then leaves the original draft intact with a failed check rather than accepting an unchecked patch.

Audit split character/schema bounds currently derived from the legacy per-note token cap. Derive new technical bounds from the independent policy while retaining established absolute response limits. Preserve required topic IDs, coverage, recovery-only failed topics and sibling boundaries. Budget changes must not weaken the structured-output contract or accept truncation as a valid note.

### 5. Immutable request compatibility

Version the new saved generation options and effective prompt/budget policy. Freeze them at each workflow's established acceptance boundary, including the existing split-confirmation timing. A later Preferences edit must not change accepted work.

Keep explicit legacy request builders/reconstructors for existing queued documents, split intents and confirmed batches. Preserve their workflow-specific differences, including the existing absence of ordinary detail instructions in legacy split prompts. Some split paths verify exact reconstructed request equality: changing a shared builder without a legacy path can invalidate existing work. Do not restamp accepted requests, reconstruct them using current preferences, or replay a submitted attempt. Unknown versions fail closed.

## Verification and acceptance

Start with focused project-venv tests, following `docs/testing.md` and `docs/dbtesting.md`:

- All nine old combinations, missing/partial preferences, new defaults, conflicting API fields, unrelated preference preservation, owning-user authorization and rendered accessible form behavior.
- All three styles in freeform, structured, split, continuation, recovery and regeneration paths. Assert semantic guidance changes while effective budgets stay equal across styles for the same task/model. Preserve clinical schema requirements.
- Legacy exact request reconstruction, new snapshot freezing, preference edits after queue/confirmation, no submitted replay, duplicate delivery and credential preflight.
- Truncation, malformed output, response-size limits, exact quota reservation, unused-unit release and conservative ambiguous-attempt accounting across adapters.
- Checker legacy bounds, independent new-style policy, exact patches and fail-open behavior. Verify split bounds remain finite and consistent with requests.

Add representative synthetic evaluation cases: short and complex consultations, multiple problems, significant negatives, medications/doses, uncertainty and safety-netting. Compare styles against the same source and template. Measure omissions, unsupported facts, completeness, repetition, truncation, latency and actual usage; do not score clinical quality solely by note length. Use clinician review before claiming clinical equivalence or changing reasoning policy. No real patient data or unapproved external provider inspection.

[OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices) supports task-specific representative cases and human-calibrated evaluation; automated schema tests alone do not establish clinical quality.

Update the API route-audit manifest if preference request contracts change, run the documented authorization audit sequentially with pytest, validate maintained docs, and perform an independent regression review. Do not run a full suite without a relevant reason.

On implementation, update the closest maintained references identified by `docs/README.md`, including provider limits, split preferences, checker policy, preferences API, workspace/help and user instructions. Record the old Ollama issue as superseded only when the implemented replacement resolves it. Preserve dated review evidence.

## Work boundaries and current status

Luna agents investigated provider guidance, Preferences UX and executable generation/snapshot dependencies. The parent reconciled findings and prepared this proposal. Implementation should be partitioned into preferences/UI, shared style plus snapshot compatibility, and adapter budgets/checker accounting with explicit file ownership; review integration before relying on delegated results.

Only this plan has been added. No application settings, runtime code, schema, deployment or model selection changed. No application tests or live provider evaluations have been run for this proposal. Numerical budgets outside the existing Gemini policy require calibration during implementation before rollout.
