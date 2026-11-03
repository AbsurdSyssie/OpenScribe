---
target: LLM and STT provider setup and editing flow
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 4
target_identity: "file:/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin_mockup.html"
target_fingerprint: "sha256:d5e114c85d3cec8a49289d9c3540f3fd7901a130ffd33d5252ddedc0ab3d1787"
target_path: /home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin_mockup.html
timestamp: 2026-10-28T15-28-12Z
slug: app-templates-admin-mockup-html
---
# Provider setup and editing UX review

Date: 28 October 2026. Assessed commit: `4c3f8e659635a5340d989f1fafe7daa35dea328b`.
Branch: `codex/improve_provider_flow`.
Target: `app/templates/admin_mockup.html`, LLM and STT provider addition and editing.
Status: dated assessment and proposed direction, not an implementation plan or current operational contract.

## Method and evidence

Two independent Luna assessments supplied the browser walkthrough/design review and detector/source evidence. The parent reviewed their findings against the template, services, maintained provider documentation, and captured screenshots.

No local application server was running. Assessment A rendered the current Jinja template on a temporary localhost server with synthetic team/provider data. Mock provider routes returned model catalogs or a synthetic 503 after 2.5 seconds. Luna drove STT editing through connection, inspection, defaults, Back, and an error; it drove LLM provider selection, connection, and model discovery. Screenshots are under `/tmp/openscribe-provider-ux-review/`. The server is stopped.

The harness omitted some custom runtime context and failed to mount application static assets correctly. Browser observations support content and inline wizard interactions, not full production styling, navigation guards, focus handling, responsive behavior, or authentication. No real provider requests, saved credential reads, database changes, or final promotion to a real provider occurred. A browser cannot demonstrate server latency with a mocked delay.

Assessment B could not connect Chrome DevTools at localhost port 9222. It supplied direct source analysis and the CLI detector result; it did not inject a browser overlay. The code knowledge graph was stale at the assessed commit, so the investigation used current source directly.

## Overall assessment

The UI uses the established OpenScribe administration vocabulary and a staged provider-specific setup. The strongest opportunity is to distinguish editing saved settings from establishing or changing a connection. The current Edit action repeats setup, then offers navigation and feedback that do not match the state of the draft.

Design specificity: the team context, provider rules and clinical-operation constraints fit OpenScribe; generic preset descriptions offer little help choosing a service. Missing harness assets prevent a reliable verdict on the complete visual presentation.

## Priority findings

### 1. [P1] STT errors target a hidden, unrelated dialog

The alert `stt-wizard-error` is inside the Add Member form at `app/templates/admin_mockup.html:5448`. The STT wizard begins at line 5466. `wizardError` updates that alert and scopes field highlighting through its nearest form at lines 6493 and 6522. The synthetic 503 populated the alert text, but its hidden Add Member ancestor prevented it from appearing in the open STT wizard. The screenshot confirms the absence of a visible error.

This explains how a failed check can look like nothing happened. Move the existing alert into the STT form and verify actual rendered visibility, field focus and retry after failure. Preserve sanitized error messages. Suggested action: `$impeccable harden`.

The LLM alert is inside its own form at line 6023. Its error path was not exercised, so this STT defect must not be generalized to LLM.

### 2. [P1] Edit repeats provider setup and remote inspection

Both Edit actions reset the form and open step 1 (`admin_mockup.html:6573`, `:6865`). Step 2 creates a revision draft through `/api/v1/stt-configs/drafts` or `/api/v1/llm-configs/drafts`, even if connection details are unchanged (`:6685`, `:6959`). The services resolve an eligible saved credential and inspect the provider during draft creation (`app/services/stt.py:2390`, `app/services/llm.py:771`). Leaving the key blank avoids entering it again; it does not avoid testing.

An administrator must wait for a remote service before reaching existing defaults. A provider outage can block that editing path. Separate routine saved-settings editing from connection changes. Open Edit with current values and identify which changes require inspection. Keep explicit reinspection available. Suggested action: `$impeccable shape`.

Label/availability update services already avoid provider inspection (`stt.py:489`, `llm.py:649`), with server-rendered `/details` routes in `app/routes/web_admin.py`; this template does not expose those routes. Model/default changes have separate validation requirements. Reusing a saved key is insufficient reason to skip validation after changing an endpoint, authentication mode, protocol, provider-specific connection setting, or runtime contract.

### 3. [P1] Back is visible but stops working after inspection

The step helpers show Back after step 1. Both click handlers require an empty draft ID before changing the step (`admin_mockup.html:6710`, `:6985`). Successful inspection creates that ID. Luna verified that clicking Back on STT Defaults left the same step visible; the equivalent LLM failure is source-confirmed.

Users must discard and restart to correct earlier connection details. Provide working navigation with explicit draft invalidation/reinspection where necessary, and preserve safe edits. Any replacement/cancellation must retain durable draft-secret cleanup. Suggested action: `$impeccable harden`.

### 4. [P2] Requests disable controls without explaining progress

During inspection and finalization, the handlers disable the primary action and Cancel without changing the action label or supplying a live progress message (`admin_mockup.html:6676`, `:6697`, `:6933`, `:6972`). The 2.5-second synthetic delay reproduced the inspection state. Close handlers also return while busy.

Add bounded, accessible states such as checking connection, finding models and saving changes. Explain failures and preserve retry. Cancellation must reflect whether a server request has already created a draft; closing the UI alone must not orphan a credential-bearing draft. Suggested action: `$impeccable clarify` and `$impeccable harden`.

