FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY base.py arxiv.py biorxiv.py medrxiv.py All_embedding.py ./

# All_embedding.py writes SQLite, FAISS, metadata, and manifest artifacts to the
# paths passed via CLI options. Mount a writable host directory at runtime if
# you want those artifacts and an optional .env file to persist.
ENTRYPOINT ["python", "/app/All_embedding.py"]
