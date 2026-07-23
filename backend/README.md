# Scicommons Backend

FastAPI backend for Scicommons user/session/profile APIs and article-service proxying.

This backend branch expects:

```text
Frontend:            http://134.87.8.193:5173
GPU article service: http://134.87.9.167:8100
```

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
DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons \
ARTICLE_SERVICE_BASE_URL=http://134.87.9.167:8100 \
CORS_ORIGINS=http://134.87.8.193:5173 \
uvicorn app.main:app --reload --port 8000
```

`ARTICLE_SERVICE_BASE_URL` should point to the article/search API in the
GPU-hosted `scicomm_embedding` service.

## Endpoints

- `GET /health`
- `GET /tags`
- `GET /sources`
- `GET /articles?semantic_query=biology&source=all`
- `GET /users/{user_id}/feed`
- `GET /users/{user_id}/tags`
- `GET /users/{user_id}/profile`
- `PUT /users/{user_id}/profile`
- `GET /users/{user_id}/recently-viewed`
- `POST /users/{user_id}/recently-viewed`
- `POST /login`
- `GET /me`
- `POST /logout`
- `PUT /users/{user_id}/tags`
- `POST /internal/feed-refresh`

`POST /login` creates a row in `user_sessions` and sets an HTTP-only
`scicommons_session` cookie. User-specific endpoints require that session cookie
to match the requested user.

Existing-account login requires both username and email to match. Unknown
emails are only created when the request includes `create_account: true`.

`POST /internal/feed-refresh` is for the GPU server to call after `pipeline.py`
finishes and the article service has fresh artifacts. It requires
`X-Internal-Token` to match `INTERNAL_API_TOKEN`.
