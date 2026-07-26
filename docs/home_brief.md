# Home and User Workspace Migration Record

## Status

The separately rendered `/home` compatibility landing is retired. Full normal-user and team-leader login now goes directly to `/workspace`; `GET /home` remains only as a temporary redirect for allowlisted legacy links.

This document records the completed migration and current compatibility boundary. The canonical user surface is `/workspace`, documented in [workspace.md](workspace.md). Navigation, forms, return routes, and product guidance use:

- `/workspace` — Scribe;
- `/workspace/account`;
- `/workspace/preferences`;
- `/workspace/library/templates`;
- `/workspace/library/quick-actions`;
- `/workspace/library/smart-phrases`;
- `/workspace/team/ai-services`;
- `/workspace/team/members`;
- `/workspace/team/account-requests`.

`/settings`, `/transcribe`, and `GET /home` are compatibility redirects into that permanent workspace. `/home2` is a development/preview surface, not the canonical contract.

## Role boundary

Normal users and team leaders can own transcript-derived content. Team leaders additionally manage own-team metadata and eligible accounts. System administrators are redirected to `/admin`, do not own transcripts, and do not receive owner-content visibility.

### Normal-user capabilities

- open/create consultation work in Scribe;
- manage own Account and Preferences;
- manage personal Templates, Quick Actions, and Smart Phrases;
- view/copy eligible same-team Templates/Quick Actions into independently owned personal assets;
- choose an allowed personal LLM model override and return to team default;
- sign out.

### Team-leader additions

Within their own team, leaders can:

- select/clear eligible team AI service policies;
- manage Team Templates and Team Quick Actions;
- review matching account requests;
- create non-system-admin members;
- send activation/reset/recovery links;
- use approved break-glass recovery where policy permits;
- suspend/reactivate eligible non-system-admin members;
- hard-delete eligible non-system-admin members.

The earlier statement that leaders intentionally omitted hard deletion is obsolete. Deletion is implemented, server-scoped, immediate, and irreversible. Use suspension to pause access.

Leaders cannot manage system administrators, users outside their team, or themselves through manager suspend/reactivate/delete routes. Management authority never grants another user's transcript, working-note, dictation, generated-document, redaction, or PII access.

## Account behavior

Canonical account changes live under `/workspace/account`.

- Name changes are owner-only profile metadata updates.
- Email/password changes require current-password reauthentication and fresh TOTP when an active method exists.
- Successful sensitive changes revoke existing sessions/trusted devices and issue one replacement session to the initiating browser.
- Audit records contain bounded field/action/outcome metadata, not submitted credentials or personal values.

## Preferences

Canonical preferences live under `/workspace/preferences`.

- Recording mode controls Create consultation behavior across workspace sections.
- Note length/detail preferences are saved for future template generation.
- Allowed model overrides are validated against the current team LLM policy.
- Saving visible style settings preserves a valid existing model override unless the user changes/clears it.

## Library

Canonical Library routes use a master-detail workspace layout and server authorization.

### Templates

- Personal: owner create/edit/copy/duplicate/delete/activate behavior.
- Team: normal users read/copy same-team assets; leaders manage own-team assets.
- Structured templates use the fixed EMIS section-key contract.
- Reusable assets must not contain patient/transcript content.

### Quick Actions

- Personal assets are owner-managed.
- Same-team Team assets are readable/copyable by normal users.
- Leaders manage Team Quick Actions.
- Copy creates an independent personal snapshot/version.

### Smart Phrases

- Personal only.
- Stored trigger is uppercase without the leading slash.
- Users can create/edit/duplicate/delete and record usage.
- They are configuration, not a team-shared content mechanism.

See [editor-smart-phrases.md](editor-smart-phrases.md).

## Team AI services

The canonical leader surface is `/workspace/team/ai-services`.

Team policy is separate from system-admin provisioning:

- system administrators provision credential-bearing ready provider configs;
- leaders choose/clear eligible options for their own team;
- consultation STT and post-consultation dictation STT can be separate purposes;
- LLM team policy controls allowed/default models;
- de-identification and clinical NLP selections remain distinct;
- policy screens expose labels/status/options, never raw credentials or unrestricted Vault references.

## Team members and requests

Canonical leader routes:

- `/workspace/team/members`;
- `/workspace/team/account-requests`.

Every action is validated server-side for own-team/non-system-admin scope. Account requests are normalized/deduplicated and remain metadata-only. Temporary passwords/setup links are credentials and must be sent only through approved channels.

## Compatibility behavior

- Successful full-session normal-user/team-leader login redirects to `/workspace`; system administrators continue to `/admin`.
- `GET /home` no longer renders the old landing. It temporarily maps allowlisted legacy tabs and selected Template/Quick Action identifiers into canonical workspace sections.
- Established mutation handlers can retain paths with the `/home` prefix while canonical workspace forms and feedback use them. These are compatibility implementation endpoints, not primary navigation.
- `/settings` maps a closed set of tabs/identifiers to canonical workspace routes and drops arbitrary query parameters.
- `/transcribe` redirects to `/workspace`, preserving only validated `transcript_id`.
- Preview routes are development surfaces and should not be linked as primary navigation.

## Completed migration

The landing was retired after the migration established:

1. normal-user/team-leader login redirects directly to `/workspace`;
2. every Home mutation/feedback path has a canonical workspace equivalent;
3. role/access/security/CSRF/redirect regression coverage passes;
4. legacy links/return values are removed or explicitly redirected;
5. root README, auth/workspace/tutorial docs are updated together.

Future cleanup can rename or remove legacy mutation paths with the `/home` prefix only as a separate route-contract change with focused authorization, CSRF, feedback/redirect, and browser regression coverage. It must not weaken owner/team scope or make the old landing a product surface again.
