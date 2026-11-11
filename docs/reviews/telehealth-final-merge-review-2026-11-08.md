# Telehealth final merge review — 8–9 November 2026

## Scope

Reviewed the complete `feature/telehealth-dual-channel-capture` branch at
`565dfe0912ca9f8006d1497e0de3bd84678fb51b`, plus the fixes described below, against
`master` and freshly fetched `origin/master` at
`bf63c2879dd8526db582a9598663db92df13642b`. The target is an ancestor of the
branch. Review started on 8 November and completed on 9 November 2026. This is
a dated review record, not a replacement for maintained operational contracts
or clinical release approval.

Sol coordinated scope and reviewed the combined changes. Terra reviewed backend
ownership, encryption, capture/dispatch/application, quotas, retries, deletion,
retention and shared generation/STT services, and implemented the provenance
and administrator metadata fixes. Luna independently reviewed frontend capture,
crash recovery, navigation, ordinary recording integration and browser asset
imports, implemented the browser fixes, and ran verification.
The final patches received an independent second source review.

## Findings and fixes

- **Discarded audio could create an unrelated clinical warning.** An
  acknowledged interrupted capture retained its audit metadata after discard.
  Later structured-source refreshes could add its protected incomplete-recording
  notice to other source content. Refresh now manages the existing warning ID
  but excludes discarded captures from notice generation. Current stale notices
  are removed on refresh; historical versions and discard audit metadata remain
  intact. The regression covers removal and subsequent source appends.
- **The journal startup check skipped expiry.** Starting another capture could
  leave encrypted recovery audio older than six hours until a later list or
  write. The readiness check now prunes under the existing cross-tab lock.
  Tests retain fresh windows and their keys, including a fresh key whose first
  window has not yet committed. The six-hour and eight-window bounds are unchanged.
- **Recovery trusted matching timing as an acceptance receipt.** An existing
  server part could cause local audio deletion without checking its original
  key and bytes. Recovery now uses the existing idempotent staging endpoint
  before removing each retained window, including exact retries after sealing.
  Conflicts retain the encrypted window. Tests cover conflicting identities,
  exact retries and stable replay identities.
- **Failure paths could retain decrypted audio buffers.** Invalid server
  ledgers and partial decryption failures could bypass buffer clearing. Both
  paths now clear already decrypted buffers. Tests exercise each failure.
- **Recovery could be mistaken for a passive action in another tab.** The
  restored-capture confirmation now requires stopping recording in other tabs
  and devices and explains that continuing stops/finalizes the capture and may
  lose unflushed speech. Automatic live-recorder detection is not implemented;
  adding leases or heartbeats would be a separate lifecycle design.
- **An existing administrator deletion defect blocked broad verification.**
  The same test failed on an isolated archive of master because a model-policy
  updater foreign key was omitted from retained metadata reassignment. The
  bounded fix applies the existing deletion-actor reassignment to that metadata
  reference. It does not transfer clinical content. The existing deletion test
  now asserts the persisted updater. See `ADMIN-DELETE-LLM-MODEL-POLICY-FK` in
  [the issue log](../../DISCOVERED_ISSUES.md).
- **Workspace refresh could close an open Follow Ups menu.** The history
  renderer rebuilt its DOM, losing menu state between opening and interaction.
  The fix preserves the open menu and its focused control only when the same
  consultation and document remain present, and repositions it through the
  existing CSSOM path after filtering. A browser regression deliberately holds
  and releases the workspace refresh while the menu is open.
- **The full suite exposed stale fixtures and two baseline inline styles.**
  Older application VM tests omitted the imported Telehealth dependency;
  asset-version assertions and several baseline markup/schema/help fixtures
  were stale. These now exercise the current contracts without removing their
  behavior or security assertions. The administrator CSP browser check accepts
  the canonical usage destination and verifies HTTP 200. Two redundant quota
  `style` attributes were removed; existing validated CSSOM data hooks and ARIA
  attributes remain. The inline-style security check is unchanged.

The affected journal, recording-client, UI and application imports and HTML
entry point have refreshed cache versions. Matching synthetic smoke fixtures
were updated. Maintained capture, browser-storage and authentication references
describe the implemented behavior. No new migration or configuration is
required by these review fixes; the branch's existing additive migrations must
still be applied before deploying it.

## Verification

The first broad focused run passed **895 tests** and failed the administrator
deletion test. That failure was reproduced on the exact master baseline before
implementation was changed. The repaired administrator test passed. Separate
discard/partial recovery checks passed **15 tests**; final journal, client, UI,
discard and partial recovery checks passed **20 tests**.

The first full run passed **3,148 tests**, skipped **47**, and failed **34**.
Twenty-two failures came from the missing application-VM dependency binding.
The remaining failures were checked against an isolated master archive: eight
reproduced there. The investigation also isolated the menu refresh race.
After the fixture and baseline repairs, the previously failing nodes passed
**33 tests**. The final browser pair covering the refresh-preserved Follow Ups
menu and admin CSSOM passed **2 tests** with **4 warnings** in **9.59 seconds**.
The browser regression holds the selected transcript's real workspace request,
changes the follow-up's `updated_at` in the test database, then releases the
request and verifies menu state after the API-driven render. The administrator
metadata reassignment test passed **1 test** with **2 warnings** in **4.91
seconds**. Recovery/discard focus checks passed **20 tests** with **2 warnings**
in **4.35 seconds**. Standalone Telehealth browser smoke, stop-UI smoke and
maintained-document validation passed; the document validator checked **54
maintained documents**. `node tests/telehealth-browser-smoke.mjs` passed its
synthetic capture, VAD and diagnostics checks, and
`.venv/bin/python scripts/smoke_telehealth_stop_ui.py` passed the retry/discard
controls. The final full suite passed **3,182 tests**, skipped **47**, and
reported **52 warnings** in **245.67 seconds**. The skips were 46 opt-in
accessibility audit cases and one Docker transport integration requiring
`redis-server`. The API authorization audit exited **0**.

## Merge and release boundaries

The review recommends merging with Telehealth behind its existing default-off
feature flag. It does not authorize production enablement, provider calls with
patient content, or clinical use. Actual microphone/tab/window recording and
platform-specific audio support still require the maintained physical
walkthrough and clinical/privacy release checks. Synthetic media tracks do not establish browser/OS support.
The reported Ubuntu Brave/Edge picker offers tab audio but no desktop audio;
native Linux audio routing is not added by this branch.

No owner-access, root retention/deletion, encryption-key, provider credential,
quota/outbox, redaction or structured-output contract was redesigned.
