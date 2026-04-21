"""
Mem0 OSS — Self-hosted Mem0 memory provider plugin for Hermes Agent.

Works with any Mem0 OSS server via REST API (no official SDK required).

Config via environment variables:
  MEM0_BASE_URL       — Base URL of self-hosted Mem0 server (required)
  MEM0_API_KEY        — API key for authentication (optional, default: any non-empty value)
  MEM0_USER_ID        — User identifier (default: hermes-user)
  MEM0_AGENT_ID       — Agent identifier (default: hermes)

Install:
  1. Copy this directory to ~/.hermes/hermes-agent/plugins/memory/mem0_oss/
  2. Set MEM0_BASE_URL in ~/.hermes/.env
  3. Restart Hermes
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List

import httpx

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120

DEFAULT_TOOLS = ["mem0_search", "mem0_profile", "mem0_conclude"]


def _load_config() -> dict:
    from hermes_constants import get_hermes_home

    config = {
        "base_url": os.environ.get("MEM0_BASE_URL", "").rstrip("/"),
        "api_key": os.environ.get("MEM0_API_KEY", "local"),
        "user_id": os.environ.get("MEM0_USER_ID", "hermes-user"),
        "agent_id": os.environ.get("MEM0_AGENT_ID", "hermes"),
        "tools": _parse_tools(os.environ.get("MEM0_TOOLS", "")),
    }

    config_path = get_hermes_home() / "mem0_oss.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items() if v is not None and v != ""})
        except Exception:
            pass

    return config


def _parse_tools(value: str) -> List[str]:
    if not value:
        return DEFAULT_TOOLS
    return [t.strip() for t in value.split(",") if t.strip()]


SEARCH_SCHEMA = {
    "name": "mem0_search",
    "description": (
        "Search memories by meaning. Returns relevant facts ranked by similarity. "
        "Set rerank=true for higher accuracy on important queries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "rerank": {"type": "boolean", "description": "Enable reranking for precision (default: false)."},
            "top_k": {"type": "integer", "description": "Max results (default: 10, max: 50)."},
        },
        "required": ["query"],
    },
}

PROFILE_SCHEMA = {
    "name": "mem0_profile",
    "description": (
        "Retrieve all stored memories about the user — preferences, facts, "
        "project context. Fast, no reranking. Use at conversation start."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CONCLUDE_SCHEMA = {
    "name": "mem0_conclude",
    "description": (
        "Store a durable fact about the user. Stored verbatim (no LLM extraction). "
        "Use for explicit preferences, corrections, or decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {"type": "string", "description": "The fact to store."},
        },
        "required": ["conclusion"],
    },
}


class Mem0OSSMemoryProvider(MemoryProvider):
    """Self-hosted Mem0 OSS via direct REST API."""

    def __init__(self):
        self._config: dict = {}
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return "mem0_oss"

    def is_available(self) -> bool:
        cfg = _load_config()
        return bool(cfg.get("base_url"))

    def get_config_schema(self):
        return [
            {
                "key": "base_url",
                "description": "Base URL of self-hosted Mem0 server (e.g. http://72.56.117.69:8420)",
                "secret": False,
                "required": True,
                "env_var": "MEM0_BASE_URL",
            },
            {
                "key": "api_key",
                "description": "API key for Mem0 server authentication",
                "secret": True,
                "required": False,
                "default": "local",
                "env_var": "MEM0_API_KEY",
            },
            {
                "key": "user_id",
                "description": "User identifier",
                "default": "hermes-user",
                "env_var": "MEM0_USER_ID",
            },
            {
                "key": "agent_id",
                "description": "Agent identifier",
                "default": "hermes",
                "env_var": "MEM0_AGENT_ID",
            },
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._user_id = kwargs.get("user_id") or self._config.get("user_id", "hermes-user")
        self._agent_id = self._config.get("agent_id", "hermes")

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is not None:
                return self._client
            base_url = self._config["base_url"]
            api_key = self._config.get("api_key", "local")
            headers = {}
            if api_key:
                headers["Authorization"] = f"Token {api_key}"
            self._client = httpx.Client(
                base_url=base_url,
                headers=headers,
                timeout=30.0,
            )
            return self._client

    def _is_breaker_open(self) -> bool:
        if self._consecutive_failures < _BREAKER_THRESHOLD:
            return False
        if time.monotonic() >= self._breaker_open_until:
            self._consecutive_failures = 0
            return False
        return True

    def _record_success(self):
        self._consecutive_failures = 0

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning(
                "Mem0 OSS circuit breaker tripped after %d failures. Pausing for %ds.",
                self._consecutive_failures,
                _BREAKER_COOLDOWN_SECS,
            )

    def _read_filters(self) -> Dict[str, Any]:
        return {"user_id": self._user_id}

    def _write_filters(self) -> Dict[str, Any]:
        return {"user_id": self._user_id, "agent_id": self._agent_id}

    def _unwrap_results(self, response: Any) -> list:
        if isinstance(response, dict):
            return response.get("results", [])
        if isinstance(response, list):
            return response
        return []

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        client = self._get_client()
        return client.request(method, path, **kwargs)

    def system_prompt_block(self) -> str:
        return (
            "# Mem0 Memory\n"
            f"Active. User: {self._user_id}.\n"
            "Use mem0_search to find memories, mem0_conclude to store facts, "
            "mem0_profile for a full overview."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## Mem0 Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._is_breaker_open():
            return

        def _run():
            try:
                resp = self._request("POST", "/v1/memories/search/", json={"query": query, "user_id": self._user_id, "top_k": 5})
                resp.raise_for_status()
                data = resp.json()
                results = self._unwrap_results(data)
                if results:
                    lines = [r.get("memory", "") for r in results if r.get("memory")]
                    with self._prefetch_lock:
                        self._prefetch_result = "\n".join(f"- {l}" for l in lines)
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mem0 OSS prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="mem0-oss-prefetch")
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self._is_breaker_open():
            return

        def _sync():
            try:
                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": assistant_content},
                ]
                filters = self._write_filters()
                resp = self._request("POST", "/v1/memories/", json={**filters, "messages": messages})
                resp.raise_for_status()
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.warning("Mem0 OSS sync failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="mem0-oss-sync")
        self._sync_thread.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [PROFILE_SCHEMA, SEARCH_SCHEMA, CONCLUDE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._is_breaker_open():
            return tool_error(
                "Mem0 OSS API temporarily unavailable (multiple consecutive failures). Will retry automatically."
            )

        try:
            if tool_name == "mem0_profile":
                return self._handle_profile()
            elif tool_name == "mem0_search":
                return self._handle_search(args)
            elif tool_name == "mem0_conclude":
                return self._handle_conclude(args)
            return tool_error(f"Unknown tool: {tool_name}")
        except httpx.HTTPStatusError as e:
            self._record_failure()
            return tool_error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            self._record_failure()
            return tool_error(f"Mem0 OSS error: {e}")

    def _handle_profile(self) -> str:
        resp = self._request("GET", "/v1/memories/", params={"user_id": self._user_id})
        resp.raise_for_status()
        data = resp.json()
        memories = self._unwrap_results(data)
        self._record_success()
        if not memories:
            return json.dumps({"result": "No memories stored yet."})
        lines = [m.get("memory", "") for m in memories if m.get("memory")]
        return json.dumps({"result": "\n".join(lines), "count": len(lines)})

    def _handle_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")
        top_k = min(int(args.get("top_k", 10)), 50)
        payload = {
            "query": query,
            "user_id": self._user_id,
            "top_k": top_k,
        }
        resp = self._request("POST", "/v1/memories/search/", json=payload)
        resp.raise_for_status()
        data = resp.json()
        results = self._unwrap_results(data)
        self._record_success()
        if not results:
            return json.dumps({"result": "No relevant memories found."})
        items = [{"memory": r.get("memory", ""), "score": r.get("score", 0)} for r in results]
        return json.dumps({"results": items, "count": len(items)})

    def _handle_conclude(self, args: dict) -> str:
        conclusion = args.get("conclusion", "")
        if not conclusion:
            return tool_error("Missing required parameter: conclusion")
        payload = {
            **self._write_filters(),
            "messages": [{"role": "user", "content": conclusion}],
            "infer": False,
        }
        resp = self._request("POST", "/v1/memories/", json=payload)
        resp.raise_for_status()
        self._record_success()
        return json.dumps({"result": "Fact stored."})

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        with self._client_lock:
            if self._client:
                self._client.close()
                self._client = None


def register(ctx) -> None:
    ctx.register_memory_provider(Mem0OSSMemoryProvider())
