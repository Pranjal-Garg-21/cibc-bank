"""The single LLM client. Every model call in the project goes through here.

What it adds on top of the vendor SDK:
- model tiers from config (`answer`, `bulk`), so no model ID is hard-coded elsewhere
- retries with exponential backoff
- a response cache keyed by a hash of the whole request (model, prompts, settings, prompt version), so a
  re-run never pays twice for the same note or question
- optional JSON output, parsed and checked
- the prompt version recorded with the result and written to the audit log

Providers sit behind a small `Backend` interface: any OpenAI-style server (OpenRouter by default), Anthropic, or a stand-in for tests.
Cached answers are still audited: the log records every question asked, not only every paid call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import config


class LLMError(Exception):
    """The call failed after all retries."""


class LLMTruncated(LLMError):
    """The reply was cut off at the token limit, so it cannot be trusted (it is retried with a larger limit)."""


class LLMOutputError(LLMError):
    """The model answered, but not in the requested JSON shape."""


class LLMNotConfigured(LLMError):
    """No API key (or similar): retrying cannot help, so the call fails at once."""


class Backend(Protocol):
    def complete(self, *, model: str, system: str, user: str, max_tokens: int, temperature: float) -> str: ...


class AnthropicBackend:
    """Anthropic Messages API. Reads ANTHROPIC_API_KEY from the environment."""

    def __init__(self):
        import anthropic  # imported here so the rest of the project works without the SDK installed

        self._client = anthropic.Anthropic()

    def complete(self, *, model, system, user, max_tokens, temperature) -> str:
        msg = self._client.messages.create(
            model=model, system=system, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _load_dotenv() -> None:
    """Read KEY=value lines from the repo's .env (gitignored) into the environment, without overriding it."""
    path = config.REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


class OpenAICompatibleBackend:
    """Any server that speaks the OpenAI chat-completions format: OpenRouter, NVIDIA, a local server.

    The key is read from the environment variable named in config (`api_key_env`), never from a file we commit.
    """

    def __init__(self, base_url: str, api_key_env: str, timeout: float = 120, extra_body: dict | None = None):
        _load_dotenv()
        self.extra_body = extra_body or {}  # provider options sent with every request, e.g. {"reasoning": {...}}
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key_env = api_key_env
        self.timeout = timeout

    def complete(self, *, model, system, user, max_tokens, temperature) -> str:
        key = os.environ.get(self.key_env)
        if not key:
            raise LLMNotConfigured(f"{self.key_env} is not set (put it in the environment or in the repo's .env file)")
        body = json.dumps({
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **self.extra_body,
        }).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:  # keep the server's reason; never include the key
            if e.code in (401, 402, 403):  # bad key, no credit, spending limit reached: retrying cannot help
                raise LLMNotConfigured(f"HTTP {e.code} from {self.url}: {e.read()[:300].decode(errors='replace')}") from e
            raise LLMError(f"HTTP {e.code} from {self.url}: {e.read()[:300].decode(errors='replace')}") from e
        if payload.get("error"):  # some routers return 200 with an error object
            raise LLMError(f"provider error: {str(payload['error'])[:300]}")
        try:
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":  # cut off mid-reply: never hand this on as an answer
                raise LLMTruncated(f"reply cut off at max_tokens={max_tokens}")
            return choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected reply shape: {str(payload)[:200]}") from e


@dataclass
class LLMResult:
    text: str
    data: dict | list | None  # parsed JSON when json_output=True
    model: str
    prompt_version: str
    cached: bool


class ResponseCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, response TEXT NOT NULL)")
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT response FROM cache WHERE key=?", [key]).fetchone()
        return row[0] if row else None

    def put(self, key: str, response: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)", [key, response])
            self._db.commit()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json(text: str):
    """Parse a model reply as JSON, tolerating a Markdown code fence or text around the object."""
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1), max(t.rfind("}"), t.rfind("]"))
        if start < 0 or end <= start:
            raise LLMOutputError(f"reply is not JSON: {text[:200]!r}")
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError as e:
            raise LLMOutputError(f"reply is not valid JSON: {e}") from e


class LLM:
    def __init__(self, backend: Backend | None = None, *, audit=None, cache: ResponseCache | None = None,
                 models: dict[str, str] | None = None, max_retries: int | None = None, sleep=time.sleep):
        cfg = config.settings().get("llm", {})
        self.cfg = cfg
        self.models = models or cfg.get("models", {})
        self.max_retries = cfg.get("max_retries", 3) if max_retries is None else max_retries
        self.backend = backend
        self.audit = audit
        self.cache = cache
        self._sleep = sleep
        if cache is None and cfg.get("cache_file"):
            self.cache = ResponseCache(config.warehouse_path().parent / cfg["cache_file"])

    def _backend(self) -> Backend:
        if self.backend is None:
            if self.cfg.get("provider") == "anthropic":
                self.backend = AnthropicBackend()
            else:
                self.backend = OpenAICompatibleBackend(
                    self.cfg["base_url"], self.cfg.get("api_key_env", "OPENROUTER_API_KEY"),
                    self.cfg.get("timeout_secs", 120), self.cfg.get("extra_body"))
        return self.backend

    def complete(self, *, system: str, user: str, prompt_version: str, tier: str = "answer",
                 json_output: bool = False, max_tokens: int = 1024, temperature: float = 0.0,
                 audit_ctx: dict | None = None) -> LLMResult:
        """Run one call. `audit_ctx` carries kind, input_text, sources, user_role and user_id for the log."""
        model = self.models.get(tier)
        if not model:
            raise LLMError(f"no model chosen for tier '{tier}': set llm.models.{tier} in config/settings.yaml")
        key = hashlib.sha256(json.dumps(
            [model, system, user, max_tokens, temperature, prompt_version, json_output, self.cfg.get("extra_body")], ensure_ascii=False
        ).encode()).hexdigest()

        text = self.cache.get(key) if self.cache else None
        cached = text is not None
        data = None
        for attempt in range(self.max_retries + 1):
            if text is None:
                text = self._call(model, system, user, max_tokens, temperature)
            try:
                data = parse_json(text) if json_output else None
                break
            except LLMOutputError:  # the model answered, but not in JSON: ask again (the bad reply is never cached)
                if cached or attempt == self.max_retries:
                    raise
                text = None
                self._sleep(min(2 ** attempt, 30))
        if not cached and self.cache:
            self.cache.put(key, text)

        if self.audit and audit_ctx is not None:
            self.audit.record(prompt=f"[system]\n{system}\n\n[user]\n{user}", answer=text, model=model,
                              prompt_version=prompt_version, **audit_ctx)
        return LLMResult(text, data, model, prompt_version, cached)

    def _call(self, model, system, user, max_tokens, temperature) -> str:
        cap = self.cfg.get("max_tokens_cap", 8000)
        last: Exception | None = None
        attempt = 0
        while attempt <= self.max_retries:
            try:
                return self._backend().complete(model=model, system=system, user=user,
                                                max_tokens=max_tokens, temperature=temperature)
            except LLMNotConfigured:
                raise
            except LLMTruncated as e:  # not a service failure: the answer needs more room, so give it more
                last = e
                if max_tokens >= cap:
                    break
                max_tokens = min(max_tokens * 2, cap)
                continue
            except Exception as e:  # network, rate limit, overload: all worth another try
                last = e
                attempt += 1
                if attempt <= self.max_retries:
                    self._sleep(min(2 ** (attempt - 1), 30))
        raise LLMError(f"{model} failed after {self.max_retries + 1} attempts: {last}") from last
