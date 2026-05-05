Recommendation

Do a targeted partial rollback of the PII display behaviour introduced by 73975479335f29ff75321fba274f2856f01ddaba, not a full revert.

That commit’s intent was “minimize PII in default responses,” add a reveal endpoint, and omit PII values from default workspace/generated-document payloads. That directly conflicts with what you want for the source transcript owner view. 

Keep the good security pieces:

encrypted-at-rest entity storage,

HTTPS/private-network policy for outbound provider calls,

Cache-Control: no-store,

owner-only access checks,

plaintext field renames such as current_draft_text.


Reinstate the old UX for the source transcript page:

owner sees PII visible by default;

transcript text is highlighted immediately;

PII sidebar shows values by default;

clinical/disease entities are highlighted too;

Hide PII is a browser-only visual mask;

Copy transcript copies the real transcript, not the masked display.


Confirmed existing transport policy

The existing de-identification URL validator already does what you described for providers:

permits https:// generally;

permits non-HTTPS only for local/private/link-local/unspecified hosts;

rejects non-HTTPS public remote endpoints. 


Apply that same rule to anything receiving transcript text or PII, including clinical NLP providers.


---

Likely code targets

1. app/schemas/transcripts.py

Current regression shape:

TranscriptPiiEntitySummary omits value.

TranscriptPiiEntityDetail includes value.

This was introduced as part of the minimisation change. 


Recommended change:

Keep both models if useful.

Use detail-with-value for owner-facing source transcript PII.

Keep summary/no-value for generated-document PII where you chose not to show PII by default.


Target behaviour:

# Source transcript owner workspace:
active_transcript_pii_entities: list[TranscriptPiiEntityDetail]

# Generated docs:
pii_entities: list[GeneratedDocumentPiiEntityDetail or summary without value]

2. app/schemas/workspace.py

Current regression changed:

active_transcript_pii_entities: list[TranscriptPiiEntitySummary]

Recommendation:

active_transcript_pii_entities: list[TranscriptPiiEntityDetail]

Only for the active source transcript owner workspace. Do not broadly re-enable plaintext values everywhere.

3. app/web/transcribe_workspace.py

There is already an include_values switch:

def transcript_pii_entities_response(..., include_values: bool = False)

When include_values=True, it returns TranscriptPiiEntityDetail with value. When false, it returns a summary without value. 

Recommended change:

For active_transcript_pii_entities, call:


transcript_pii_entities_response(db, active_transcript, include_values=True)

For generated-document PII entities, leave include_values=False.


This is the smallest backend fix matching your decisions.

4. app/static/js/transcribe/app.js

The regression added reveal-oriented behaviour:

allowReveal

stripping values from display rows

reveal button machinery

/pii-entities/reveal fetch flow

placeholder-first rendering


Recommended change:

Remove reveal-as-default path for the source transcript.

Render owner PII immediately from activeTranscriptPiiEntities.

Add a Hide PII / Show PII toggle near the transcript header/sidebar.

Toggle should be browser-only state, reset on every page load.

Do not persist hidden state.

Do not refetch from server to reveal.

Do not affect copy behaviour.


Suggested UI state:

let piiMasked = false;

Rendering rules:

// PII visible by default
renderHighlightedTranscript(currentDraftText, {
  piiEntities: currentPiiEntities,
  clinicalEntities: currentClinicalEntities,
  maskPii: piiMasked,
});

Hide behaviour:

// Keep span/highlight, replace visible PII text only
<span class="entity-highlight entity-highlight--pii" data-real-value="John Smith">
  ••••••
</span>

Show behaviour restores the real span text from render state, not from another API call.

5. app/static/js/transcribe/documents.js

Regression explicitly passed allowReveal: false when selecting generated notes. 

Recommendation:

Leave generated-document PII minimised, because you selected source transcript only.

Ensure note selection does not overwrite source transcript highlighting state or blank the transcript PII values.

The document navigator should not call a shared renderPiiEntities in a way that mutates currentPiiEntities for the source transcript unless the active panel is actually showing generated-document PII.


This is a likely source of “highlighting is completely broken”: generated-note selection may be replacing source transcript PII rows with no-value summaries.

6. app/services/clinical_nlp.py and app/web/transcribe_workspace.py

The source transcript page should receive clinical/disease entities alongside PII entities.

Recommendation:

Keep clinical NLP values visible by default.

Highlight them separately from PII.

Hide PII should not hide clinical highlights.

If there is a clinical_detection_allow_unredacted flag, ensure it only governs provider input safety, not whether the transcript owner can see clinical entities.


7. app/templates/transcribe/_workspace.html

Recommended UI addition:

