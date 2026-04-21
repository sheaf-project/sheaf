FROM python:3.12-slim AS base

WORKDIR /app

RUN addgroup --system sheaf && adduser --system --ingroup sheaf sheaf

# Copy source first — hatchling needs the package dir to build
COPY pyproject.toml .
COPY sheaf/ sheaf/
COPY alembic.ini .
COPY alembic/ alembic/

RUN pip install --no-cache-dir ".[s3,smtp]"

# Dev-only tools (destructive jobs for demo instances, etc.)
# Only installed when INCLUDE_DEV_TOOLS=true. Default: not installed,
# so production images physically cannot contain this code.
ARG INCLUDE_DEV_TOOLS=false
COPY sheaf_dev/ sheaf_dev/
RUN if [ "$INCLUDE_DEV_TOOLS" = "true" ]; then \
      pip install --no-cache-dir ./sheaf_dev; \
    fi && rm -rf sheaf_dev/

RUN mkdir -p /app/data && chown sheaf:sheaf /app/data

USER sheaf

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn sheaf.main:app --host 0.0.0.0 --port ${SHEAF_PORT:-8000}"]
