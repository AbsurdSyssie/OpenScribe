# User Tutorial

## What OpenScribe does

OpenScribe helps turn consultation audio and clinician-authored context into draft clinical writing.

A typical workflow:

1. record or upload consultation audio;
2. review the resulting draft transcript;
3. optionally add a working note or post-consultation dictation;
4. create a draft note from an approved template;
5. review and edit every result;
6. copy only verified text into the EPR.

OpenScribe does not replace clinical judgement. Transcripts and generated documents can be incomplete, incorrect, overconfident, or placed in the wrong section. You are responsible for checking the final clinical record.

## Before clinical use

Confirm:

- sign-in and TOTP work;
- your team has the required consultation/dictation speech services;
- an approved writing-assistant configuration and templates are available;
- microphone/recording consent and local policy are understood;
- you have practised with synthetic or approved training material;
- you know how to report a privacy, safety, or access-control concern.

Do not use real patient content until local validation/training is complete.

## Open the workspace

Normal-user login currently lands on the `/home` compatibility page. Open the consultation/Scribe link to enter the canonical `/workspace` shell.

The workspace contains:

- Scribe;
- Account;
- Preferences;
- My Library;
- Team sections for leaders.

The Scribe area contains consultation history, recording/upload controls, transcript/history views, working note, generated notes, follow-ups, and quick actions.

## Create or select the correct consultation

Before adding audio or notes:

1. check the visible consultation title/session;
2. create a new consultation for a new encounter;
3. use an existing consultation only when continuing the same encounter/work session;
4. stop if audio was attached to the wrong consultation—do not continue adding content to it.

The browser remembers only an untrusted transcript UUID for navigation. The server still checks ownership and retention on every request.

## Capture consultation audio

### Live microphone

Use live recording when capturing through the browser microphone. During active recording, OpenScribe disables marked workspace navigation and warns before leaving the page.

The browser sends speech chunks while recording. After stop, transcription can continue in the background; you may open another consultation while the prior one finishes.

### Microphone batch

Depending on the selected mode, the browser may record locally and submit one or more whole-file parts. It automatically rolls over before a recording approaches server size/duration limits.

### File upload

Use upload only for an approved audio file belonging to the current consultation. Whole-file limits include individual and hourly byte/duration safeguards; the UI/API reports controlled errors when a limit is reached.

For all modes:

- confirm local recording consent/policy before starting;
- verify the browser microphone/file selection;
- avoid refreshing or closing while active recording controls show `Stop`;
- do not submit real content as a support/test fixture.

## Review the transcript

The transcript is draft source material. Check especially:

- patient/person identity and dates;
- medicines, doses, allergies, diagnoses;
- important positives and negatives;
- examination/investigation details;
- safety-netting and follow-up plans;
- who said what when speaker information matters.

If the transcript is materially wrong, correct the source/draft where supported and apply extra caution before generation.

## Working note

Working note is clinician-authored source content separate from the transcript and generated output.

- Choose either freeform or structured mode.
- The mode locks after the first non-empty save.
- Clearing the working note removes its content and unlocks the mode.
- Editing a generated note never changes the working note.
- Generation snapshots the working note used for that request.

Do not use working note as an unreviewed dumping ground. It becomes generation source after redaction and should contain only relevant approved clinical context.

## Post-consultation dictation

Use post-consultation dictation for a clinician summary/assessment/plan source separate from the consultation transcript.

- Opening the modal does not start recording automatically.
- Record/upload preview returns editable text without saving a dictation row.
- Save explicitly after reviewing the preview.
- Cancel discards unsaved audio/text.
- Later saved segments contribute to one transcript-owned dictation aggregate.
- You can edit the combined text; generation then uses that edited version exactly.
- Clearing edited combined text intentionally removes dictation influence.

Quick-action context recording is transient: it inserts transcription into the existing additional-context field and is redacted before the LLM request.

## Choose note options and a template

Templates define document shape and instructions. Select one approved for the intended destination.

Note options can include:

- writing assistant/model from the active team policy;
- approximate length (`short`, `normal`, `long`);
- detail level.

OpenAI-compatible/Ollama adapters map length to bounded output caps. Gemini currently saves/snapshots the preference but uses its fixed provider ceiling; length is not a guarantee of exact output size.

