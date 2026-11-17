FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config

USER appuser
EXPOSE 8000

CMD ["uvicorn", "erp.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
