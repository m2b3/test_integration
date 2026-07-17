# Scicommons Integration Prototype

Monorepo prototype for the Scicommons web app.

Current deployment shape:

```text
React frontend -> FastAPI backend -> Postgres user database
                         |
                         v
              Article/search service -> SQLite + FAISS article artifacts
```

For the prototype, all services can run on one Arbutus server.

## Repository Layout

```text
backend/             FastAPI user/session/profile API and article-service proxy
frontend/            React/Vite frontend
scicomm_embedding/   Article fetching, indexing, semantic/keyword search API
igather2/            PubMed ingestion support and bundled EDirect tools
docker-compose.yml   Local Postgres user database
setup.sh             Install dependencies and initialize local configuration
run.sh               Run Postgres, article service, backend, and frontend
```

The article database is intentionally separate from the user database. User
state lives in Postgres. Article records and search indexes live in
`scicomm_embedding` artifacts such as `all.sqlite`, `all_specter.index`,
`all_metadata.json`, and `all_manifest.json`.

## Server Setup

SSH to the Arbutus server at `134.87.8.193`.

Clone the repo:

```bash
git clone git@github.com:m2b3/test_integration.git
cd test_integration
```

If GitHub SSH is not set up on the server, add a server SSH key to GitHub first.

## Quick Start

Run setup from the repo root:

```bash
./setup.sh
```

On the Arbutus server, set the browser-facing backend URL before setup so the
frontend `.env` points at the reachable backend:

```bash
VITE_API_BASE_URL=http://134.87.8.193:8000 ./setup.sh
```

Then run everything:

```bash
./run.sh
```

Open:

```text
http://134.87.8.193:5173
```

Local defaults:

```text
Frontend:        http://localhost:5173
Backend:         http://localhost:8000
Article service: http://localhost:8100
Postgres:        localhost:5432
```

## Setup Script

`setup.sh` does the following:

- creates Python virtual environments for `backend`, `scicomm_embedding`, and `igather2`
- installs backend, article pipeline/search, and PubMed ingestion dependencies
- runs `npm install` in `frontend`
- writes `frontend/.env` if one does not already exist
- starts Postgres through Docker Compose
- recreates and seeds the user database

Useful environment overrides:

```bash
VITE_API_BASE_URL=http://134.87.8.193:8000 ./setup.sh
OVERWRITE_FRONTEND_ENV=1 VITE_API_BASE_URL=http://134.87.8.193:8000 ./setup.sh
DOCKER_COMPOSE="sudo docker compose" ./setup.sh
RESET_USER_DB=0 ./setup.sh
RUN_PIPELINE=1 ./setup.sh
```

`RESET_USER_DB=1` is the default and runs `backend/setup_database.py`, which
drops and recreates the prototype user-side tables. `RUN_PIPELINE=0` is the
default because the article pipeline performs network fetching and embedding
work.

## Article Pipeline

Run the article pipeline when you need fresh article artifacts:

```bash
cd scicomm_embedding
source .venv/bin/activate
python pipeline.py
```

The article service expects these artifacts in `scicomm_embedding/` by default:

```text
all.sqlite
all_specter.index
all_metadata.json
all_manifest.json
```

If artifacts live elsewhere, point `run.sh` at them:

```bash
SCICOMM_ARTIFACT_DIR=/path/to/artifacts ./run.sh
```

## Run Script

`run.sh` starts:

- Postgres via Docker Compose
- article/search API on port `8100`
- backend API on port `8000`
- frontend dev server on port `5173`

Useful environment overrides:

```bash
DOCKER_COMPOSE="sudo docker compose" ./run.sh
START_DB=0 ./run.sh
BACKEND_PORT=8001 ARTICLE_PORT=8101 FRONTEND_PORT=5174 ./run.sh
VITE_API_BASE_URL=http://134.87.8.193:8000 ./run.sh
```

Verify from the server:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/tags
curl http://localhost:8100/health
```

Verify externally:

```bash
curl http://134.87.8.193:8000/health
```

If local curl works but external curl hangs, open inbound TCP ports `8000` and
`5173` in the Arbutus security/firewall settings.

## Docker Notes

If Docker requires sudo every time, either use:

```bash
DOCKER_COMPOSE="sudo docker compose" ./setup.sh
DOCKER_COMPOSE="sudo docker compose" ./run.sh
```

or add the user to the Docker group and reconnect:

```bash
sudo usermod -aG docker $USER
```

## Manual Commands

Backend:

```bash
cd backend
source .venv/bin/activate
DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons \
ARTICLE_SERVICE_BASE_URL=http://localhost:8100 \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Article service:

```bash
cd scicomm_embedding
source .venv/bin/activate
uvicorn article_service.main:app --host 0.0.0.0 --port 8100
```

Frontend:

```bash
cd frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

## Running With tmux

Use tmux if you want services to keep running after logout:

```bash
tmux new -s scicommons
./run.sh
```

Detach from tmux:

```text
Ctrl+b, then d
```

Reconnect later:

```bash
tmux attach -t scicommons
```

Useful tmux commands:

```bash
tmux ls
tmux kill-session -t scicommons
```

## Main API Endpoints

```text
GET  /health
GET  /tags
GET  /sources
GET  /articles
GET  /articles?semantic_query=biology&source=arxiv
GET  /users/{user_id}/feed
GET  /users/{user_id}/profile
PUT  /users/{user_id}/profile
GET  /users/{user_id}/tags
GET  /users/{user_id}/recently-viewed
POST /users/{user_id}/recently-viewed
POST /login
GET  /me
POST /logout
PUT  /users/{user_id}/tags
```

## Git Notes

`scicomm_embedding` and `igather2` were imported with non-squashed Git subtree
merges, so their histories are reachable from this repository's history.

For future work in the old standalone repositories, do not force-push over a
partner's branch. Have collaborators commit or stash local work before pulling
updates:

```bash
git status
git stash push -u -m "work before monorepo sync"
git pull
git stash pop
```

Committed work in the old repositories can still be brought into this monorepo
with `git subtree pull` for the appropriate prefix.

## Notes

- Backend runs on port `8000`.
- Article/search service runs on port `8100` by default.
- Frontend dev server runs on port `5173`.
- Postgres runs on local port `5432`.
- User login/session/profile/interests/recently viewed are stored through the backend and Postgres. The browser also keeps a non-authoritative cache of the last profile and source filter so refreshes feel remembered; `/me` verifies the session and refreshes profile data from the DB.
- Article listing/search is proxied through `ARTICLE_SERVICE_BASE_URL`; Postgres is not the source of article records.
- Frontend, user backend, Postgres, and article/search service can run together for now; they can be split across servers once deployment requirements are clearer.
