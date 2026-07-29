# OpenScribe documentation

This index distinguishes maintained current references from implemented design history, future roadmaps, and dated evidence. When documentation and implementation disagree, use the repository rules in [`AGENTS.md`](../AGENTS.md): migrations/constraints, implemented services/routes, focused tests, and runtime configuration take precedence over prose.

## Maintained current references

These documents are maintained as descriptions of implemented behavior or an explicitly identified compatibility boundary.

### Setup, operations, and engineering

| Area | Reference |
| --- | --- |
| Local development | [setup.md](setup.md) |
| Local evaluator demo | [local-demo.md](local-demo.md) |
| Persistent single-host Docker runtime | [docker.md](docker.md) |
| Environment variables | [environment.md](environment.md) |
| Test strategy | [testing.md](testing.md) |
| Database test behavior | [dbtesting.md](dbtesting.md) |
| Persistence architecture | [DatabasePlan.md](DatabasePlan.md) |
| Frontend styling architecture | [styling_condensation_plan.md](styling_condensation_plan.md) |
| Current focused backlog | [feature_todo.md](feature_todo.md) |
| Contributor rules | [`AGENTS.md`](../AGENTS.md) and [`brief.md`](../brief.md) |

### Security, identity, and API

| Area | Reference |
| --- | --- |
| Authentication and access control | [auth.md](auth.md) |
| Account recovery | [account_recovery_brief.md](account_recovery_brief.md) |
| Security model | [security.md](security.md) |
| XSS probe and focused checks | [security-xss.md](security-xss.md) |
| MFA secret encryption and compatibility | [mfa-secret-encryption.md](mfa-secret-encryption.md) |
| JSON API behavior | [api.md](api.md) |
| DEK/KEK architecture and production hardening | [dek-kek-production-plan.md](dek-kek-production-plan.md) |

### User workspace and content workflows

| Area | Reference |
| --- | --- |
| Permanent user workspace | [workspace.md](workspace.md) |
| User-workspace migration history and legacy `/home` redirects | [home_brief.md](home_brief.md) |
| Scribe workspace summary | [transcribe_brief.md](transcribe_brief.md) |
| Transcript capture contracts and remaining roadmap | [transcript-capture.md](transcript-capture.md) |
| Live chunked STT behavior | [live_stt.md](live_stt.md) |
| Working note implementation contract/history | [working_note_implementation.md](working_note_implementation.md) |
| Structured EMIS notes | [emis-roadmap.md](emis-roadmap.md) |
| Smart Phrase editor behavior | [editor-smart-phrases.md](editor-smart-phrases.md) |
| Library import/export | [template_io_plan.md](template_io_plan.md) |
| Hallucination check | [hallucination-check-design.md](hallucination-check-design.md) |
| Scribe browser-test roadmap | [transcribe-playwright-checklist.md](transcribe-playwright-checklist.md) |

### Provider and administration

| Area | Reference |
| --- | --- |
| STT provider configuration | [stt-config.md](stt-config.md) |
| LLM provider configuration | [llm-providers.md](llm-providers.md) |
| Gemini Enterprise deployment | [gemini-enterprise-setup.md](gemini-enterprise-setup.md) |
| Provider credential lifecycle design history/current rules | [provider-credential-combined-flow-plan.md](provider-credential-combined-flow-plan.md) |
| Canonical admin workspace map | [admin_workspace_function_map.md](admin_workspace_function_map.md) |
| System-administrator brief | [admin_brief.md](admin_brief.md) |
| Admin Usage reporting | [usage_tab.md](usage_tab.md) |
| Role-based product tutorials | [tutorials/README.md](tutorials/README.md) |

A maintained document can contain an explicitly labelled compatibility note, implemented design history, or remaining-work section. Unlabelled current-behavior claims should match code, tests, migrations, and configuration.

## Historical design records

These files are retained to explain original sequencing or design reasoning and are not active backlog/schema/runtime contracts:

- [goals.md](goals.md) — original project phases;
- [dbroadmap.md](dbroadmap.md) — early vertical-slice roadmap;
- [at-rest-encryption-plan.md](at-rest-encryption-plan.md) — implemented encryption rollout history.

Other files whose names contain `roadmap`, `plan`, `brief`, `todo`, or `design` must state their status at the top. The filename alone no longer determines whether the file is current; use this index and its status section.

## Compliance evidence

Files under [`Compliance/`](Compliance/) and dated `security-evidence/` directories are assessment records captured at a point in time. Preserve them as evidence snapshots. Do not rewrite an old result merely because current code changed; add a newer assessment/remediation record with the commit/environment identified.

## Documentation maintenance

For behavior changes:

1. update the closest maintained current reference;
2. update the corresponding section in repository [`README.md`](../README.md) when entry points, setup, deployment, or user-facing routes change;
3. mark compatibility, implemented history, and remaining work explicitly;
4. use repository-relative links only;
5. update `app/api_route_audit.py` when `/api/v1` routes change;
6. update `.env.example`, Compose mapping, and [environment.md](environment.md) together for runtime setting changes;
7. do not copy secrets, transcript-derived content, provider responses, or private deployment paths into documentation;
8. run `python .github/scripts/check-operational-docs.py`.
