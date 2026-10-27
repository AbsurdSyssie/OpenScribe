# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Primary users are clinicians in UK healthcare organisations who capture or upload consultation audio, review the resulting source material, and prepare notes for the clinical record.
- Team leaders manage their own team's members and approved service choices. A team leader may also be a clinician and content owner; the role itself does not grant access to another person's clinical content.
- The administrative audience is technical system administrators who operate an organisation's OpenScribe deployment, teams, accounts, provider connections, limits, and operational metadata.

## Product Purpose

OpenScribe is an open-source clinical scribe that organisations can host for multiple teams. It transcribes supplied consultation information and helps clinicians arrange it into draft notes and follow-ups. Success means clinicians can review, correct, and choose what to transfer into the clinical record; the system does not make that decision for them.

## Operating Context

OpenScribe runs as an organisation-operated web application. Clinicians use the `/workspace` Scribe to capture or upload audio, review transcripts, maintain a Working note, add separate post-consultation dictation, and create editable note or follow-up drafts. Teams use organisation-approved speech and language-model services. Technical system administrators use `/admin` to manage accounts, teams, providers, policy, quotas, and operational information. Team leaders manage only their authorised team scope.

## Capabilities and Constraints

- The Scribe supports whole-file and live audio capture, transcript review, Working notes, post-consultation dictation, Templates, Quick Actions, generated notes and follow-ups, and copying reviewed material for transfer to a clinical record.
- Generated material is a draft. A clinician must review and approve it before it is committed to the clinical record. Copying is not verification.
- OpenScribe does not diagnose, triage, recommend treatment, make clinical decisions, or act autonomously.
- Transcript-derived content belongs to its owning user. System administrators and team leaders can manage authorised metadata and configuration but do not gain access to another user's transcript, Working note, dictation, generated content, prompts, redaction data, or audio. System-administrator accounts cannot own transcripts.
- Designated user content is encrypted at rest within the owner's key boundary. Provider credentials are managed through the established secret-storage boundary and are not exposed in ordinary administration responses.
- Provider selection, quota limits, retention policy, and team roles are server-controlled. Existing asynchronous work keeps its established execution and accounting contracts when administrators change policy.
- The fixed EMIS-aligned structured-note profile is an output shape; OpenScribe does not claim automatic write-back to an EPR.
- Clinical safety status and deployment responsibilities are defined in [SAFETY.md](SAFETY.md). Its DCB0129 safety case is not external certification, and its coverage must not be extended to later releases without the required safety review.

## Brand Commitments

Keep OpenScribe's name, terminology, and future administration work in keeping with the existing site and the user-approved quota redesign. Use plain UK English and the established product terms, including **Scribe**, **Working note**, **Templates**, and **Quick Actions**. No visual world, palette, or typeface is specified here.

## Evidence on Hand

Maintained product and workflow references are indexed in [docs/README.md](docs/README.md). The implementation and user-facing description are in [README.md](README.md), with role and administration boundaries in [docs/admin_brief.md](docs/admin_brief.md) and [docs/admin_workspace_function_map.md](docs/admin_workspace_function_map.md), Scribe behavior in [docs/transcribe_brief.md](docs/transcribe_brief.md) and [docs/workspace.md](docs/workspace.md), and clinical safety in [SAFETY.md](SAFETY.md). No customer testimonials, outcome benchmarks, or independent product-certification claims are recorded in these sources.

## Product Principles

- Turn supplied information into reviewable drafts; leave clinical judgement and approval with the clinician.
- Keep clinical source material owner-only, encrypted, and separate from administrative metadata.
- Give each role only its established team or deployment responsibilities.
- Make provider, quota, retention, and operational behavior explicit without exposing clinical content or secrets.

## Accessibility & Inclusion

Target WCAG 2.2 AA for the web interface. Dated accessibility reviews and verification are listed in [docs/README.md](docs/README.md); automated results are not a certification, and remaining human checks must stay visible as follow-up work.
