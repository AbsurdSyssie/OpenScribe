# OpenScribe Tutorials

These tutorials describe current product behavior by role. For deployment and operator configuration, use the main [documentation index](../README.md).

Recommended order:

1. [Onboarding tutorial](onboarding.md)
2. [User tutorial](user.md)
3. [Team leader tutorial](team-leader.md)
4. [Admin tutorial](admin.md)
5. [System admin setup tutorial](system-admin-setup.md)

User guidance also applies to team leaders when they use OpenScribe as clinicians. The team leader tutorial covers only additional own-team management authority. The admin guides cover metadata/configuration operations and do not grant owner-content visibility.

The workspace migration is transitional: `/workspace` is canonical for Scribe, Account, Preferences, Library, and leader Team sections, while normal-user login currently still lands on `/home`. Tutorials should direct ongoing work to canonical workspace routes and identify `/home` only as the compatibility landing.

Tutorial content is product guidance. Never add real transcript or note text, prompts containing patient content, provider secrets, setup/reset tokens, TOTP values, recovery codes, or plaintext session identifiers.
