#!/usr/bin/env bash
# Runs everything CloudAgent has today, in the right order, in one command:
#   Docker (Postgres + Redis) -> Alembic migrations -> backend -> agent_loop -> frontend
#
# Usage:  ./dev.sh          (from the repo root)
#         Ctrl+C stops backend + agent_loop + frontend. Docker keeps
#         running -- run `docker compose down` separately if you want it
#         stopped too (kept running on purpose: killing your DB on every
#         Ctrl+C would be more annoying than helpful for a dev loop).

set -eo pipefail
cd "$(dirname "$0")"

LOG_DIR=".dev-logs"
mkdir -p "$LOG_DIR"

echo "==> Checking for .env"
if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill in real values first:"
  echo "    cp .env.example .env"
  exit 1
fi

echo "==> Freeing ports 8000/8001/5173 if a stale process is still holding them"
# Leftover uvicorn/vite processes from a previous run are the single most
# common cause of "it's running but I get connection refused" during this
# project's dev loop -- kill anything bound to our three ports before
# starting fresh ones, rather than silently colliding with them.
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:8001 | xargs kill -9 2>/dev/null || true
lsof -ti:5173 | xargs kill -9 2>/dev/null || true

echo "==> Starting Postgres + Redis (docker compose)"
# --wait blocks until both services' healthchecks pass, so nothing below
# races an unready database -- no manual polling loop needed.
docker compose up -d --wait

echo "==> Applying database migrations"
uv run --package cloudagent-core alembic -c cloudagent_core/alembic.ini upgrade head

echo "==> Starting backend on :8000 (log: $LOG_DIR/backend.log)"
uv run --package cloudagent-backend uvicorn app.main:app --app-dir backend --reload --port 8000 \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

echo "==> Starting agent_loop on :8001 (log: $LOG_DIR/agent_loop.log)"
uv run --package cloudagent-agent-loop uvicorn app.main:app --app-dir agent_loop --reload --port 8001 \
  > "$LOG_DIR/agent_loop.log" 2>&1 &
AGENT_LOOP_PID=$!

echo "==> Starting frontend on :5173 (log: $LOG_DIR/frontend.log)"
(cd frontend && npm run dev -- --port 5173) > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "==> Stopping backend + agent_loop + frontend (Docker left running -- 'docker compose down' to stop that too)"
  # Killing by port, not by the captured $! PIDs: both `uv run` and
  # `npm run dev` are wrapper processes that spawn the real
  # uvicorn/vite as a *child* -- killing only the wrapper PID leaves
  # the actual listening process orphaned and still bound to the port,
  # which then breaks the next run. Whatever's actually listening on
  # these three ports is what needs to die, regardless of which wrapper
  # spawned it.
  lsof -ti:8000 | xargs kill -9 2>/dev/null || true
  lsof -ti:8001 | xargs kill -9 2>/dev/null || true
  lsof -ti:5173 | xargs kill -9 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

sleep 2
echo ""
echo "-------------------------------------------------------------"
echo "  Frontend:        http://localhost:5173"
echo "  Backend:         http://localhost:8000  (/healthz, /docs)"
echo "  Agent Loop:      http://localhost:8001  (/healthz, /docs)"
echo "  Postgres/Redis:  docker compose ps"
echo "-------------------------------------------------------------"
echo "Tailing logs below. Ctrl+C to stop backend + agent_loop + frontend."
echo ""

tail -f "$LOG_DIR/backend.log" "$LOG_DIR/agent_loop.log" "$LOG_DIR/frontend.log"
