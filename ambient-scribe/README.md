# Ambient Scribe MVP bootstrap

## Start infra

```bash
docker compose up -d
```

## Create venv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run app

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
fastapi dev app/main.py --host "$APP_HOST" --port "$APP_PORT"
```

## Open API docs

Go to:

`http://127.0.0.1:8000/docs`

## First things to test

- Create a team
- Create a user in that team
- Create a transcript
- Commit a transcript version
- List that user's transcripts

## Test suite

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
pytest
```

## Bootstrap checklist

- [ ] Start Postgres, Redis, Vault with Docker Compose
- [ ] Create Python venv and install requirements
- [ ] Run FastAPI locally
- [ ] Verify `/health`
- [ ] Create a team via `/docs`
- [ ] Create a user via `/docs`
- [ ] Create a transcript via `/docs`
- [ ] Commit a transcript version via `/docs`
- [ ] Confirm transcript list returns only that user's transcripts
- [ ] Commit the bootstrap files to git
