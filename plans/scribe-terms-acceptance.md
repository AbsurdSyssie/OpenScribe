# Scribe Terms acceptance

## Access and rollout

- Members and team leaders must accept the Terms before using the Scribe, including existing accounts. System administrators are exempt. Apply the requirement after full authentication across all sign-in methods.
- Use the Terms publication pinned at sign-in for that session. An existing session at rollout resolves its requirement on first Scribe access. Preserve the pin during session rotation. Publishing a new Terms version applies to subsequent sign-ins, and posting acceptance from an older session records only the version shown to that session. If no Terms existed when the session was pinned, the Scribe remains available for that session.
- On entry to the Scribe, render an accessible dialog with the published Terms using the existing validated legal blocks. Check the requirement before reading any clinical data or rendering clinical content. Acceptance is an authenticated, CSRF-protected form submission. The secondary action, **I don’t accept**, goes to Preferences. Selecting Scribe again opens the dialog. Other workspace sections retain their existing access rules.
- Apply the same check to browser mutations, clinical JSON APIs, and new live-stream connections. Preserve each route’s existing authorization and deny unaccepted clinical API use with a stable 403 error code. Background tasks and established streams follow their existing lifecycle. Keep any existing stricter role, ownership, capture or CSRF checks.

## Evidence and administration

- Store one row per user and accepted Terms version, with a server-assigned UTC timestamp. Make repeated and concurrent accepts idempotent. Reject a submitted version that differs from the session’s requirement. Reuse operator-published Terms and their existing retention. Keep the acceptance version identifier after routine legal-document retention removes superseded text.
- In the system administrator account view, show the accepted versions and dates. In the account deletion confirmation, offer a CSV download before deletion. Include account name, full name, Terms version and acceptance time in UTC, with spreadsheet-safe values and no-store caching. Keep this evidence system administrator–only, including where existing team-leader deletion authority applies. Delete acceptance rows with the account.

## Implementation and verification

1. Persistence and auth: add the acceptance model and migration, implement idempotent acceptance and session requirement resolution, and preserve the requirement on authentication rotations. Verify model behavior, rollout, and session transitions.
2. Scribe enforcement and UI: wire the browser entry, accept action, clinical browser routes, JSON and streaming boundaries. Review the complete route inventory and update the authorization manifest. Verify denial before clinical access, navigation and retry, successful acceptance, and non-Scribe compatibility.
3. Admin evidence: add the per-account history and CSV download in the deletion interaction. Verify role checks, output and account deletion behavior.
4. Integrate with work already present in the checkout. Run focused pytest and the API authorization audit sequentially, check migrations and maintained docs, and have an independent agent review the integrated changes before reporting back.
