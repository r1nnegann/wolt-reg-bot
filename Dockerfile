# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SMS_BOT_DB=/app/data/bot.db \
    HERO_WEBHOOK_HOST=0.0.0.0

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && mkdir -p /app/data

COPY bot.py .

CMD ["python", "bot.py"]
