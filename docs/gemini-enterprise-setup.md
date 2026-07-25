# Gemini Enterprise setup

OpenScribe uses the native Google Gen AI SDK in Gemini Enterprise mode. It does not use a Gemini API key. Authentication comes from Application Default Credentials (ADC) or a service-account JSON credential uploaded through the system-admin wizard and stored in Vault.

## Google Cloud prerequisites

For the project entered in the wizard:

1. Enable billing.
2. Enable the Agent Platform API service (`aiplatform.googleapis.com`, also shown as the Vertex AI API in some Google Cloud tooling):

   ```bash
   gcloud services enable aiplatform.googleapis.com --project=PROJECT_ID
   ```

3. Grant the runtime identity `roles/aiplatform.user`, or a narrower organization-approved custom role that permits model inference and token counting:

   ```bash
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member='user:YOUR_ACCOUNT@example.com' \
     --role='roles/aiplatform.user'
   ```

   For a service account, use `serviceAccount:SERVICE_ACCOUNT_EMAIL` instead of `user:...`.
4. Confirm the intended model is available in the chosen location. Google model and regional availability changes over time; check Google's current model-lifecycle and location documentation before production rollout.

Project IDs and quota projects are different concepts. OpenScribe addresses model requests to the project entered in the wizard. The local-development commands below set the same project as the ADC quota/billing project. If an organization deliberately uses a different quota project, the authenticated identity needs `serviceusage.services.use` there and `aiplatform.googleapis.com` must also be enabled there; model access IAM still applies to the wizard project.

## Supported location input

Enter the exact Google location identifier, not a shortened AWS-style region:

- `global` — highest availability; no regional processing isolation.
- `eu` — EU jurisdictional multi-region; model support may differ from global.
- `us` — US jurisdictional multi-region.
- `europe-west2` — London/UK regional endpoint. Model and capacity support can be narrower than `global`, `us`, or `eu`.

`eu-west2` is invalid. OpenScribe derives audit metadata as `https://europe-west2-aiplatform.googleapis.com` for `europe-west2`, and `https://aiplatform.googleapis.com` for `global`; this URL is not editable.

OpenScribe discovers publisher models through Google's `v1beta1` Model Garden catalog because stable `v1` has no publisher-model list operation. Token counting and generation remain on stable `v1`. Google's regional catalogs can lag the corresponding jurisdictional catalog, so OpenScribe merges `europe-*` regions with `eu`, and US/North America regions with `us`. It filters out embedding, image, TTS, and live models. Discovery remains advisory: finalization validates the selected model with a fixed synthetic `count_tokens` request. No clinical content is used for connection validation. A catalog failure still permits manual model selection.

For `gemini-3.5-flash`, Google's current model page lists `europe-west2` for model availability and Provisioned Throughput, while Standard PayGo is listed for `global`, `us`, and `eu`. A project without suitable provisioned capacity can therefore receive capacity errors in `europe-west2` even though the model and endpoint exist. Re-check the current model page and the project's Google capacity contract before rollout; do not infer PayGo availability from endpoint availability.

## Bare-metal development with user ADC

Run these commands as the same operating-system user that runs OpenScribe:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
chmod 600 "$HOME/.config/gcloud/application_default_credentials.json"
```

Verify resolution through OpenScribe's virtualenv without printing a token:

```bash
.venv/bin/python - <<'PY'
import google.auth

credentials, project = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
print("ADC resolved:", type(credentials).__name__)
print("Default project:", project or "not set; wizard project is authoritative")
print("Quota project:", credentials.quota_project_id or "not set")
PY
```

Then select Gemini Enterprise in the wizard, enter `PROJECT_ID`, choose a valid location, and select Application Default Credentials. OpenScribe uses the running process's ADC search path; it does not copy the credential into Vault.

User ADC created by `gcloud auth application-default login` is suitable for local development. For a bare-metal production service outside Google Cloud, prefer a dedicated service identity obtained through Workload Identity Federation. If ADC uses an explicit deployment-managed credential configuration file, set its path in the service manager and restrict local access to the OpenScribe service account:

```ini
[Service]
User=openscribe
Environment=GOOGLE_APPLICATION_CREDENTIALS=/etc/openscribe/google/adc.json
```

```bash
sudo chown openscribe:openscribe /etc/openscribe/google/adc.json
sudo chmod 600 /etc/openscribe/google/adc.json
```

Do not place Google credentials in `.env`, command-line arguments, repository files, logs, or world-readable locations.

## Persistent Docker runtime with ADC

The `runtime` Compose profile runs FastAPI and the Celery generation worker in the same `openscribe` container. Both therefore resolve the same mounted ADC file and runtime identity.

For local development only, use the repository's optional Compose override to mount the single ADC JSON file read-only. Do not mount the whole gcloud configuration directory:

```bash
export GOOGLE_ADC_HOST_FILE="$HOME/.config/gcloud/application_default_credentials.json"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  --profile runtime \
  up -d --build
