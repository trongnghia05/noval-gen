FROM python:3.12-slim

WORKDIR /app

# fonts-dejavu-core: scalable TTF for Pillow title overlay on generated cover art
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

# DATABASE_URL is provided by docker-compose (Postgres). The SQLite fallback in
# app/config.py only applies to bare `uvicorn` runs outside compose.
# /data still holds the CSV knowledge graphs (see docker-compose.yml).
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
