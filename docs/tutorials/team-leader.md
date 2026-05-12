# Team Leader Tutorial

## What This Role Means

A team leader manages a team inside OpenScribe.

You can help people get access, choose team-level settings, and manage shared team assets.

You are not given access to another user's transcript text or generated notes just because you are a team leader.

## Read This First

Read [User tutorial](user.md) before this page.

When you use OpenScribe as a clinician, you follow the user workflow exactly like any other user. This page only explains the extra team leader controls.

## Important Words

`Team` means the group of users who share provider settings and team assets.

`Provider` means an external or internal service used by OpenScribe, such as speech-to-text, writing assistant, or de-identification.

`Speech service` means the provider that turns audio into transcript text.

`Writing assistant` means the provider/model used to draft notes and follow-ups.

`Team template` means a note template available to the team.

`Team quick action` means a reusable team prompt for follow-ups or other generated text.

`System admin` means the person who provisions providers, secrets, teams, and high-level system configuration.

## What You Can Do

As a team leader, you may be able to:

- check team service status
- choose the active team speech service from available options
- choose active team de-identification provider from available options
- manage team users
- send setup links
- lock or deactivate users
- help with account recovery
- create and manage team templates
- create and manage team quick actions
- review account requests for your team

Available controls depend on the current build and local policy.

## What You Cannot Do

You cannot:

- read another user's transcript content
- read another user's generated notes
- recover raw provider secrets
- create provider credentials from scratch unless a system-admin route explicitly allows it
- fully delete users in the MVP
- bypass MFA for routine access
- change privacy, deletion, encryption, provider, or structured-note rules

If you need one of those actions, escalate to a system admin or architecture owner.

## First Thing to Check: Is the Team Ready?

A team is ready for normal use only when the basics are in place.

Check:

1. Users can sign in and complete onboarding.
2. The team has an active speech service.
3. The team has an available writing assistant.
4. Templates needed for normal workflow exist.
5. Users know generated notes are drafts that require review.
6. Local clinical validation and training are complete.

If speech or writing services are missing, users may be unable to record, upload, transcribe, or generate notes.

## Check Team Services

Open Home and go to the team or AI services area.

Look for:

- active speech-to-text selection
- active writing assistant/default model
- de-identification provider, if configured
- clinical NLP provider, if configured

If no option appears, do not guess. Ask a system admin to provision providers for the team.

## Choose the Team Speech Service

The speech service affects transcript quality. Transcript quality affects generated notes.

To choose it:

1. Open Home.
2. Go to AI Services.
3. Find speech-to-text settings.
4. Read the available provider labels.
5. Choose the provider/model approved for your team.
6. Save the selection.
7. Tell users if the change affects their workflow.

Do not switch speech service casually during clinical use. Follow local validation/change-control process.

## Choose the Team De-Identification Provider

De-identification helps remove or transform identifiable information when a workflow needs it.

To choose it:

1. Open Home.
2. Go to AI Services.
3. Find de-identification settings.
4. Choose an assigned provider.
5. Save the selection.

If no valid team selection exists, OpenScribe uses the built-in native Presidio fallback.

## Manage Users

Use team user management to help people access the system safely.

Common tasks:

- approve or reject account requests
- create or invite team users where supported
- send setup links
- help users who are stuck in onboarding
- lock or deactivate users who should not currently access the system
- start approved account recovery

Locking or deactivating a user revokes access. It does not delete their transcript-derived content.

Team leaders cannot fully delete users in the MVP. Full user deletion is a system admin action because it deletes transcript-derived content and personal templates/actions.

## Send a Setup Link

A setup link lets a user finish their account setup.

Before sending:

1. Confirm the person belongs to your team.
2. Confirm the email address is correct.
3. Confirm local approval exists.
4. Send the setup link through the approved OpenScribe control.

Tell the user:

- use the newest link only
- do not forward the link
- complete password setup
- complete MFA setup
- ask for help if the link expires

## Lock or Deactivate a User

Use lock/deactivate when a user should no longer access OpenScribe for now.

Examples:

- person left the team
- account may be compromised
- role changed
- access should pause during review

This revokes sessions immediately. It does not alter the user's existing content state.

## Manage Team Templates

Team templates shape generated notes for everyone who uses them.

A template is not just a label. It is instruction text that can change what the writing assistant includes, excludes, or emphasises.

Before creating a team template:

1. Decide what clinical document it is for.
2. Decide whether it should be structured or freeform.
3. Write plain instructions.
4. Avoid real patient details.
5. Test with synthetic or approved training material.
6. Get local clinical approval before routine use.

Structured EMIS templates must use allowed EMIS section keys only:

- `problem`
- `history`
- `family_history`
- `social_history`
- `examination`
- `comment`
- `tasks`
- `investigations`

Poor templates can cause notes to miss important details, overstate findings, or put information in the wrong place.

## Manage Team Quick Actions

Team quick actions are reusable drafting tasks.

Good quick actions are narrow and clear. For example:

- draft a referral summary
- draft follow-up instructions
- summarise tasks from the consultation

Avoid quick actions that:

- make clinical decisions automatically
- send patient-facing advice without review
- include real patient examples
- ask the model to ignore clinician review
- produce output that users may copy without checking

Team quick actions are configuration. They should not contain transcript-derived text.

## What to Tell New Users

When a new user joins, tell them:

1. Finish onboarding and MFA.
2. Read the user tutorial.
3. Start with test/training material if available.
4. Confirm they can open the consultation workspace.
5. Confirm they understand generated notes are drafts.
6. Confirm they know who to contact when speech or writing services fail.

## Escalate to System Admin

Escalate when:

- no speech service is available
- no writing assistant is available
- a provider must be created, edited, inspected, tested, or deleted
- a provider secret must be replaced
- team deletion or system-level cleanup is needed
- a user must be fully deleted
- provider/model behaviour looks unsafe or unexpected
- you suspect a privacy or access-control problem

When escalating, share safe metadata such as user email, team name, provider label, error code, timestamp, and status. Do not paste transcript text, note text, prompts containing patient content, provider secrets, reset tokens, or session identifiers.