```

The override sets the container path to `/var/run/secrets/google/adc.json` through `GOOGLE_APPLICATION_CREDENTIALS`. `${GOOGLE_ADC_HOST_FILE}` is expanded by Docker Compose on the host. Confirm the source path before starting.

Verify inside the running container without printing credentials or tokens:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  exec openscribe \
  python -c 'import google.auth; c,p=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"]); print(type(c).__name__, p, c.quota_project_id)'
```

The image runs as UID `10001`. The host file must be readable by that UID; do not solve permission failures with `chmod 644`. Use an appropriate ownership/ACL arrangement or a deployment secrets mechanism.

For production containers, do not copy a user ADC refresh-token file into the image. Preferred choices are:

- Google Cloud: attach a least-privilege service account to the workload (Cloud Run, GKE Workload Identity, or the compute platform's service identity).
- Outside Google Cloud: configure Workload Identity Federation and expose its ADC configuration through the deployment secret mechanism.
- Advanced fallback: select Service-account JSON in the OpenScribe wizard; OpenScribe validates it and stores it in Vault. Long-lived keys require rotation and create greater operational risk.

Never bake credentials into an image layer, Compose file, environment-variable value, or Kubernetes ConfigMap. Mount secret material read-only and ensure every web and generation-worker process receives it.

## Network requirements

Bare-metal and containerized web/worker processes need DNS, HTTPS, and trusted CA access to:

- `aiplatform.googleapis.com`
- the chosen regional host, such as `europe-west2-aiplatform.googleapis.com`
- `oauth2.googleapis.com` for user/service-account token exchange when applicable

Attached Google Cloud identities additionally need the runtime's normal route and DNS access to the metadata server (commonly `metadata.google.internal`/link-local HTTP). Do not proxy metadata traffic to the public internet.

If an outbound proxy is required, configure it consistently for web and workers. Do not replace the Gemini base URL with a proxy URL; use standard proxy environment settings and preserve TLS verification.

## Wizard flow

1. Provider: Gemini Enterprise.
2. Project: exact Google Cloud project ID.
3. Location: exact value such as `europe-west2`, `eu`, or `global`.
4. Authentication: Application Default Credentials.
5. Capacity:
   - `Auto` omits the request-type header, allowing Google's default routing behavior.
   - `Shared` forces pay-as-you-go routing.
   - `Dedicated` forces Provisioned Throughput and returns `429` rather than spilling over when purchased capacity is exhausted.

   Use `Auto` unless the Google capacity contract or a deliberate PayGo-only policy requires an override. Location/model consumption support still applies; selecting `Shared` cannot create PayGo availability in a region where Google does not offer it.
6. Check credentials and find models.
7. If discovery is unavailable, enter the exact model ID manually.
8. Finalize. OpenScribe calls `count_tokens` with synthetic text before saving a manual model.

ADC configurations show `Credential source: Runtime identity` and have no Vault secret. Service-account configurations show only non-secret account metadata and `has_secret`; raw JSON is never returned.

## Troubleshooting

### “Default credentials unavailable”

- Run the virtualenv or container verification above in the same runtime identity as FastAPI and Celery.
- Check `GOOGLE_APPLICATION_CREDENTIALS` points to a readable file inside that runtime, not a host-only path.
- For attached identities, verify metadata-server access.

### “Required Google API is not enabled”

Enable `aiplatform.googleapis.com` in the wizard project, then allow IAM/service propagation time.

### “Google IAM denied this operation”

Grant the runtime identity `roles/aiplatform.user` or the approved custom equivalent in the wizard project. Confirm ADC resolves the identity you expect; gcloud CLI login and ADC login are separate.

### “Gemini model discovery is unavailable”

OpenScribe uses the `v1beta1` publisher-model catalog for this step. Check API access and networking to the selected `aiplatform.googleapis.com` endpoint. If discovery remains unavailable, continue with manual model entry; finalization performs the authoritative stable-`v1` `count_tokens` check.

### “Gemini model is unavailable”

- Check exact model ID and lifecycle.
- Check model availability for the chosen location.
- Try `global` only if global processing is acceptable.
- Confirm model-specific access and capacity requirements with the Google account team when the published model page is insufficient.

### “Gemini location is unavailable”

- Check spelling: `europe-west2`, not `eu-west2`.
- Confirm the selected model supports that endpoint.
- Confirm the project has access to the selected location and a supported consumption option.

### `429` or “Gemini capacity is temporarily unavailable”

- Check the selected capacity mode. `Dedicated` requires matching Provisioned Throughput and does not spill over.
- `Shared` requires Standard PayGo support for that model and location.
- For `europe-west2`, check the current model page carefully: endpoint/model availability does not imply Standard PayGo availability.
- Compare with `global`, `us`, or `eu` only when their broader processing boundary is acceptable; do not change location solely to bypass residency policy.

### Setup succeeds but generation fails

- Verify ADC in the Celery worker, not only the web container/process.
- Confirm worker DNS/HTTPS/proxy/CA configuration matches the web process.
- Confirm the saved model and provider remain enabled and allowed for the team.

### “LLM generation returned invalid JSON”

This is a provider-output validation failure rather than an ADC failure. Check the selected model, prompt contract, and provider response logs that are safe to inspect; do not weaken structured-output validation.
