FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# postgresql-client provides pg_isready for the entrypoint wait-loop.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

COPY migrations ./migrations
COPY alembic.ini ./
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run as non-root; /data holds the persisted Telethon session.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/session \
    && chown -R app:app /data /app
USER app

ENTRYPOINT ["/entrypoint.sh"]
CMD ["tgdigest", "run"]
