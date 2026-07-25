# MFA Secret Encryption

## Implemented behavior

New and re-enrolled TOTP methods store their Base32 seed in `user_mfa_methods.secret` as a versioned AES-256-GCM envelope. The envelope is encrypted with the owning user's existing per-user DEK. Its associated data binds the value to:

```text
openscribe:v1:user_mfa_methods:secret:{user UUID}:{method UUID}
```

Consequently, moving an encrypted seed to another user or MFA-method row makes it unreadable. The DEK remains wrapped by Vault; neither a plaintext seed nor a plaintext DEK is persisted for this path.

Enrollment returns the plaintext seed only to the authenticated user completing onboarding, so that user can provision an authenticator app. The browser enrollment flow and API responses are `no-store`; the seed, provisioning URI, and QR material must not be logged, cached, or exposed to managers, administrators, or other users.

TOTP verification distinguishes an unavailable encryption dependency from an invalid authenticator code:

- Vault/key-access failures for an encrypted method fail closed as `503 mfa_service_unavailable`; no remembered-device authority is created.
- A malformed envelope, missing DEK, failed AEAD validation, or invalid stored seed is a controlled `500 mfa_secret_unreadable` failure.
- A readable seed with a wrong TOTP value remains the ordinary `422 business_rule_violation` invalid-code result.

Recovery codes are not encrypted MFA secrets: they remain hash-only and are shown only once when generated.

## Development and pre-production compatibility

Legacy plaintext Base32 seeds remain temporarily readable. Reading a legacy row does **not** encrypt or rewrite it, create a DEK, unwrap Vault material, or otherwise mutate the account. This preserves access for existing development accounts during the transition.

This fallback is compatibility behavior, not the target production state. It must be removed only after an explicit migration/backfill and operational recovery plan are in place.

All newly created local users, including teamless system administrators, receive a per-user DEK. This key eligibility supports encrypted authentication material; it does not make system administrators transcript owners and does not change transcript authorization. Transcript-derived content remains owner-only.

## Recovery and destructive key reset

- Password-only reset preserves enrolled TOTP methods and recovery-code hashes, while revoking sessions and trusted devices.
- Explicit MFA reset and account-recovery flows clear TOTP methods and recovery-code state, revoke sessions and trusted devices, and require TOTP reenrollment (subject to the existing pending-password-change ordering).
- `scripts/reset_unreadable_owner_content.py` is an operator recovery tool for a user whose DEK record is missing while dependent transcript content or encrypted MFA remains. Its default mode is a dry run. Only `--apply` deletes the user's dependent transcript content, clears MFA and recovery-code state, revokes active sessions and trusted devices, forces reenrollment, and provisions a fresh DEK.
- The tool preflights every scoped user before applying any reset. Existing DEKs are unwrapped during preflight, and any Vault availability, authorization, configuration, or unwrap failure aborts before destructive work begins. Users without transcript or encrypted-MFA dependencies are skipped.

The tool is intentionally destructive because ciphertext whose DEK record is missing cannot be restored through this path. It does not guess that a broad Vault failure proves permanent key loss. Limit execution with `--email` where appropriate and verify the dry-run list before applying it.

Each user's database reset is atomic, but a multi-user `--apply` run is not one transaction: an apply-time failure after preflight may leave earlier users completed and later users untouched. Re-run the dry run to establish remaining work.

## Deferred production requirements

Before treating this implementation as production-complete, resolve and deliver:

- a controlled backfill of legacy plaintext TOTP rows and removal of plaintext-read fallback;
- an explicit decision on whether MFA/authentication secrets need a DEK/key-purpose distinct from transcript-derived content;
- Vault Agent/auth-path operation, timeout behavior, availability monitoring, and alerting for MFA verification dependency failures;
- documented KEK/DEK rotation, envelope/key-version history, and migration handling; and
- an approved recovery, incident-response, and destructive-reset runbook.
