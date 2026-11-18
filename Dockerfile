FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY finance_bot ./finance_bot

RUN useradd --create-home --uid 1000 bot
USER bot

# Миграции Alembic применяются автоматически при старте бота
CMD ["python", "-m", "finance_bot"]
