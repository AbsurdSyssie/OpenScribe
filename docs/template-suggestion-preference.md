# Template-suggestion preference

Status: current behavior.

## Purpose

The Scribe workspace can suggest one of the user's available templates after a consultation has enough transcript text. The user controls this in **Workspace → Preferences** with **Suggest a template based on the consultation**. The information icon says: “AI will suggest which of your templates matches the consultation.”

The setting is on by default. A missing preference value means on; the database stores only an explicit `false` opt-out. The control saves when it changes and does not show a confirmation message. It remains available even when the user has too few templates or no eligible LLM provider.

## Effect on work

When the setting is on, the normal Scribe workflow may claim its one template-suggestion job for an eligible transcript. The browser includes the currently selected template ID in the queue request. The server validates it against the owner's available templates and snapshots its ID, name, and description with the job. The worker sends that snapshot to the LLM as `current_template`; the LLM returns no suggestion when that template is already a good fit. When the setting is off, the browser and API do not create a job, and the worker checks the setting again before redaction or provider dispatch.

Turning the setting off cancels the user's queued suggestion jobs in the same database transaction as the preference change. It cancels the pending outbox dispatch and releases any reserved provider quota. A broker message already published may still reach a worker, but the terminal job makes it a no-op. The service cannot recall a request that a provider has already received.

A suggestion already shown in the browser remains visible. A completed suggestion that has not appeared is suppressed while the setting is off. Re-enabling does not create another job for a transcript whose job was cancelled; the setting applies to the next consultation.

## Privacy and logging

The preference is owner-scoped. The existing suggestion flow stores transcript snapshots encrypted for the owner and sends only the established redacted source to an eligible provider. Lifecycle logs contain identifiers, states, counts, and reason codes only. They do not contain transcript text, prompts, provider responses, PII, or credentials.
