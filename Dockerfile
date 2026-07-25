FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN python -m venv /opt/venv \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && python -m spacy download en_core_web_sm

COPY . .
COPY docker/entrypoint.sh /usr/local/bin/openscribe-entrypoint
RUN chmod +x /usr/local/bin/openscribe-entrypoint \
    && useradd --create-home --uid 10001 openscribe \
    && mkdir -p /app/.local/vault /app/.runtime \
    && chown -R openscribe:openscribe /app /opt/venv

USER openscribe

EXPOSE 8080
ENTRYPOINT ["openscribe-entrypoint"]
CMD ["web"]
