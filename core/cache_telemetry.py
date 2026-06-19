#!/usr/bin/env python3
"""
cache_telemetry.py -- Session-wide prefix cache hit tracking.

Reasonix Pillar 3 (partial): cost/cache visibility.

Tracks cumulative cache hit/miss tokens from DeepSeek usage responses,
computes per-session hit rate, and provides degradation detection.

Already partially implemented in api.py (_report_cache_hit,
_check_cache_degradation).  This module provides a clean API for
reading those stats and displaying them.
"""

from __future__ import annotations



def get_cache_stats() -> dict:
    """Read current cache statistics from _TOOL_CONTEXT.

    Returns a dict with keys:
      hits, misses, calls, hit_rate_pct, input_tokens, output_tokens,
      estimated_usd_saved, turn_history
    """
    try:
        from tools import _TOOL_CONTEXT
        if _TOOL_CONTEXT is None:
            return _empty_stats()
        stats = getattr(_TOOL_CONTEXT, "_cache_stats", None)
        if stats is None:
            return _empty_stats()
        total = stats.get("hits", 0) + stats.get("misses", 0)
        hit_rate = (stats.get("hits", 0) / total * 100) if total > 0 else 0.0
        history = getattr(_TOOL_CONTEXT, "_cache_turn_history", [])
        return {
            "hits": stats.get("hits", 0),
            "misses": stats.get("misses", 0),
            "calls": stats.get("calls", 0),
            "hit_rate_pct": round(hit_rate, 2),
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("output_tokens", 0),
            "estimated_usd_saved": _estimate_usd_saved(stats.get("hits", 0)),
            "turn_history": history[-8:] if history else [],
        }
    except Exception:
        return _empty_stats()


def get_semantic_cache_stats() -> dict:
    """Read semantic cache statistics."""
    try:
        from tools import _TOOL_CONTEXT
        if _TOOL_CONTEXT is None:
            return {"hits": 0, "misses": 0, "estimated_usd_saved": 0.0}
        s = getattr(_TOOL_CONTEXT, "_semantic_cache_stats", None)
        if s is None:
            return {"hits": 0, "misses": 0, "estimated_usd_saved": 0.0}
        return dict(s)
    except Exception:
        return {"hits": 0, "misses": 0, "estimated_usd_saved": 0.0}


def get_cache_status_line() -> str:
    """Return a one-line cache status for display (TUI/terminal)."""
    prefix = _get_prefix_stats()
    dcache = get_cache_stats()
    scache = get_semantic_cache_stats()

    prefix_rate = prefix.get("hit_rate_pct", 0)
    dcache_rate = dcache.get("hit_rate_pct", 0)

    prefix_str = f"prefix={prefix_rate:.0f}%" if prefix.get("total", 0) > 0 else "prefix=n/a"
    dcache_str = f"dcache={dcache_rate:.0f}%" if dcache.get("calls", 0) > 0 else "dcache=no data"

    return f"[cache] {prefix_str} {dcache_str} semantic={scache.get('hits', 0)}hits"


def _get_prefix_stats() -> dict:
    """Read prefix fingerprint stability stats."""
    from core.prefix import _session_prefix_cache
    cache = _session_prefix_cache
    prefix = cache.prefix
    if prefix is None:
        return {"fingerprint": None, "total": 0, "hit_rate_pct": 0.0}
    return {
        "fingerprint": prefix.short_fingerprint,
        "changes": len(cache._fingerprint_history),
        "total": 1,
        "hit_rate_pct": 100.0,  # prefix itself doesn't track hits; this means "stable"
    }


def _estimate_usd_saved(hit_tokens: int) -> float:
    """Estimate USD saved from cache hits (DeepSeek v4-flash pricing).

    Cached input: ~$0.014/M tokens.  Uncached input: ~$0.14/M tokens.
    Difference: ~$0.126/M tokens saved per cache hit.

    These numbers drift with provider pricing; this is a rough estimate.
    """
    SAVINGS_PER_1M = 0.126  # USD per 1M tokens (cache hit vs miss)
    return round(hit_tokens / 1_000_000 * SAVINGS_PER_1M, 4)


def _empty_stats() -> dict:
    return {
        "hits": 0, "misses": 0, "calls": 0,
        "hit_rate_pct": 0.0, "input_tokens": 0, "output_tokens": 0,
        "estimated_usd_saved": 0.0, "turn_history": [],
    }
