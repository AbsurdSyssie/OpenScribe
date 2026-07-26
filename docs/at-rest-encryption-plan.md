# At-Rest Encryption Rollout Plan

## Status

**Historical implementation plan.** The central rollout described here has been implemented and this file is retained to record the design direction and phased reasoning. It is not the current runtime contract.

Current behavior is documented in:

- [security.md](security.md)
- [mfa-secret-encryption.md](mfa-secret-encryption.md)
- [transcript-capture.md](transcript-capture.md)
- [app/services/content_crypto.py](../app/services/content_crypto.py)
- [app/services/vault.py](../app/services/vault.py)

Implemented state includes per-user DEKs, Vault Transit wrapping, versioned AES-GCM content envelopes, owner-bound associated data, encryption/decryption service boundaries, and ciphertext persistence for designated transcript-derived and authentication fields. The earlier statements that `*_encrypted` columns contained plaintext and that `user_encryption_keys` was not implemented are obsolete.

The repository-relative references below identify the implementation areas that fulfilled the plan:

- [app/services/transcripts.py](../app/services/transcripts.py)
- [app/services/templates.py](../app/services/templates.py)
- [app/services/redaction.py](../app/services/redaction.py)
- [app/services/vault.py](../app/services/vault.py)
- [app/services/content_crypto.py](../app/services/content_crypto.py)
- [AGENTS.md](../AGENTS.md)
- [DatabasePlan.md](DatabasePlan.md)
- [goals.md](goals.md)

## Original objective

The rollout was intended to provide application-layer encryption for transcript-derived content using a DEK/KEK model that:

- preserves owner-only content access;
- keeps password reset independent from content encryption;
- avoids raw DEK storage in PostgreSQL;
- uses Vault as the KEK boundary;
- preserves hard-delete and retention behavior;
- does not create an administrative content-recovery path.

## Design that was adopted

- Each user has wrapped content-key metadata in PostgreSQL.
- Vault Transit owns the KEK and wraps/unwraps user DEKs.
- Content is encrypted with AES-GCM before persistence.
- Envelopes are versioned and bound to their logical storage location and owner through associated data.
- Decryption occurs in backend service code after owner authorization and retention checks.
- Worker tasks resolve the owner from durable database rows rather than a browser session.
- Password and account recovery do not rotate or delete the content DEK.
- User/content deletion retains the existing relational root and durable Vault cleanup behavior.

## Original rollout phases

The implementation was planned in these broad phases:

1. introduce user key metadata and Vault Transit helpers;
2. add the content-crypto service and transcript-root encryption;
3. encrypt transcript-derived children such as generated documents and redaction artifacts;
4. handle existing plaintext data through bounded migration/backfill tooling;
5. add key rotation, recovery, audit, and operational hardening.

The current code and migrations, rather than this phase list, are authoritative for what is now implemented.

## Invariants preserved from the plan

- User passwords are authentication material, not encryption keys.
- Leaders and system administrators do not gain transcript readability.
- Plaintext and unwrapped DEKs are not logged.
- Encryption errors fail closed rather than persisting plaintext.
- Queue payloads do not carry transcript-derived content or raw credentials.
- PostgreSQL and Vault data must be backed up and restored as one recoverable deployment set.
- Deletion and retention remain rooted in the transcript/user lifecycle rather than external ciphertext blobs.

## Remaining operational work

Future work should be recorded in a focused issue or new plan with explicit current-state references. Possible areas include key-rotation operations, production restore drills, removal of any remaining legacy plaintext TOTP compatibility, and newer encryption-algorithm/version migrations. Do not reopen the obsolete premise that encryption has not started.
