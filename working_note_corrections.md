# Working Note Corrections Critique

## Kept

- Quick-action UI guard mismatch is real. Backend allows quick actions when saved transcript text is empty but saved Working note or saved dictation exists. Server-rendered controls and client JS must use that broader saved-source eligibility for quick-action controls.
- Keep follow-up generation separate. Follow-ups still require their own supported source path and do not implicitly consume Working note.

## Modified

- Use `active_quick_action_input_available` instead of overloading `active_note_input_available`. This keeps existing follow-up controls from silently changing source contract.
- Client JS now uses one `hasGenerationSource` guard for template note and quick-action eligibility: transcript draft, saved Working note, or saved dictation.

## Rejected

- Do not allow quick actions with zero user-created consultation source. Quick-action instructions alone are not clinical source material and would weaken the source-bounded generation contract.

## No Code Change

- Migration backfill caveat accepted. SQL backfill may not detect encrypted legacy structured context envelopes, but runtime decryption/fallback still infers structured Working-note mode when `working_note_mode` is null. No broad migration churn unless staging/prod data proves missing mode causes user-visible failure.
