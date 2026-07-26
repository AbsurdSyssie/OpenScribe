# Onboarding Tutorial

## Audience

This tutorial is for normal users, team leaders, and system administrators completing first account setup after receiving a temporary password or an activation/setup link.

## Protect setup material

A setup link or temporary password is a credential.

- Use only the newest link or temporary password supplied through the approved channel.
- Do not forward it or paste it into chat/tickets.
- Ask a team leader or system administrator for replacement when it is expired or invalid.
- A setup link is single-use and does not provide full application access by itself.

## Set a permanent password

1. Open the activation/setup page or sign in with the temporary password.
2. Enter a new permanent password.
3. Submit the form.
4. Continue to TOTP setup.

The password must be at least 12 characters and include uppercase, lowercase, and numeric characters. Use a unique password that is not shared with another clinical or personal system.

Until password change and required MFA enrollment are complete, the session is restricted to onboarding/current-user/logout routes.

## Enroll TOTP MFA

1. Start TOTP enrollment.
2. Scan the QR code with an approved authenticator app or enter the displayed secret manually.
3. Enter the current six-digit code.
4. Verify the code.

The seed/QR code is shown only during the authorized enrollment flow. Do not ask another person to copy or store it for you.

## Recovery codes

OpenScribe offers optional recovery codes after successful TOTP enrollment.

When generating them:

- store them in a location approved by local policy;
- do not put them in a shared chat, ticket, or clinical record;
- treat each code as a one-time credential;
- understand that OpenScribe cannot recover their plaintext from the database.

Skipping recovery codes is allowed, but loss of the authenticator may then require an approved manager-assisted recovery flow.

## Complete onboarding

After the final onboarding step, sign in normally:

1. enter email and permanent password;
2. complete the authenticator challenge when requested;
3. verify the correct destination:
   - system administrator: `/admin`;
   - normal user or team leader: `/workspace`.

The permanent workspace contains Scribe, Account, Preferences, Library, and leader-only Team sections. See [user.md](user.md) and [team-leader.md](team-leader.md).

## Remember this browser

On the MFA challenge, a user can choose to remember the browser. This does not replace password authentication and does not create permanent MFA bypass. It allows a correct password login to skip TOTP only while the server-side trusted-device record remains valid and within the current 24-hour MFA freshness window.

Use it only on an approved personal/managed device. Sign out and report the device when it is lost or compromised.

## Problems during onboarding

Ask a team leader or system administrator for help when:

- the setup link or temporary password is invalid/expired;
- password validation repeatedly fails despite meeting the stated rule;
- the QR code cannot be scanned;
- authenticator codes repeatedly fail after checking device time;
- access to the authenticator or recovery codes is lost;
- the account returns to onboarding unexpectedly.

Routine support must not bypass MFA. Use recovery codes, email recovery, or the approved manager-assisted recovery paths described in [../auth.md](../auth.md).
