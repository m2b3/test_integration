# Scicommons Integration Prototype

Prototype frontend + backend + database stack for Scicommons.

Current deployment shape:

```text
React frontend -> FastAPI backend -> Postgres database
```

For the prototype, all three can run on one Arbutus server.

## Server Setup

SSH to the Arbutus server at `134.87.8.193`.

Clone the repo:

```bash
git clone git@github.com:m2b3/test_integration.git
cd test_integration
```

If GitHub SSH is not set up on the server, add a server SSH key to GitHub first.

## Database

Start Postgres:

```bash
sudo docker compose up -d db
```

If Docker requires sudo every time, either keep using `sudo docker ...` or add the user to the Docker group and reconnect:

```bash
sudo usermod -aG docker $USER
```

Create/seed the database:

```bash
cd ~/test_integration/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons \
python setup_database.py
```

The setup script drops and recreates the prototype tables on every run.

## Backend

Start the FastAPI backend:

```bash
cd ~/test_integration/backend
source .venv/bin/activate

DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify from the server:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/tags
```

Verify externally:

```bash
curl http://134.87.8.193:8000/health
```

If local curl works but external curl hangs, open inbound TCP port `8000` in the Arbutus security/firewall settings.

## Frontend

Create the frontend environment file:

```bash
cd ~/test_integration/frontend
echo "VITE_API_BASE_URL=http://134.87.8.193:8000" > .env
```

Install Node 22 if the server Node version is too old for Vite:

```bash
cd ~
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node -v
```

Install dependencies and start the dev server:

```bash
cd ~/test_integration/frontend
npm install
npm run dev -- --host 0.0.0.0
```

Open:

```text
http://134.87.8.193:5173
```

If the page is not reachable externally, open inbound TCP port `5173` in the Arbutus security/firewall settings.

## Running With tmux

Use tmux so backend and frontend keep running after logout.

Create a session:

```bash
tmux new -s scicommons
```

In the first tmux window, run the backend:

```bash
cd ~/test_integration/backend
source .venv/bin/activate
DATABASE_URL=postgresql://scicommons:scicommons@localhost:5432/scicommons \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Create a second tmux window:

```text
Ctrl+b, then c
```

Run the frontend:

```bash
cd ~/test_integration/frontend
npm run dev -- --host 0.0.0.0
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
GET  /articles?tags=biology,chemistry&match=or
GET  /users/{user_id}/feed
GET  /users/{user_id}/profile
PUT  /users/{user_id}/profile
GET  /users/{user_id}/tags
GET  /users/{user_id}/recently-viewed
POST /users/{user_id}/recently-viewed
POST /login
PUT  /users/{user_id}/tags
```

## Notes

- Backend runs on port `8000`.
- Frontend dev server runs on port `5173`.
- Postgres runs on local port `5432`.
- Frontend `.env` is intentionally not committed; recreate it on each server.
- This is a prototype deployment. Later, frontend/backend/database can be separated if needed.
