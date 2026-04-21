# mem0_oss — Self-Hosted Mem0 Memory Provider for Hermes Agent

Plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that connects to any **self-hosted Mem0 OSS server** via REST API.

Unlike the official `mem0` plugin which requires Mem0 Platform API keys, this provider works with your own Mem0 OSS instance — no external dependencies, no API keys needed (use `local`).

## Features

- `mem0_search` — semantic memory search with relevance scores
- `mem0_profile` — retrieve all stored facts about a user
- `mem0_conclude` — persist a new fact
- Thread-safe httpx client with connection pooling
- Circuit breaker (pauses after 5 consecutive failures)
- Background prefetch and sync threads
- Config via `~/.hermes/.env`

## Requirements

- Hermes Agent
- A running [Mem0 OSS](https://github.com/mem0ai/mem0) server

## Quick Start

### 1. Install

```bash
hermes plugins install DenSul/mem0-oss
```

### 2. Configure

Add to `~/.hermes/.env`:

```env
MEM0_BASE_URL=http://localhost:8420
MEM0_API_KEY=local
MEM0_USER_ID=your-user-id
MEM0_AGENT_ID=hermes
```

### 3. Enable and restart

```bash
hermes plugins enable mem0_oss
hermes gateway restart
```

## Mem0 OSS Server Setup

```bash
docker run -d -p 8420:8000 \
  -e ADMIN_API_KEY=your-secret-key \
  mem0ai/mem0
```

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MEM0_BASE_URL` | Yes | — | Mem0 OSS server URL |
| `MEM0_API_KEY` | No | `local` | Any value; OSS server accepts any key |
| `MEM0_USER_ID` | No | `hermes-user` | User identifier for memory scoping |
| `MEM0_AGENT_ID` | No | `hermes` | Agent identifier |

## Repository Structure

```
mem0-oss/
├── __init__.py    # Mem0OSSMemoryProvider implementation
└── plugin.yaml     # Plugin manifest
```

## How It Works

The provider uses direct REST calls to your Mem0 OSS server:

- `GET /v1/memories/?user_id={user_id}` — profile
- `POST /v1/memories/search/` — semantic search
- `POST /v1/memories/` — store new fact

No official SDK used — avoids API version mismatches with self-hosted servers.

## License

MIT
