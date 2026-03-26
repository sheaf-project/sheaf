FROM python:3.12-slim AS base

WORKDIR /app

RUN addgroup --system sheaf && adduser --system --ingroup sheaf sheaf

# Copy source first — hatchling needs the package dir to build
COPY pyproject.toml .
COPY sheaf/ sheaf/
COPY alembic.ini .
COPY alembic/ alembic/

RUN pip install --no-cache-dir ".[s3]"

RUN mkdir -p /app/data && chown sheaf:sheaf /app/data

USER sheaf

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn sheaf.main:app --host 0.0.0.0 --port 8000"]
