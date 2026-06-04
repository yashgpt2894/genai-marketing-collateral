"""
Result cache — an optional Memorystore (Redis) layer in front of generation.

Generation is the expensive path (a Gemini call + ~tens of seconds). When the
*same* request comes again — same tenant, pair, prompt, template, and unchanged
briefs — we can return the prior ArticleJSON instead of paying for it again.

Design choices that keep this safe to ship:
  * **Off by default** (`CACHE_BACKEND=none` -> NullCache). Local/dev and the
    current deployment behave exactly as before until Redis is wired in.
  * **Fail-open everywhere.** A cache miss, a timeout, or Redis being down must
    never break a request — every Redis call is wrapped and falls back to "miss".
  * **Correctness via the key.** The key includes a fingerprint of the briefs, so
    re-uploading a company's PDF (new facts) naturally invalidates old results.

The cache stores `ArticleJSON.model_dump_json()` strings, keyed per tenant.
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import Optional, Protocol

from app.config import get_settings
from app.schemas import ArticleJSON, CompanyBrief

log = logging.getLogger("collateral.cache")


def brief_fingerprint(sender: CompanyBrief, receiver: CompanyBrief) -> str:
    """Stable 16-hex digest of both briefs; changes when either brief changes."""
    blob = sender.model_dump_json() + "\x1f" + receiver.model_dump_json()
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def gen_cache_key(tenant: str, pair_id: str, prompt: str, template_id: str, brief_fp: str) -> str:
    digest = hashlib.sha1(
        f"{prompt}\x1f{template_id}\x1f{brief_fp}".encode("utf-8")
    ).hexdigest()[:16]
    return f"gen:{tenant}:{pair_id}:{digest}"


class Cache(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class NullCache:
    """No-op cache: every get is a miss. The default."""

    enabled = False

    def get(self, key: str) -> Optional[str]:
        return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None


class RedisCache:
    """Memorystore for Redis. Lazy-imports `redis`; every call is fail-open."""

    enabled = True

    def __init__(self, host: str, port: int):
        import redis  # lazy: only needed when CACHE_BACKEND=redis

        # short timeouts: a slow cache must not slow the request path
        self._r = redis.Redis(
            host=host, port=port, socket_timeout=0.5,
            socket_connect_timeout=0.5, retry_on_timeout=False,
        )

    def get(self, key: str) -> Optional[str]:
        try:
            v = self._r.get(key)
            return v.decode("utf-8") if v else None
        except Exception as e:  # pragma: no cover - network failure path
            log.warning("cache get failed (open): %s", e)
            return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            self._r.set(key, value, ex=ttl_seconds)
        except Exception as e:  # pragma: no cover - network failure path
            log.warning("cache set failed (ignored): %s", e)


@lru_cache
def get_cache() -> Cache:
    s = get_settings()
    if s.cache_backend == "redis":
        try:
            log.info("cache: Redis at %s:%s (ttl=%ss)", s.redis_host, s.redis_port, s.cache_ttl_seconds)
            return RedisCache(s.redis_host, s.redis_port)
        except Exception as e:  # redis lib missing / bad config -> degrade, don't crash
            log.warning("cache: Redis init failed, using NullCache: %s", e)
            return NullCache()
    return NullCache()


def load_cached(cache: Cache, key: str) -> Optional[ArticleJSON]:
    """Read+parse a cached ArticleJSON, or None on miss/garbage (fail-open)."""
    raw = cache.get(key)
    if not raw:
        return None
    try:
        return ArticleJSON.model_validate_json(raw)
    except Exception as e:  # pragma: no cover - corrupt entry
        log.warning("cache: ignoring unparseable entry %s: %s", key, e)
        return None
