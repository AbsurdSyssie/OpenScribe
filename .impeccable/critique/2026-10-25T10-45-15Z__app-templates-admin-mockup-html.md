---
target: admin area
total_score: 24
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 1
target_identity: "file:/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin_mockup.html"
target_fingerprint: "sha256:3c8d97a04c4786c012c2a5748b7ff18448352ca91eacca076668b7bca909aaf9"
target_path: /home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin_mockup.html
timestamp: 2026-10-25T10-45-15Z
slug: app-templates-admin-mockup-html
closed: true
---
Method: dual-agent (A: /root/admin_design_assessment · B: /root/admin_detector_assessment)

**The admin area has the right identity, but uneven execution.** Keep the warm surfaces, teal accents and serif headings. The largest gain will come from applying the quota editor’s clear grouping and spacing to older forms, then improving navigation and mobile data layouts.

**Design specificity and overall impression**

This feels like OpenScribe rather than a generic dashboard. Provider purposes, team scope and resource-specific quotas give it useful product character. However, moving from the quota editor to Defaults feels like moving between a finished screen and an unfinished form.

Fresh synthetic browser captures covered Overview, Members, Defaults, Provider policy, team/global Usage and the quota editor at 1440px and 390px. This is a design assessment, not a new WCAG conformance audit or a test of production data.

**Design health: 24/40 — Acceptable; substantial improvements remain.** Scores are qualitative judgments, not measured usability results.

| Heuristic | Score /4 | Main finding |
|---|---:|---|
| System status | 3 | Team, active tab, provider status and quota periods are visible. |
| Match with users’ language | 3 | Purpose descriptions help; some provider terminology needs context. |
| Control and freedom | 2 | Exit paths exist, but the location of settings is inconsistent. |
| Consistency and standards | 2 | Defaults and Overview forms fall below the quota editor’s standard. |
| Error prevention | 2 | Numeric bounds help; crowded default fields hinder checking. |
| Recognition over recall | 3 | Scope and values are visible; mobile columns separate context from actions. |
| Efficiency | 2 | Search and direct member actions help; navigation requires repeated scanning. |
| Aesthetic and minimalist design | 2 | Calm identity, uneven spacing, and a heavy mobile page header. |
| Error recovery | 3 | Source provides validation and feedback; recovery paths were not freshly exercised. |
| Help and documentation | 2 | Useful inline explanations, limited contextual help. |
| **Total** | **24/40** | **Acceptable** |

**What’s working**

- The approved quota editor is a strong reference: clear resource groups, explicit units, current settings and a focused save/cancel area.
- Members works reasonably well on desktop. Search, account metadata and the restrained table are useful; turning every row into a large card would add unnecessary bulk.
- The selected team and provider purpose descriptions make scope and configuration understandable without exposing clinical content.

**Priority issues**

1. **[P1] Defaults uses cramped, unfinished-looking controls.** Four consequential quota values run together on desktop and interleave awkwardly as labels and inputs wrap on mobile. This makes daily/monthly and tokens/audio harder to verify before saving.
   **Fix:** reuse the quota editor’s two resource groups, stacked labels, consistent input dimensions and explicit units. Keep “new members only” beside the form and its save action. Apply the same field treatment to the bare retention form.
   **Command:** `$impeccable layout`.
   Evidence: [Defaults desktop](/home/oscar/Documents/Code_Projects/OpenScribe/output/admin-critique-2026-10-25/defaults-desktop.png), [Defaults mobile](/home/oscar/Documents/Code_Projects/OpenScribe/output/admin-critique-2026-10-25/defaults-mobile.png).

2. **[P2] Retention is edited in an unexpected place.** Overview contains the retention editor, while Defaults only displays its current value. The maintained function map also describes Overview as read-only. Users should not have to remember that this particular default lives elsewhere.
   **Fix:** make Overview a useful summary with a link to the retention setting, and put the editor under Defaults. Preserve the existing server-owned retention rules and route behavior; align the documentation when implementing.
   **Command:** `$impeccable shape`.
   Evidence: [Overview](/home/oscar/Documents/Code_Projects/OpenScribe/output/admin-critique-2026-10-25/overview-desktop.png); source `app/templates/admin_mockup.html`, Overview form around line 4078 and Defaults around line 4437.

