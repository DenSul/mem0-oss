# mem0_oss — Self-Hosted Mem0 Memory Provider for Hermes Agent

Plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that connects to any **self-hosted Mem0 OSS server** via REST API — no Mem0 cloud, no API keys required.

---

## Why this exists

Hermes Agent already has an official `mem0` plugin. So why build another one?

The official plugin and the `mem0ai` Python SDK are built for **Mem0 Platform** — the managed cloud service. They use `/v2/memories/search/` endpoints that simply don't exist on a self-hosted Mem0 OSS server:

```
SDK calls:  /v2/memories/search/   ← Mem0 Platform only
OSS has:    /v1/memories/search/   ← your own server
```

Every attempt to use the official SDK against a self-hosted instance ends the same way:

```
mem0ai 2.x:  "Connection refused" or 404 on /v2/...
mem0ai 1.x:  Same problem — still calls /v2/ endpoints
```

There's no configuration flag to switch API versions. The SDK is tightly coupled to the Platform API.

**This plugin solves it** by calling your Mem0 OSS server directly over REST — same endpoints your server actually provides. It was tested against a real self-hosted instance at `http://YOUR_MEM0_SERVER_IP:8420` and handles all three core operations: search, profile, and conclude.

---

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
| `MEM0_API_KEY` | No | `local` | Any value; OSS accepts any key |
| `MEM0_USER_ID` | No | `hermes-user` | User identifier for memory scoping |
| `MEM0_AGENT_ID` | No | `hermes` | Agent identifier |

## How it works

The provider uses direct REST calls to your Mem0 OSS server:

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Search | `POST` | `/v1/memories/search/` |
| Profile | `GET` | `/v1/memories/?user_id={user_id}` |
| Store fact | `POST` | `/v1/memories/` |

No official SDK used — avoids API version mismatches entirely.

## Repository Structure

```
mem0-oss/
├── __init__.py    # Mem0OSSMemoryProvider implementation
└── plugin.yaml     # Plugin manifest
```

## License

MIT
