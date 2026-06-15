# 07 - SBOM / Dependency Vulnerability Scan

Date: 2026-06-14
Tool: `pip-audit` (PyPA advisory database)
Target: `.venv` installed packages + `requirements.txt`

## Summary

| Scope | Packages scanned | Vulnerabilities found |
| --- | --- | --- |
| `requirements.txt` (pinned) | 27 | 11 vulns in 4 packages |
| `.venv` (installed + transitive) | all | 24 vulns in 10 packages |
| Skipped | `en-core-web-sm 3.8.0` | Not on PyPI (spaCy model) |

## Vulnerability Register

### Runtime — Actionable

| Package | Installed | Fix | Vulns | Severity | Impact |
| --- | --- | --- | --- | --- | --- |
| `cryptography` | 46.0.3 | 46.0.7 | PYSEC-2026-35, PYSEC-2026-36, CVE-2026-26007 | Medium | DNS constraint bypass, buffer overflow (non-contiguous buffer), EC subgroup validation (SECT curves only) |
| `starlette` | 0.38.6 | 0.47.2+ (CVE-2025-54121), 1.0.1 (PYSEC-2026-161) | PYSEC-2026-161, CVE-2024-47874, CVE-2025-54121 | Medium | Host header injection bypassing `request.url`-based path checks; unlimited form field buffering DoS; main thread block on large file upload |
| `python-multipart` | 0.0.22 | 0.0.27 | CVE-2026-40347, CVE-2026-42561 | Medium | Multipart preamble/epilogue DoS; unlimited multipart header count/size DoS |
| `idna` | 3.11 | 3.15 | CVE-2026-45409 | Low | ReDoS on crafted long inputs to `idna.encode()` |
| `urllib3` | 2.6.3 | 2.7.0 | PYSEC-2026-142, PYSEC-2026-141 | Low | Decompression bomb via streaming API; cross-origin redirect header leak |
| `requests` | 2.32.5 | 2.33.0 | CVE-2026-25645 | Low | `extract_zipped_paths()` predictable temp filename (only affects direct callers; OpenScribe does not use this function) |

### Runtime — Not Applicable / Accepted

| Package | Installed | Vulns | Rationale |
| --- | --- | --- | --- |
| `mako` | 1.3.10 | CVE-2026-44307 | Windows-only path traversal; OpenScribe runs on Linux |
| `pygments` | 2.19.2 | CVE-2026-4539 | ReDoS in archetype lexer, local access only |

### Build/Dev Only

| Package | Installed | Vulns | Rationale |
| --- | --- | --- | --- |
| `pip` | 24.0 | PYSEC-2026-196, CVE-2025-8869, CVE-2026-1703, CVE-2026-3219, CVE-2026-6357 | Build-time vulnerabilities; not runtime |
| `pytest` | 8.3.3 | CVE-2025-71176 | Temp directory privilege issue; dev/test only |

## Static Vendor Assets

Vendored JS/WASM/ONNX assets (no package manager):

| Asset | Version | Notes |
| --- | --- | --- |
| `lucide` (icons) | 1.8.0 | CDN-free, vendored locally |
| `sortable` (drag-drop) | unversioned | Local vendor; check for known vulns |
| `vad-web` (VAD) | 0.0.29 | ONNX runtime; vendored locally |
| `onnxruntime-web` | 1.22.0 | WASM/JS runtime; vendored locally |

No `package.json` or npm lockfile exists. Static assets reviewed manually for known CVEs — none found at current versions via NVD/OSV quick check. ONNX runtime model (`silero_vad_v5.onnx`) is a binary model file, not executable code.

## Infrastructure Images (docker-compose.yml)

| Image | Version | Notes |
| --- | --- | --- |
| `postgres` | 16 | Docker Hub official; regularly update |
| `redis` | 7 | Docker Hub official; regularly update |
| `hashicorp/vault` | 1.17 | Docker Hub; regularly update |

No Dockerfile found. Vulnerability management for these images is an operational concern (image scanning in CI, regular pulls).

## Remediation Plan

1. **Immediate**: Upgrade `cryptography` to >=46.0.7; `python-multipart` to >=0.0.27; `idna` to >=3.15.
2. **Near-term**: Upgrade `starlette` when FastAPI compatibility allows (starlette update may require FastAPI upgrade); pin `urllib3` >=2.7.0.
3. **Accepted**: `mako` (Windows-only), `pygments` (local ReDoS), `requests` (unused function), `pip`/`pytest` (build/dev only).
4. **Operational**: Add container image scanning to CI pipeline for `postgres:16`, `redis:7`, `hashicorp/vault:1.17`.

## Redaction Note

No cookies, tokens, account data, transcript/note content, prompts, provider responses, or audio committed. Tool output records package names and CVE IDs only.
