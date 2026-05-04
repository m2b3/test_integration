FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY base.py .

# The script writes its SQLite DB to the path passed via --db.
# Mount the host repo or another writable directory at runtime if you want the
# database and optional .env file to persist outside the container.
ENTRYPOINT ["python", "/app/base.py"]
