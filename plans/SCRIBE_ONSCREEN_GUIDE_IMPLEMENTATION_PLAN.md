# Scribe on-screen guide implementation plan

## Intent

Repair the existing user-opened Scribe Guide as an accessible, read-only walkthrough of the current consultation workflow. Keep the established sidebar opener and tour module; remove automatic startup and tour persistence.

## Scope and behavior

- Present ten short, static workflow steps in a native modal `<dialog>` with an accessible title, current-step description, live progress, Back, Next/Finish, Close, and Escape dismissal.
- Highlight only the current step's visible target. If a target is hidden or absent in an empty/new consultation, highlight the visible Scribe header fallback and continue with the same static explanation.
- Use the existing layout controller only to show a tab needed by a step. Restore the starting tab on every close path, including Escape and Finish.
- Block opening during active consultation recording/upload, active dictation capture/busy state, or another visible modal. Report the reason without changing recording or draft state.
- Never submit forms, invoke providers, read or alter consultation content, start recording, or mutate source/draft state from guide controls.
- Do not read, write, or remove the legacy `openscribe:tour:transcribe:<role>` localStorage key. Leave existing values untouched.
- At 320 CSS px, keep the card within the viewport and its content scrollable. Provide a high-contrast focus indicator and respect reduced-motion preferences.

## Static step outline

1. Start a consultation and verify its title.
2. Return to recent work only for the same encounter.
3. Choose an audio mode and confirm consent; keep the page open during capture.
4. Review the transcript as draft source material.
5. Keep the Working note separate from transcript and generated output; check its save status before creating a draft.
6. Treat post-consultation dictation as a separate source; preview before saving and cancel unsaved work.
7. Choose the approved template and create a draft.
8. Review and edit every section and version for correctness, omissions, invented text, and placement.
9. Prepare follow-ups with a Quick Action, add context, review the resulting draft, and do not assume it is sent.
10. Copy only verified text to the correct patient, consultation, and EPR field; reread the pasted entry. Copy visibility is not approval.

## Verification contract

Audit the manual-open flow on empty and populated synthetic Scribe states at desktop and narrow viewport sizes. Verify dialog name/role, keyboard containment, Back/Next/Finish/Escape, focus return, target fallback, original-tab restoration, no source/provider/consultation mutations, and the explicit clinical review/copy gate. Automated audit owns DB-backed tests and execution.

## Status

Implemented and verified by the final automated run: 24 accessibility tests, 25 scans, zero axe violation nodes, and 39 workspace regressions passed. Human screen-reader/AT review, true browser zoom at 200%, and a live capture guard exercise remain pending; the isolated synthetic fixture has no safe capture lifecycle hook.
