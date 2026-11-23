# Production security remediation, 22 November 2026

The inspected EC2 deployment uses the Vault root policy, Nginx with a 300 MiB request limit, an overlay-backed container temporary directory, and Uvicorn access logging. Recovery links carry short-lived credentials. The deployment is live and its existing encrypted content must remain readable.

1. Sol owns the Vault boundary. Separate privileged bootstrap from the application process and its mounts. Give the runtime only required operations. Preserve existing volumes, transit key, wrapped keys, and secret references. Document migration, validation, rollback and operator-controlled retirement of privileged credentials.
2. Sol owns recovery transactions. Serialize issue and redemption with credential changes, invalidate outstanding links on email or password changes, and commit session and device revocation with the credential mutation. Preserve ordinary reset MFA and explicit recovery semantics.
3. Sol owns request limits. Enforce streaming limits before multipart spooling for fixed-length and chunked requests while preserving supported file sizes. Bound temporary storage and document Nginx controls including proxy buffering.
4. Luna owns access logging. Remove sensitive request targets and referrers from supported application launchers and document safe ingress logging. Verify with synthetic data only.
5. Sol reviews the integrated implementation, including upgrade and failure paths. Address review findings first. Then write focused regression tests, run existing and new checks in the isolated environment, run the API audit sequentially and validate operational documentation.
6. Report repository verification separately from pending production rollout. Live Vault changes, credential retirement and deployment require a coordinated operator cutover.
