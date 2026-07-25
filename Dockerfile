FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m spacy download en_core_web_sm

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY docker ./docker

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin openscribe \
    && mkdir -p /app/.local/vault \
    && chown -R openscribe:openscribe /app \
    && chmod +x /app/docker/entrypoint.sh

USER openscribe

EXPOSE 8080

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["runtime"]
