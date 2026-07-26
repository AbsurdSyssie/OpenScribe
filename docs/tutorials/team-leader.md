# Team Leader Tutorial

## Role boundary

A team leader is a normal content-owning user with additional management authority for their own team.

Leader authority does not grant access to another user's transcript, working note, dictation, generated note, redaction/PII, or prompt content. Provider, user, request, and shared-asset screens are metadata/configuration surfaces.

Read [user.md](user.md) for the clinician workflow. This guide covers only leader functions.

## Canonical leader routes

Use the permanent workspace:

- AI services: `/workspace/team/ai-services`
- Members: `/workspace/team/members`
- Account requests: `/workspace/team/account-requests`
- Templates: `/workspace/library/templates`
- Quick Actions: `/workspace/library/quick-actions`
- Smart Phrases: `/workspace/library/smart-phrases`

Normal login currently lands on `/home`; use the workspace link for ongoing management. `/settings` redirects to canonical workspace sections.

## What leaders can do

Within their own team, leaders can:

- view service-selection status and choose/clear eligible provisioned STT, LLM-related, de-identification, clinical NLP, and other supported team policy selections;
- review/approve/reject matching account requests;
- create non-system-admin team users;
- send setup, password-reset, or full account-recovery links where mail is configured;
- perform approved break-glass recovery when route policy permits and email is unavailable;
- suspend/reactivate eligible non-system-admin team users;
- hard-delete eligible non-system-admin team users;
- create/manage Team Templates and Team Quick Actions;
- import/export personal and authorized team assets under current bundle rules.

Every action is still constrained by server-side own-team checks and protected-account rules.

## What leaders cannot do

Leaders cannot:

- manage users outside their team;
- create, promote, suspend, recover, or delete system administrators;
- act on themselves through manager suspend/reactivate/delete routes;
- provision/edit/reveal/replace/delete raw provider credentials unless a route is explicitly system-admin-only (current credential provisioning is system-admin-only);
- recover provider secrets or unrestricted Vault references;
- read another user's owner content;
- bypass MFA or normal recovery rules;
- change global retention maximums, encryption/key architecture, privacy boundaries, or deletion semantics.

## Confirm the team is ready

Before routine use, verify:

1. users can sign in and complete TOTP onboarding;
2. consultation STT is selected and tested;
3. post-consultation dictation STT is selected when that workflow is expected;
4. an approved writing assistant/default policy is available;
5. de-identification/clinical NLP policy is understood;
6. approved templates/Quick Actions exist;
7. users have completed local training and know all generated text is draft.

If no selectable provider appears, ask a system administrator to provision/finalize/activate one. Do not create a substitute URL or credential outside the approved setup flow.

## Manage team AI services

Open `/workspace/team/ai-services`.

Selections are policy references to system-admin-provisioned providers. Choosing/clearing a selection does not reveal or alter the raw credential.

### Speech-to-text

- Select only a provider/model approved for the intended purpose.
- Consultation capture and post-consultation dictation can use distinct selection purposes; verify both where relevant.
- Follow local validation/change-control before switching a live clinical team.
- Tell users when provider/model changes could affect quality or workflow.

### Writing assistant

- Confirm an approved team/default configuration exists.
- Users may have authorized model/preferences within team policy.
- Provider/model options should be tested with synthetic content before clinical rollout.

### De-identification and clinical NLP

- Select only assigned/available providers.
- When no valid remote de-identification selection exists, current runtime can use the built-in native Presidio path.
- Selection does not establish regulatory approval; confirm local data-processing requirements separately.

## Account requests

Open `/workspace/team/account-requests`.

Before approval:

- verify identity, email, requested team, and local authorization;
- choose only an allowed non-system-admin role;
- do not copy request details containing unnecessary personal/clinical information into other systems;
- use activation/setup email where configured or provide a temporary password through an approved out-of-band channel.

Reject a request when it does not belong to your team or lacks approval. Review notes should be minimal operational metadata.

## Create a team user

Open `/workspace/team/members` and use the available create/add-user flow.

- Confirm the normalized email and team.
- Choose `user` or `leader` only when authorized.
- Never set `is_system_admin` as a leader.
- Generate/provide a temporary password only through approved channels, or send a setup link.
- The new user must set a permanent password and enroll TOTP before full access.

## Setup and recovery

Preferred recovery order:

1. recovery code/self-service reset where available;
2. manager-sent password-reset or account-recovery email;
3. break-glass temporary password only when policy allows and email recovery is unavailable.

Break-glass requires the leader's own current TOTP code, a reason, explicit email-unavailable confirmation, and a protected/rate-limited route. The generated temporary password is shown once and must be transmitted securely. Full account recovery clears MFA/recovery codes; password-only break-glass preserves them but forces password change.

Never ask a user to send their password, TOTP seed/current code, recovery code, reset link, or session cookie.

## Suspend and reactivate

Suspension is the reversible manager state used to stop login/access without immediately deleting the account/content.

Use it for approved cases such as departure, suspected compromise, role/access review, or temporary access pause. Suspension/relevant security state revokes active authority.

Reactivation currently resets the user into password-change onboarding and clears prior MFA trust according to the implemented lifecycle. Tell the user they must complete setup again.

## Delete an eligible team user

Leader deletion is implemented for eligible non-system-admin users in the leader's own team. It is immediate hard delete, not archive/soft delete.

Before confirmation:

- verify the exact user and team;
- verify local authorization/retention obligations;
- understand that owned transcript-derived content and personal assets are deleted through current cascades/cleanup;
- ensure preserved account-request records can retain only nullable metadata links;
- do not use deletion merely to pause access—use suspension instead.

There is no undo path in the current product.

## Team Templates

Team Templates affect generated notes for every authorized team user.

- Use synthetic/approved examples only.
- Keep instructions narrow, clinically reviewed, and destination-specific.
- Choose freeform or structured mode deliberately.
- Structured EMIS templates use only: `problem`, `history`, `family_history`, `social_history`, `examination`, `comment`, `tasks`, `investigations`.
- Review version/active behavior after edits.
- Do not embed patient content in a reusable template.

## Team Quick Actions

Good Quick Actions describe one bounded drafting task, such as referral wording, follow-up instructions, or task summary.

Avoid actions that:

- make autonomous clinical decisions;
- tell the model to ignore clinician review;
- send patient-facing advice automatically;
- contain real patient examples;
- request unsupported disclosure or unsafe certainty.

Team configuration should never contain transcript-derived content.

## Import and export

Bundle operations enforce scope/authority server-side:

- Personal imports become caller-owned.
- Team Template/Quick Action imports require leader authority for the current team.
- Smart Phrase import is always Personal.
- Bundle-supplied owner/team/creator/version/active/usage metadata is ignored/rejected as authority.
- Preflight is read-only; confirmation revalidates and writes selected assets in one transaction.

Inspect every imported prompt/instruction before activation.

## Escalate to a system administrator

Escalate when:

- no suitable provider/config is available;
- a provider must be created, edited, inspected, diagnosed, reactivated, credential-rotated, or deleted;
- team creation/deletion or system-wide defaults are needed;
- a system-admin/protected account needs lifecycle action;
- provider errors suggest credential/endpoint/contract failure;
- privacy/access-control/encryption/deletion behavior appears incorrect.

Share only safe metadata: team/user email, provider/config label, status/error code, route/action, and timestamp. Do not paste transcript/note/dictation/prompt content, raw provider responses, credentials, reset tokens, TOTP values, recovery codes, or session identifiers.