Add a small button near the transcript header or PII sidebar:


<button type="button" data-toggle-pii-visibility>
  Hide PII
</button>

Behaviour:

Default label: Hide PII

When masked: Show PII

No server call

No persistence

Only affects source transcript display and PII sidebar values


8. tests/test_pii_response_minimisation.py

These tests currently enforce the wrong behaviour for your intended source transcript UX:

workspace PII entities do not include values by default; 

generated-document PII entities do not include values by default; 

reveal endpoint returns values; 


Recommended test rewrite:

Keep / adapt

Keep tests asserting:

generated-document PII entities do not include values by default;

sensitive API responses use Cache-Control: no-store;

non-owner cannot access transcript PII;

transcript text itself is returned to owner under the correct field name.


Replace

Replace:

test_workspace_pii_entities_do_not_include_values_by_default

with:

test_owner_workspace_source_transcript_pii_entities_include_values_by_default

Expected:

entities[0]["value"] == "John Smith"
entities[0]["placeholder"] == "[PHI-1]"
entities[0]["entity_type"] == "PERSON"

Add frontend regression tests

Add tests checking:

Hide PII button exists;

no required reveal button for initial display;

source transcript rendering uses PII values immediately;

PII and clinical entities use different CSS classes;

hiding PII masks only PII spans;

clinical/disease spans remain visible;

copy transcript path reads raw transcript text, not masked DOM text.



---

Implementation stance

Do

Make the owner source transcript page privileged and usable.

Send plaintext PII to that page over the normal authenticated HTTPS path.

Keep PII encrypted at rest in DB.

Keep redaction/manual PII values encrypted in DB.

Keep provider transport restrictions.

Keep source transcript PII visible by default.

Add browser-only masking for passerby privacy.

Keep clinical highlights visible when PII is hidden.


Do not

Do not require a reveal fetch before highlighting.

Do not use placeholder-only entities for source transcript highlighting.

Do not let generated-document PII state overwrite source transcript highlighting state.

Do not persist “Hide PII” as a user preference.

Do not make copy use masked DOM text.

Do not remove no-store.

Do not weaken provider URL validation.



---

Agent instructions to avoid regressions

Use this as the brief:

Fix the PII/clinical highlighting regression on the source transcription page.

Do a targeted partial rollback of commit 73975479335f29ff75321fba274f2856f01ddaba only where it changed owner source-transcript PII display. Do not fully revert the commit.

Required behaviour:
- Transcript owner sees source transcript PII values by default.
- PII values are sent only to the authenticated owner workspace/source transcript response.
- PII remains encrypted at rest in the database.
- External providers receiving transcript text/PII must use HTTPS unless local/private-network HTTP is allowed by the existing validator.
- PII and clinical/disease entities are both highlighted in the source transcript.
- PII and clinical/disease highlights use distinct visual classes/colours.
- Add a browser-only Hide PII / Show PII toggle.
- Default state on every page load is PII visible.
- Hide PII masks only PII values in the transcript and PII sidebar; it must not remove highlight spans.
- Hide PII must not hide clinical/disease highlights.
- Copy transcript must copy the real transcript text, not masked DOM text.
- Generated-document PII entities should remain minimised by default; this fix is for the source transcript page.

Likely backend files:
- app/schemas/transcripts.py
- app/schemas/workspace.py
- app/web/transcribe_workspace.py
- app/web/presentation.py only if generated-document/source transcript PII responses are coupled
- app/schemas/deidentification.py only if applying provider URL policy to clinical NLP is incomplete

Likely frontend files:
- app/static/js/transcribe/app.js
- app/static/js/transcribe/documents.js
- app/templates/transcribe/_workspace.html
- app/templates/transcribe/_head_assets.html

Likely tests:
- rewrite tests/test_pii_response_minimisation.py so source transcript owner workspace includes PII values by default
- keep generated-document PII minimisation tests
- add tests for Hide PII / Show PII markup and no reveal-required flow
- add tests that clinical highlights remain visible when PII is hidden
- add tests that Copy transcript uses raw transcript text, not masked DOM text

Acceptance criteria

Opening /transcribe?transcript_id=... as the owner shows real PII immediately.

PII is highlighted.

clinical/disease entities are highlighted in a different colour.

PII sidebar shows real values immediately.

There is a visible Hide PII control.

Clicking Hide PII masks PII but leaves PII highlight spans and clinical highlights intact.

Clicking Show PII restores the visible values without a network call.

Generated-document PII remains minimised by default.

Existing provider URL validation still rejects public non-HTTPS endpoints.

Tests no longer assert that source transcript owner workspace omits PII values by default.