### 5. [P1] Edit does not faithfully represent retained settings

Source-derived risks, not verified real-provider changes:

- Both Edit buttons carry a saved model name, but the open/render handlers clear and rebuild the model controls without restoring it. STT inserts an empty Provider default option first (`admin_mockup.html:6472`); LLM rebuilds the default list from checked catalog entries (`:6817`, `:6830`). An unrelated edit can submit a different default unless the administrator reselects it. Restore the saved model when still eligible, and explain an unavailable saved choice.
- The generic helper says a blank key keeps the saved credential (`:5639`, `:5880`). Draft inheritance only covers the required-token cases and Gemini service-account rules. Optional-auth custom STT presets and Ollama follow different rules (`app/services/stt.py:2403`, `app/services/stt_presets.py:101`, `app/services/llm.py:788`, `docs/llm-providers.md`, Secrets). A blank optional credential can produce a no-auth revision; the LLM wizard hides the key input for Ollama. Make the current authentication state and the actual effect of blank/replacement input explicit. Any decision to change retention semantics needs design authorization; this review does not authorize that contract change.

Suggested action: `$impeccable harden` and `$impeccable clarify`.

## Strengths

- Saved secrets remain server-side, and the browser does not redisplay them. The draft/revision boundary protects the active provider until promotion.
- Four named steps and provider-specific fields make initial setup easier to follow than an undifferentiated configuration form.
- Existing error handling has sanitized guidance and field-error support. Correct placement can make that work useful for STT.

## Heuristic assessment

Provisional UX scores for this bounded review, not accessibility conformance or a user study. The parent revised the independent design scores after corroborating the hidden STT alert and saved-state risks.

| Heuristic | Score / 4 | Main finding |
| --- | --- | --- |
| Visibility of system status | 1 | Silent wait and hidden STT errors |
| Match to user language | 3 | Clear workflow terms; generic preset descriptions |
| User control and freedom | 1 | Dead Back and blocked dismissal while busy |
| Consistency and standards | 3 | Shared stages; STT/LLM error placement differs |
| Error prevention | 2 | Saved defaults and optional-auth copy can mislead |
| Recognition rather than recall | 3 | Prefilled connection metadata, but lost default selection |
| Flexibility and efficiency | 1 | Routine Edit repeats inspection |
| Minimalist design | 3 | Staged setup; LLM choice list is dense |
| Error recovery | 1 | STT error targets hidden form; restart required for earlier fields |
| Help and documentation | 2 | Credential help lacks provider-specific accuracy |
| **Total** | **20/40** | **Significant improvements needed** |

Cognitive-load checklist: 2 of 8 items failed in the independent assessment, chunking and choice count at the LLM chooser. Luna observed nine primary LLM choices plus Advanced, with repeated Supported provider preset descriptions. The intrinsic endpoint configuration complexity remains high; staged fields help.

Emotional journey: named stages set an expectation of progress; a silent wait and ineffective Back interrupt that progress; hidden STT failure leaves the administrator without an explanation. The edit path should retain current settings and state what changed before final promotion.

Persona risks: an experienced administrator loses time to repeat inspection and restart; a first-time administrator receives little guidance distinguishing presets; a keyboard or screen-reader user cannot rely on an error placed under a hidden ancestor. Existing dated keyboard/axe artifacts do not establish current or complete conformance and were not rerun here.

## Detector and smaller observations

The command `.agents/skills/impeccable/scripts/impeccable detect --json app/templates/admin_mockup.html` exited 2 with 83 template-wide findings. Five map to provider UI: one small Provider / model text warning and four padding warnings for wizard panels/inspection rows. The tool returned line 0 for these snippets. These are static style signals; the missing harness assets prevent reliable visual validation. The remaining detector findings were not treated as provider-flow defects.

The LLM checkbox catalog adds a model-selection concept, while the finalize request transmits a default model rather than a subset of models. Clarify what selection means before changing any team model-policy contract. This observation did not receive a separate runtime test.

## Proposed next direction and constraints

Open Edit on saved settings. Keep connection changes and explicit connection testing discoverable. Reuse current services where they support the requested edit. Run inspection for the connection, identity, model or capability changes that require it, rather than treating unchanged key text as the only criterion.

Preserve admin/team authorization, write-only credentials, draft-owned versioned secrets, post-commit durable secret cleanup, ready/active eligibility, model validation, synthetic-only diagnostics and protections for in-flight work. This assessment changes no architecture, API, migration, runtime configuration or clinical workflow.

Questions for a subsequent implementation pass: prioritize editing efficiency or setup/error feedback? Address the highest-impact three issues first or the complete flow? The user requested analysis only, so this review contains no implementation authorization.

## Work and verification record

Luna performed the independent browser/design and detector/source assessments. The parent reconciled their output and corrected an initial browser report that mistook populated hidden error text for a visible message. The screenshot and source support the corrected STT finding.

Performed: branch creation, current-source/documentation inspection, synthetic current-template browser interactions, screenshots and scoped detector execution. Not performed: pytest, API authorization audit, live provider testing, complete accessibility audit, real draft promotion, model-default persistence regression or authenticated application walkthrough. No implementation changed, so no migration/configuration or maintained operational-document update was required. Findings belong to the requested scope; no unrelated discovered issues were recorded.

The maintained-document validator passed and checked 54 files. The storage helper returned an earlier whole-admin score of 24/40 and this provider-focused score of 20/40; the different scopes prevent a like-for-like trend comparison.
