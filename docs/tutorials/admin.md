# Admin Tutorial

## Audience

This tutorial is for system admins using the admin workspace after the first system admin account already exists.

For first-time bootstrap and infrastructure setup, read [System admin setup tutorial](system-admin-setup.md).

## Role Boundary

System admins manage teams, users, provider configuration, default assets, account requests, and usage metadata.

System admin accounts are admin-only in the MVP. They do not own transcript-derived content and do not gain transcript or generated-note visibility by default.

## Admin Workspace

Use `/admin` or `/admin2` for:

- teams and people
- account requests
- provider setup
- team provider assignment and selection controls
- default templates and quick actions
- usage and failure metadata

Admin pages must show operational metadata only. They must not expose transcript text, note text, prompts containing patient content, model responses containing patient content, provider secrets, reset tokens, or plaintext session identifiers.

## Manage Teams

Admins may create and manage teams. Team deletion is destructive and must not silently skip blockers.

Before team deletion, confirm cleanup can enumerate and remove:

- team users
- transcript-derived content
- team-scoped templates and quick actions
- provider config and selection rows
- usage metadata
- linked account requests
- provider credential references

If a system-admin account is still linked to the team, team deletion must block until that link is resolved.

## Manage Users

Admins may:

- create users
- send setup links
- reset or recover accounts
- lock or deactivate users
- perform system-level user deletion when allowed

System-level user deletion immediately deletes transcript-derived content and personal templates/actions. Treat it as irreversible.

## Provider Provisioning

Admins provision providers for teams. Raw provider credentials must be stored as Vault references in the database, not plaintext.

Provider areas include:

- speech-to-text
- LLM providers/models
- de-identification
- clinical NLP

When testing or inspecting providers, only safe operational metadata should appear in UI or logs:

- status
- provider/model labels
- error codes
- durations
- counts
- cost estimates

Do not log or display raw secrets, transcript text, prompts, generated note text, or provider responses containing patient data.

## Default Templates and Quick Actions

Default assets are admin-managed starter configuration. They are not transcript-derived content unless someone wrongly puts patient content into them.

When creating defaults:

- keep wording generic
- avoid real patient examples
- mark generated outputs as drafts needing review
- keep structured templates within EMIS section rules

## Usage and Failure Metadata

Usage views may show aggregate metadata such as counts, status, provider/model names, latency, token counts, and estimated cost.

Usage views must not show transcript-derived text or generated clinical content.

## Escalation Points

Pause and seek architecture direction before changing:

- ownership model
- privacy model
- deletion model
- encryption/key model
- provider resolution model
- structured-note JSON contract
- shareability of transcript-derived content

