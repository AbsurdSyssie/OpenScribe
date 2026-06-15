# Tool Outputs

Date: 2026-06-14  
Target: `https://openscribe.co.uk`  
Scope: unauthenticated public passive/read-only evidence unless noted.

## Contents

| Path | Tool | Purpose | Sensitivity note |
| --- | --- | --- | --- |
| `passive-http-tls-summary.md` | Python stdlib HTTP/TLS capture | Redacted DNS, TLS, header, metadata-path summary | Cookie values and CSP nonces omitted. |
| `zap/` | OWASP ZAP Docker baseline | Passive baseline report against public unauthenticated routes | Reports contain no session secrets; they include public route names, form field names, CSRF cookie names, and ZAP dummy form value `zaproxy@example.com`. |

## ZAP Command

Executed from repo root with outputs mounted inside this OWASP evidence folder:

```bash
docker run --rm -t \
  -v "/home/oscar/Documents/Code_Projects/OpenScribe/docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap:/zap/wrk:rw" \
  zaproxy/zap-stable \
  zap-baseline.py -t "https://openscribe.co.uk" -m 1 \
  -r zap-baseline.html -J zap-baseline.json -w zap-baseline.md -I
```

ZAP baseline spider submitted discovered public forms with default/dummy values and received `403` on those POSTs. No authenticated crawl, active attack scan, fuzzing, or high-volume test was run.