Settings save for future note requests. If a save fails, OpenScribe warns and the next request may use the previous preference.

## Generate and monitor a note

1. confirm the correct consultation and source content;
2. choose the intended template/options;
3. create the note;
4. wait for queued/processing status to finish;
5. open the generated document;
6. review any controlled failure/truncation message rather than repeatedly resubmitting blindly.

A generation may wait briefly for transcript ingestion to finish. Provider failure messages are intentionally sanitized; when requesting support, provide safe metadata such as time, consultation UUID, provider label, status, and error code—not patient content.

## Review and edit

Read the complete output. Check:

- correct consultation/patient;
- symptoms, important negatives, diagnoses, medicines/doses/allergies;
- examination and investigation facts;
- tasks, follow-up and safety-netting;
- invented, omitted, duplicated, or overly certain statements;
- correct section placement and appropriate tone.

Edit inside OpenScribe until the text matches your judgement. Generated content remains draft even when a hallucination checker reports no issue.

## Structured notes

Structured EMIS templates use these section keys:

- `problem`
- `history`
- `family_history`
- `social_history`
- `examination`
- `comment`
- `tasks`
- `investigations`

Empty sections may be omitted. Do not fill a section merely to make it non-empty. Move, edit, or remove misplaced/uncertain content before copying.

## Follow-ups and quick actions

Follow-ups and Quick Actions create additional draft text from the authorized consultation source.

Possible uses:

- referral wording;
- follow-up instructions;
- task/admin summaries;
- patient-facing drafts where local policy permits.

Always review patient-facing text for advice accuracy, safety-netting, uncertainty, and local policy. OpenScribe does not send the output automatically.

## Copy into the EPR

Before saving in the EPR:

1. read the entire text/section;
2. confirm patient and consultation;
3. correct errors and omissions;
4. copy into the correct EPR field;
5. read the pasted EPR entry again;
6. save only when correct.

Section copy buttons do not verify the destination field.

## Privacy and PII

Transcript-derived content is visible only to its owning user through normal content routes. Team leaders/system administrators can manage metadata, accounts, providers, and shared assets but do not gain content access from their role.

OpenScribe redacts detected/manual PII before LLM dispatch and reidentifies authorized generated output. The PII table minimizes original-value display; revealing an original is an explicit owner-only protected action.

Report suspected cross-user visibility immediately and stop using the affected workflow.

## Delete a consultation

Deletion is immediate after confirmation and has no undo period. It removes the transcript root and implemented transcript-derived children, including working note, dictation, generated documents, versions, redaction/PII data, and jobs according to current lifecycle rules.

Delete only when you have selected the correct consultation and local retention policy permits it.

## Account and preferences

Use:

- `/workspace/account` for name/email/password changes;
- `/workspace/preferences` for recording/writing preferences;
- `/workspace/library/*` for personal/team-visible templates, Quick Actions, and Smart Phrases according to your role.

Sensitive email/password changes require strong reauthentication and revoke other sessions/trusted devices after success.

## Common problems

### Cannot sign in or complete MFA

Use a recovery code when available or ask a leader/system administrator for the approved email/manager recovery flow. Do not share password, TOTP seed, or current codes.

### Recording unavailable

Check browser microphone permission and the selected recording mode. If team STT is missing/unavailable, ask the leader/system administrator.

### Upload rejected

Check file, size/duration, request rate, and hourly limits. Repeated provider failure should be escalated with safe metadata only.

### Transcript or note looks wrong

Do not treat it as authoritative. Correct/review manually and report repeatable provider/template quality issues using synthetic examples where possible.

### Generation repeatedly fails

Avoid rapid repeated requests because they can consume limits/quota. Report timestamp, consultation UUID, document status/error code, provider label/model, and action type—never transcript/note text in a general support ticket.

### Wrong provider/model shown

Stop the clinical workflow and ask the team leader/system administrator to verify team policy.

## Ask for help when

- authentication/recovery fails;
- speech/writing/de-identification policy is missing or unexpected;
- provider/model quality changes unexpectedly;
- capture/generation remains stuck or repeatedly fails;
- content appears visible to the wrong person;
- deletion/retention expectations are unclear;
- local policy does not clearly permit a workflow.
