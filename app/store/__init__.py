"""Storage layer: a tenant-aware Store interface with local and cloud backends."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.store.base import Store
from app.store.local import LocalStore

__all__ = ["Store", "LocalStore", "get_store"]


@lru_cache
def get_store() -> Store:
    """Return the configured backend: LocalStore (default) or CloudStore (GCS+Firestore)."""
    s = get_settings()
    if s.store_backend.lower() == "cloud":
        from app.store.cloud import CloudStore  # lazy: google-cloud SDKs only needed here
        return CloudStore(s)
    return LocalStore()
