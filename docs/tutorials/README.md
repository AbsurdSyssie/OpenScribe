# OpenScribe Tutorials

These tutorials describe current product behavior by role. For deployment and operator configuration, use the main [documentation index](../README.md).

Recommended order:

1. [Onboarding tutorial](onboarding.md)
2. [User tutorial](user.md)
3. [Scribe workflow guide](scribe-workflow-guide.md)
4. [Team leader tutorial](team-leader.md)
5. [Admin tutorial](admin.md)
6. [System admin setup tutorial](system-admin-setup.md)

The Scribe workflow guide applies to users and team leaders. The team leader tutorial covers only extra authority within the leader's own team. The admin guides cover metadata and settings. They do not grant access to user-owned content.

`/workspace` is the main route for Scribe, Account, Preferences, Library, and leader Team sections. Normal users and team leaders land there after sign-in. Old `GET /home`, `/transcribe`, and `/settings` links redirect to the matching workspace route.

Tutorial content is product guidance. Never add real transcript or note text, patient prompts, provider secrets, setup or reset tokens, TOTP values, recovery codes, or plain session identifiers.
