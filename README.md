# Scicommons Backend

Backend branch for the Scicommons app.

Current deployment shape:

```text
React frontend       -> http://134.87.8.193:5173
FastAPI backend      -> this repo/server, port 8000
Postgres user DB     -> this repo/server, port 5432
GPU article service  -> http://134.87.9.167:8100
```

The backend owns user/session/profile state in Postgres. The GPU server owns
article fetching, embedding, FAISS, SQLite article artifacts, and the
article/search API. The backend talks to the GPU article service through
`ARTICLE_SERVICE_BASE_URL`.

## Repository Layout

```text
backend/             FastAPI user/session/profile API and article-service proxy
docker-compose.yml   Local Postgres user database
setup.sh             Install backend dependencies and initialize backend env
run.sh               Run Postgres and the backend API
```

The article database is intentionally separate from the user database. User
state lives in Postgres. Article records and search indexes live on the GPU
server.

## Quick Start

From the repo root:

```bash
./setup.sh
./run.sh
```

Defaults:

```text
Backend:             http://localhost:8000
Frontend origin:     http://134.87.8.193:5173
GPU article service: http://134.87.9.167:8100
Postgres:            localhost:5432
```

For first-time prototype setup, `RESET_USER_DB=1` is the default and runs
`backend/setup_database.py`, which drops and recreates the user-side tables.
For a deployed database with real users, use:

```bash
RESET_USER_DB=0 ./setup.sh
```

## Environment

`setup.sh` writes `.env` and `backend/.env`. Useful overrides:

```bash
ARTICLE_SERVICE_BASE_URL=http://134.87.9.167:8100 ./setup.sh
CORS_ORIGINS=http://134.87.8.193:5173 ./setup.sh
SESSION_COOKIE_SECURE=true ./setup.sh
RESET_USER_DB=0 ./setup.sh
OVERWRITE_ENV=1 ./setup.sh
```

Important variables:

```text
DATABASE_URL               Postgres connection string
ARTICLE_SERVICE_BASE_URL   GPU article/search API base URL
CORS_ORIGINS               Comma-separated browser origins allowed by backend
SESSION_COOKIE_SECURE      Set true when serving backend over HTTPS
INTERNAL_API_TOKEN         Shared secret for internal feed refresh endpoint
```

If `INTERNAL_API_TOKEN` is not provided, `setup.sh` generates one and writes it
to `.env` and `backend/.env`.

## Daily Feed Refresh

The backend stores per-user daily feeds in `user_daily_feed` as ranked
`article_key` rows. After the GPU server finishes `pipeline.py` and the article
service has the fresh artifacts, it should call the backend refresh endpoint:

```bash
curl -X POST http://134.87.8.193:8000/internal/feed-refresh \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: ${INTERNAL_API_TOKEN}" \
  -d '{"force": true}'
```

Optional body fields:

```json
{
  "feed_date": "2026-07-23",
  "user_ids": ["user-1", "user-2"],
  "force": true
}
```

If a user changes interests or authors, the backend invalidates that user's
cached feed and regenerates it on the next `GET /users/{user_id}/feed` request.

## Run Script

`run.sh` starts:

- Postgres through Docker Compose, unless `START_DB=0`
- FastAPI backend on port `8000`

Useful overrides:

```bash
START_DB=0 ./run.sh
KILL_PORTS=0 ./run.sh
BACKEND_PORT=8001 ./run.sh
ARTICLE_SERVICE_BASE_URL=http://134.87.9.167:8100 ./run.sh
```

Verify from the backend server:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/tags
curl http://localhost:8000/sources
```

Verify externally:

```bash
curl http://134.87.8.193:8000/health
```

If local curl works but external curl hangs, open inbound TCP port `8000` in
the Arbutus security/firewall settings.

## Docker Notes

The scripts default to `sudo docker compose`, matching the Arbutus server setup.
If your Docker user does not need sudo, use:

```bash
DOCKER_COMPOSE="docker compose" ./setup.sh
DOCKER_COMPOSE="docker compose" ./run.sh
```
