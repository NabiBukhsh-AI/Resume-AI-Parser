"""Content-addressed cache for parse results.

Parsing is a pure function of (document bytes, model, prompt version, schema), so the same
upload never needs to be paid for twice. In recruiting workloads this is not a micro-
optimisation: the same CV arrives repeatedly through different channels, and re-parsing a
50-page portfolio at Opus rates adds up fast.

Two tiers, both optional. The in-memory LRU absorbs the common case; the disk tier makes
results survive a restart or be shared across workers via a mounted volume.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from resume_parser.observability.logging import get_logger
from resume_parser.settings import CacheSettings

__all__ = ["ParseCache", "build_cache_key"]

logger = get_logger(__name__)


def build_cache_key(
    *,
    content_sha256: str,
    model_label: str,
    prompt_version: str,
    schema_fingerprint: str,
) -> str:
    """Derive the cache key for one parse.

    Every input that can change the output is folded in, so bumping a prompt or editing
    the schema invalidates the affected entries automatically instead of serving results
    that silently predate the change.
    """
    material = "|".join((content_sha256, model_label, prompt_version, schema_fingerprint))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Entry:
    """A cached payload with its creation time."""

    value: dict[str, Any]
    stored_at: float


class ParseCache:
    """An async-safe LRU cache with an optional write-through disk tier."""

    def __init__(self, settings: CacheSettings | None = None) -> None:
        self._settings = settings or CacheSettings()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._directory = self._settings.directory
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        """Whether reads and writes do anything."""
        return self._settings.enabled

    @property
    def stats(self) -> dict[str, int | float]:
        """Hit/miss counters, exposed on the health endpoint."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._entries),
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }

    def _expired(self, entry: _Entry) -> bool:
        """True when ``entry`` has outlived the configured TTL."""
        ttl = self._settings.ttl_seconds
        return ttl > 0 and (time.time() - entry.stored_at) > ttl

    def _disk_path(self, key: str) -> Path | None:
        """Location of ``key`` on the disk tier, if one is configured."""
        if self._directory is None:
            return None
        # Shard by prefix so a busy cache does not put 100k files in one directory.
        shard = self._directory / key[:2]
        return shard / f"{key}.json"

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached payload for ``key``, or ``None`` on a miss."""
        if not self.enabled:
            return None
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if self._expired(entry):
                    del self._entries[key]
                else:
                    self._entries.move_to_end(key)
                    self._hits += 1
                    return entry.value

        if (value := await self._read_disk(key)) is not None:
            async with self._lock:
                self._store_memory(key, _Entry(value=value, stored_at=time.time()))
                self._hits += 1
            return value

        async with self._lock:
            self._misses += 1
        return None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        """Store ``value`` under ``key`` in every configured tier."""
        if not self.enabled:
            return
        entry = _Entry(value=value, stored_at=time.time())
        async with self._lock:
            self._store_memory(key, entry)
        await self._write_disk(key, value)

    def _store_memory(self, key: str, entry: _Entry) -> None:
        """Insert into the LRU, evicting the coldest entry when at capacity."""
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._settings.max_entries:
            self._entries.popitem(last=False)

    async def _read_disk(self, key: str) -> dict[str, Any] | None:
        """Load ``key`` from the disk tier, treating any failure as a miss."""
        path = self._disk_path(key)
        if path is None or not path.is_file():
            return None
        try:
            payload = await asyncio.to_thread(path.read_text, encoding="utf-8")
            record = json.loads(payload)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("cache_disk_read_failed", key=key, reason=str(exc))
            return None

        ttl = self._settings.ttl_seconds
        if ttl > 0 and (time.time() - float(record.get("stored_at", 0))) > ttl:
            return None
        value = record.get("value")
        return value if isinstance(value, dict) else None

    async def _write_disk(self, key: str, value: dict[str, Any]) -> None:
        """Persist ``key`` to the disk tier. Failures are logged, never raised."""
        path = self._disk_path(key)
        if path is None:
            return
        record = {"stored_at": time.time(), "value": value}
        try:
            await asyncio.to_thread(_atomic_write, path, json.dumps(record))
        except OSError as exc:
            logger.warning("cache_disk_write_failed", key=key, reason=str(exc))

    async def clear(self) -> None:
        """Drop every in-memory entry. The disk tier is left alone."""
        async with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0


def _atomic_write(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` via a temp file, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(path)
