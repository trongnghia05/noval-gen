FROM python:3.12-slim

WORKDIR /app

# fonts-dejavu-core: scalable TTF for Pillow title overlay on generated cover art
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite file lives here — mount a volume on this path to persist data
# across container restarts (see docker-compose.yml).
ENV DATABASE_URL=sqlite:////data/novelgen.db
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
