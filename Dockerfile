FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY bot ./bot
COPY config ./config
COPY seed ./seed
COPY migrations ./migrations

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app:app /app/data
USER app

# Спершу застосовуємо міграції, потім стартуємо бота
CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
