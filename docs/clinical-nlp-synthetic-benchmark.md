# Clinical NLP Synthetic Benchmark

## Purpose

Synthetic-only timing notes for the local OpenMed clinical NLP endpoint at `http://localhost:8090/analyze`.

No transcript-derived content was used.

## Synthetic Payload

The benchmark text repeated this sentence to simulate a long consultation without PHI:

```text
Patient has asthma and diabetes. No chest pain today.
```

Useful sizes:

- short smoke test: 1 sentence, about 53 characters
- medium repeated test: 50 sentences, about 2.7k characters
- long repeated test: 450 sentences, about 24k characters

## Observed Behavior

- `GET /analyze` returned `405`, confirming the route is POST-only.
- Short POST to `/analyze` returned `200` in about `0.7s`.
- Short POST to `/pii/extract` returned `200` in about `0.05s`.
- Long POST to `/analyze` with default `sentence_detection=true` exceeded `40s`.
- The app timeout for generic REST detection is `20s`, so long default `/analyze` calls fail before note generation can move on.
- Setting `sentence_detection=false` made a 24k-character synthetic request complete in about `19.5s`, but produced a large duplicate-heavy response.
- A `~11.9k` character synthetic chunk with `sentence_detection=false` returned `200` in about `9.9s`.

## Implemented Technique

- Split clinical NLP text into chunks capped at `12,000` characters.
- Prefer sentence boundaries, then whitespace, preserving each chunk offset.
- Run generic REST clinical detection per chunk.
- Add returned entity offsets back to original transcript positions.
- Resolve overlaps across the combined span list before storing encrypted clinical entities.
- For local `/analyze` clinical providers, send `sentence_detection=false` unless the provider config already sets that key.

## Rationale

Chunking keeps each provider call under the fixed timeout and avoids sending one very large payload to a service that appears serialized or expensive on long sentence-detection runs. Offset correction keeps the persisted entity values aligned with the original source text while preserving existing owner encryption and clinical run scoping.
