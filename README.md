# GATSV-OS

OS for GATSV Labs: a calm, AI‑assisted operating system for solo creators and small teams. This repo currently contains the core **control‑plane** service.

## What’s inside

- `services/control-plane/`: FastAPI service exposing health checks, webhooks, and email connectors
- `docs/`: architecture notes and current‑state handoff docs
- `docker-compose.yml`: local dev stack (API + Postgres)
- `.env.example`: starter environment variables

## Quick start (local)

```bash
# 1. Clone and enter the repo
git clone https://github.com/GATSV-tech/GATSV-OS-.git
cd GATSV-OS-

# 2. Create your env file
cp .env.example .env
# edit .env with your own secrets (Postgres, Postmark, etc.)

# 3. Run the stack
docker compose up --build
```

The control‑plane API will be available at `http://localhost:8000`, with interactive docs at `http://localhost:8000/docs`.

## Tech stack

- Python / FastAPI
- PostgreSQL
- Docker + Docker Compose
