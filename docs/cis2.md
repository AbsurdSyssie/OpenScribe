# NHS Care Identity (CIS2)

## Status: implemented locally, not tested with NHS CIS2

The current integration has automated tests with synthetic OIDC metadata, tokens, identities, and endpoints. It has **not** been connected to NHS CIS2 INT, DEP, or production. It has not passed NHS technical conformance or assurance.

Do not describe a deployment as CIS2-compatible yet. A deploying organisation must test its own NHS registration and credentials against CIS2 INT, correct any protocol or claim differences, and complete the required NHS onboarding and assurance.

OpenScribe includes technical support for NHS CIS2 Authentication but does not include access to the CIS2 service or shared NHS credentials. Organisations deploying OpenScribe must complete any applicable NHS England onboarding, assurance, and application registration and configure their own CIS2 credentials.

A CIS2-enabled OpenScribe deployment does not authenticate smartcards or NHS.net accounts directly. NHS CIS2 Authentication handles those authenticators. OpenScribe performs one OpenID Connect login against CIS2.

## Implemented capability

The dedicated provider is `cis2`, shown as **Care Identity**, with this callback:

```text
https://<deployment>/auth/oidc/cis2/callback
```

It reuses OpenScribe's existing authorization-code flow, state and nonce checks, S256 PKCE, signed ID-token validation, configured ACR enforcement, issuer-and-subject identity linking, session rotation, and bounded security logging. It is off by default and makes no CIS2 request while disabled.

The first compatibility slice supports `client_secret_post` only. OpenScribe reads the deployment's client secret from `CIS2_OIDC_CLIENT_SECRET` or the fixed Vault reference `secret:openscribe/oidc/cis2`. The source tree contains no NHS client ID, secret, endpoint, ACR value, test identity, or token.

For local Vault setup, run `.venv/bin/python scripts/set_oidc_client_secret.py cis2`. It prompts twice without echoing the secret and writes only `client_secret` at `openscribe/oidc/cis2`.

OpenScribe uses the signed `iss` and `sub` claims as the external identity. Email is not identity proof, and an `@nhs.net` address does not grant access. CIS2 proves identity; OpenScribe still controls roles, team membership, account status, MFA, onboarding, and content access.

## Deployment responsibility

The organisation responsible for a deployment must arrange an appropriate CIS2 registration with NHS England and supply the resulting credentials and configuration. This includes the approved use case, target environment, exact callback, client ID, client secret, issuer, discovery URL, scopes, signing algorithms, and ACR values.

Register the exact deployed callback. Production configuration requires HTTPS. CIS2 supports `response_mode=query`, so the dedicated provider requires that mode; OpenScribe removes the callback query from the ASGI access-log target before the response starts. Set the issuer, discovery metadata, scopes, algorithms, and ACR values from the deployment's current CIS2 registration and environment metadata; do not copy values from another installation.

NHS onboarding, assurance, technical conformance, penetration testing, production approval, and agreements remain outside OpenScribe runtime configuration. Installing OpenScribe does not grant access to CIS2.

Use the current NHS England guidance for [CIS2 integration](https://digital.nhs.uk/services/care-identity-service/applications-and-services/cis2-authentication), [environment registration](https://digital.nhs.uk/services/care-identity-service/applications-and-services/cis2-authentication/integrate/design-and-build/register-to-access-an-environment), and [assurance](https://digital.nhs.uk/services/care-identity-service/applications-and-services/cis2-authentication/integrate/assurance). Confirm details with NHS England because environment and assurance requirements can change.

## Configuration

Set the `CIS2_OIDC_*` variables listed in [environment.md](environment.md#openid-connect-login). At minimum:

```text
CIS2_OIDC_ENABLED=true
CIS2_OIDC_ISSUER=<registered environment issuer>
CIS2_OIDC_CLIENT_ID=<deployment client ID>
CIS2_OIDC_CLIENT_SECRET=<deployment secret, or use Vault>
CIS2_OIDC_REDIRECT_URI=https://<deployment>/auth/oidc/cis2/callback
CIS2_OIDC_RESPONSE_MODE=query
```

Supply agreed scopes and ACR values explicitly. OpenScribe does not guess AAL2 ACR literals. Required ACR values must be a subset of requested ACR values; a missing or different returned `acr` claim fails authentication.

Users must first link Care Identity from `/workspace/account`. CIS2 login never creates an OpenScribe user, matches an account by email, changes a role, or changes team membership.

## Remaining compatibility and hardening work

An automated wire-level test verifies that `client_secret_post` sends the client ID and secret in the token-request form body and does not send an HTTP Authorization header. A live NHS INT test has not yet verified the deployment's registration, discovery metadata, claims, exact scopes, or ACR semantics. Complete that verification before calling a deployment CIS2-compatible.

### Required before the first NHS INT sign-in

1. Obtain an NHS CIS2 INT registration for the deployment and register the exact HTTPS callback URI.
2. Confirm the registered issuer, discovery URL, client ID, Client Secret authentication method, scopes, allowed signing algorithms, and ACR values with NHS England.
3. Store the issued secret in Vault or inject it through the deployment secret manager.
4. Enable the dedicated `CIS2_OIDC_*` configuration and check that startup validation passes.
5. Confirm that live discovery metadata advertises Authorization Code flow, S256 PKCE, `query` response mode, `client_secret_post`, and an allowed ID-token signing algorithm.

### Required during INT testing

1. Link a synthetic OpenScribe test account to a CIS2 test identity through `/workspace/account`.
2. Verify the authorization request, exact callback, token exchange, issuer, signature, audience, nonce, expiry, and configured ACR against live CIS2 responses.
3. Confirm the live ID token's stable `iss` and `sub` claims and that repeat login resolves the same OpenScribe account without using email.
4. Test smartcard and NHS.net Connect through CIS2 where the registration permits them. OpenScribe must not add authenticator-specific code.
5. Exercise cancellation, unavailable service, bad state, bad nonce, insufficient ACR, unknown signing key, malformed token, and token-endpoint failure paths.
6. Confirm that application, proxy, and platform logs contain no authorization codes, tokens, claims, Care Identity identifiers, or credentials.
7. Record the observed INT metadata and claims as synthetic test fixtures, without copying real identities or tokens into the repository.

### Required before production

1. Resolve every difference found during INT testing and add a focused regression test for each one.
2. Implement cached CIS2 discovery/JWKS handling and refresh once when token verification encounters an unknown signing `kid`.
3. Implement `private_key_jwt`, deployer-owned signing-key storage, public JWKS publication, stable `kid` values, and safe key rollover, unless NHS England approves Client Secret for the production registration.
4. Decide whether the deployment requires CIS2 back-channel logout. If it does, validate logout tokens and revoke only the correct OpenScribe sessions.
5. Complete NHS technical conformance, security and penetration testing, assurance, agreements, and production onboarding for the deployment.
6. Run focused authentication, account-linking, session, logging, configuration, and browser tests in the production-like environment.

OpenScribe does not yet implement:

- `private_key_jwt`, public JWKS publication, or signing-key rollover;
- CIS2 back-channel logout or CIS2-driven session revocation;
- cached issuer JWKS with one refresh-and-retry when a new signing `kid` appears.

NHS recommends Private Key JWT for stronger client authentication and key rollover. OpenScribe fails closed if it is selected now; it never falls back to a client secret. Treat Private Key JWT/JWKS and back-channel logout as explicit later phases with current NHS conformance evidence and focused tests.
