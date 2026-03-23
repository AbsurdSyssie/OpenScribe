# Portable Presidio API

This folder is a standalone Presidio-only API bundle for:

- PHI redaction
- PHI indexing with deterministic `[PHI-N]` placeholders
- re-identification from `phi_index`

It does not depend on the rest of this repository once cloned or copied.

## Contents

- `app.py`: FastAPI service
- `presidio_policy.py`: false-positive backoff rules
- `presidio_config.yaml`: custom recognizer config
- `requirements.txt`: Python dependencies
- `setup_portable.sh`: local venv bootstrap
- `start_portable.sh`: API launcher

## Endpoints

- `GET /health`
- `GET /config`
- `POST /redact`
- `POST /unredact`
- `GET /docs`
- `GET /openapi.json`

## Setup

```bash
./setup_portable.sh
```

This creates `.venv`, installs dependencies, and downloads `en_core_web_sm`.

## Run

```bash
./start_portable.sh
```

Override the port:

```bash
PRESIDIO_API_PORT=8011 ./start_portable.sh
```

## Redaction Example

```bash
curl -s -X POST http://127.0.0.1:8010/redact \
  -H "Content-Type: application/json" \
  -d '{"text":"Jonathan Miller was seen on 08/10/2025. NHS: 485 777 3457. Driver licence: AB123456C."}'
```

Example response shape:

```json
{
  "redacted_text": "[PHI-1] was seen on [PHI-2]. NHS: [PHI-3]. Driver licence: [PHI-4].",
  "phi_mapping": {
    "phi-1": {"type": "PERSON", "value": "Jonathan Miller"},
    "phi-2": {"type": "DATE_TIME", "value": "08/10/2025"},
    "phi-3": {"type": "UK_NHS_NUMBER", "value": "485 777 3457"},
    "phi-4": {"type": "UK_DRIVER_LICENSE", "value": "AB123456C"}
  },
  "phi_index": [
    {"index": 1, "type": "PERSON", "value": "Jonathan Miller"},
    {"index": 2, "type": "DATE_TIME", "value": "08/10/2025"},
    {"index": 3, "type": "UK_NHS_NUMBER", "value": "485 777 3457"},
    {"index": 4, "type": "UK_DRIVER_LICENSE", "value": "AB123456C"}
  ],
  "phi_count": 4
}
```

## Re-identification Example

```bash
curl -s -X POST http://127.0.0.1:8010/unredact \
  -H "Content-Type: application/json" \
  -d '{
    "redacted_text":"[PHI-1] was seen on [PHI-2]. NHS: [PHI-3]. Driver licence: [PHI-4].",
    "phi_index":[
      {"index":1,"type":"PERSON","value":"Jonathan Miller"},
      {"index":2,"type":"DATE_TIME","value":"08/10/2025"},
      {"index":3,"type":"UK_NHS_NUMBER","value":"485 777 3457"},
      {"index":4,"type":"UK_DRIVER_LICENSE","value":"AB123456C"}
    ]
  }'
```

## Config

Custom Presidio recognizers live in `presidio_config.yaml`.

Current custom entities:

- `UK_NHS_NUMBER`
- `UK_DRIVER_LICENSE`
- `UK_POSTCODE`
- `STREET_ADDRESS_PHRASE`

The false-positive policy in `presidio_policy.py` suppresses:

- relative/non-identifying `DATE_TIME` phrases such as `today`, `yesterday`, `this morning`
- pain scores like `6/10`
- transcript speaker labels such as `SPEAKER_01`
- conversational address false positives such as `go all the way`