3. **[P2] Eleven peer-level team tabs create needless scanning.** Overview, Members, Quotas, Provider policy, STT, LLM, De-identification, Defaults, Usage, Security and Danger zone compete at the same level. On mobile they occupy three rows before the task begins.
   **Fix:** group related provider destinations and less frequent settings, keeping the active destination obvious. Preserve the distinction between global administration and selected-team settings. Validate the grouping before changing routes or labels.
   **Command:** `$impeccable shape`.

4. **[P2] Mobile tables separate identity from status and actions.** Members and Provider policy retain wide desktop grids. Horizontal scrolling is available, but users must pan to connect a member or provider purpose with its later columns and action.
   **Fix:** retain efficient desktop tables; on narrow screens show an essential summary with labelled expandable details, or compact stacked rows. Keep identity, status and the action together and preserve complete data access.
   **Command:** `$impeccable adapt`.
   Evidence: [Members mobile](/home/oscar/Documents/Code_Projects/OpenScribe/output/admin-critique-2026-10-25/members-mobile.png), [Provider policy mobile](/home/oscar/Documents/Code_Projects/OpenScribe/output/admin-critique-2026-10-25/provider-policy-mobile.png).

5. **[P2] Incomplete deployment setup overwhelms the mobile task header.** In the synthetic incomplete-setup state, the legal warning, team context and wrapped tabs push the Defaults heading below the initial phone viewport. The warning is necessary; its repeated presentation is expensive.
   **Fix:** retain a prominent warning summary and direct setup link, with accessible disclosure of the individual missing items where appropriate. Keep required and urgent information visible; do not simply hide or dismiss it. Reduce repeated header copy before shrinking readable text.
   **Command:** `$impeccable distill`.

**Cognitive load and emotional journey**

Three of eight checklist items fail: single focus, chunking and minimal choices. Grouping, hierarchy, sequential actions, visible context and progressive disclosure generally work. The eleven peer tabs are the clearest excessive choice set; seven provider-purpose rows are legitimate operational data and do not automatically need hiding.

The warm shell establishes confidence. That confidence drops when a carefully structured quota editor leads to cramped Defaults fields, or when a phone user must pan between a provider’s identity and its action. Consistent controls and clearer task locations would improve trust more than additional decoration.

**Persona red flags**

- **Alex, frequent administrator:** eleven tabs slow repeated navigation. Preserve desktop table density, member search and the existing Manage quotas action.
- **Sam, keyboard or screen-reader user:** horizontal scroll regions preserve access but make long rows harder to follow. Preserve the recent focus, dialog and labelled-region work; real screen-reader and zoom checks remain necessary.
- **Casey, interrupted mobile user:** a large preamble and separated table columns make it harder to recover the current task. Keep the selected team and primary action near the content.

**Minor observations**

- Provider policy mixes setup links with editable selectors and per-row Save buttons. Make “configure a connection” and “select a provider” visually distinct while preserving their different responsibilities.
- Keep Georgia for headings; long email addresses need restrained sizing and robust wrapping.
- Usage is a very long mobile report. Section links and expandable detail could help without removing its accessible data tables.

**Independent detector check**

The source scan returned **84 flags: 34 warnings and 50 advisories** across `admin_mockup.html` and the two quota partials: 49 design-token mismatches, 12 padding flags, 15 small-text flags, six heading skips and two decorative-pattern flags. Examples include font-size comparisons at main-template lines 48/87/91 and radius comparisons around lines 974/988. Many warnings have no usable source line (reported as 0).

These are **not 84 confirmed defects**. Several token mismatches are documented admin-specific values; isolated partials lose inherited styles, and Jinja branches do not all appear on one page. The scan adds useful small-text and heading-outline follow-ups but does not replace the visual findings above. A static quota-page overlay returned nine further flags; incomplete assets and stalled browser capture mean those are unverified, and no reliable user-visible overlay is claimed.
