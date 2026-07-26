# OpenScribe documentation

This index distinguishes current operational documentation from design history and dated evidence. When documentation and implementation disagree, use the repository rules in [`AGENTS.md`](../AGENTS.md): migrations and constraints, implemented services and routes, focused tests, and runtime configuration take precedence over prose.

## Current operational references

These documents are maintained as descriptions of implemented behavior:

| Area | Reference |
| --- | --- |
| Local development | [setup.md](setup.md) |
| Persistent single-host Docker runtime | [docker.md](docker.md) |
| Environment variables | [environment.md](environment.md) |
| Authentication and access control | [auth.md](auth.md) |
| Security model | [security.md](security.md) |
| JSON API behavior | [api.md](api.md) |
| Permanent user workspace | [workspace.md](workspace.md) |
| STT provider configuration | [stt-config.md](stt-config.md) |
| LLM provider configuration | [llm-providers.md](llm-providers.md) |
| Transcript capture contracts and remaining roadmap | [transcript-capture.md](transcript-capture.md) |
| Test strategy | [testing.md](testing.md) |
| Database test behavior | [dbtesting.md](dbtesting.md) |
| Gemini Enterprise deployment | [gemini-enterprise-setup.md](gemini-enterprise-setup.md) |

A document in this section can still contain an explicitly labelled compatibility note or remaining-work section. Unlabelled current-behavior claims should match code and tests.

## Roadmaps, plans, and briefs

Files whose names contain `roadmap`, `plan`, `brief`, `todo`, or `design` describe intended direction, a bounded implementation proposal, or historical reasoning. They are not runtime contracts unless a section explicitly says it records implemented behavior. Examples include:

- [frontend-roadmap.md](frontend-roadmap.md)
- [DatabasePlan.md](DatabasePlan.md)
- [feature_todo.md](feature_todo.md)
- [template_io_plan.md](template_io_plan.md)
- [hallucination-check-design.md](hallucination-check-design.md)

When a planned feature becomes active, update the relevant operational reference rather than treating the plan as the only documentation.

## Compliance evidence

Files under [`Compliance/`](Compliance/) and dated `security-evidence/` directories are assessment records captured at a point in time. Preserve them as evidence snapshots. Do not rewrite an old result merely because current code has changed; add a newer assessment or remediation record instead.

## Documentation maintenance

For behavior changes:

1. update the closest operational reference;
2. update the corresponding section in the repository [`README.md`](../README.md) when entry points, setup, or user-facing routes change;
3. mark superseded plans or compatibility behavior clearly;
4. use repository-relative links only;
5. do not copy secrets, transcript-derived content, provider responses, or private deployment paths into documentation.
