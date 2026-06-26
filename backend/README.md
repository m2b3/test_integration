# Scicommons Backend

Prototype FastAPI backend for the Scicommons frontend.

## Local Setup

Start local Postgres from the repo root:

```bash
docker compose up -d db
```

Seed the database from this folder:

```bash
DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons python3 setup_database.py
```

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the API:

```bash
DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons uvicorn app.main:app --reload --port 8000
```

## Endpoints

- `GET /health`
- `GET /tags`
- `GET /articles?tags=biology,chemistry&match=or&source=all`
- `GET /users/{user_id}/feed`
- `GET /users/{user_id}/tags`
- `POST /login`
- `PUT /users/{user_id}/tags`
