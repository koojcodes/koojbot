"""
Async wrappers for synchronous Zep/graph operations.

All Zep Cloud SDK calls are synchronous. These wrappers use
asyncio.to_thread() to run them without blocking Max's async event loop.

Usage:
    from bot.swarm.async_wrappers import SwarmGraphAsync

    swarm = SwarmGraphAsync()
    graph_id = await swarm.get_or_create_graph()
    await swarm.add_episodes(graph_id, text)
    result = await swarm.quick_search(graph_id, "BTC momentum")
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from bot.config import settings
from bot.utils.logger import log


class SwarmGraphAsync:
    """
    Async facade over GraphBuilderService and ZepToolsService.

    Lazily initializes the underlying services on first use,
    so importing this module doesn't fail if ZEP_API_KEY is not set.
    """

    def __init__(self):
        self._builder = None
        self._tools = None
        self._graph_id: Optional[str] = None
        self._search_cache: Dict[str, tuple[float, Any]] = {}
        self._cache_ttl = 60.0  # seconds

    def _ensure_builder(self):
        if self._builder is None:
            from .graph_builder import GraphBuilderService
            self._builder = GraphBuilderService()

    def _ensure_tools(self):
        if self._tools is None:
            from .zep_tools import ZepToolsService
            self._tools = ZepToolsService()

    # ── Graph lifecycle ─────────────────────────────────────────────

    async def get_or_create_graph(self, name: str = "MaxTrader Knowledge Graph") -> str:
        """Get the persisted graph ID or create a new one."""
        self._ensure_builder()
        graph_id = await asyncio.to_thread(self._builder.get_or_create_graph, name)
        self._graph_id = graph_id
        return graph_id

    async def get_graph_id(self) -> Optional[str]:
        """Return the current graph ID, loading from state if needed."""
        if self._graph_id:
            return self._graph_id
        self._ensure_builder()
        from .graph_builder import GraphBuilderService
        state = GraphBuilderService._load_graph_state()
        self._graph_id = state.get("graph_id")
        return self._graph_id

    # ── Ontology ────────────────────────────────────────────────────

    async def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        """Apply ontology to a graph."""
        self._ensure_builder()
        await asyncio.to_thread(self._builder.set_ontology, graph_id, ontology)

    # ── Episode ingestion ───────────────────────────────────────────

    async def add_episodes(
        self,
        graph_id: str,
        text: str,
        ontology: Optional[Dict[str, Any]] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3,
    ) -> List[str]:
        """Chunk text and add episodes to the graph (runs in thread)."""
        self._ensure_builder()
        return await asyncio.to_thread(
            self._builder.add_episodes,
            graph_id, text, ontology, chunk_size, chunk_overlap, batch_size,
        )

    # ── Search (with caching) ──────────────────────────────────────

    async def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> Any:
        """
        Fast semantic search with 60s cache TTL.

        Caching prevents the 30-second trading loop from hammering Zep
        with redundant queries.
        """
        cache_key = f"{graph_id}:{query}:{limit}"
        now = time.time()

        # Check cache
        if cache_key in self._search_cache:
            cached_time, cached_result = self._search_cache[cache_key]
            if now - cached_time < self._cache_ttl:
                return cached_result

        self._ensure_tools()
        result = await asyncio.to_thread(
            self._tools.quick_search, graph_id, query, limit,
        )

        # Update cache
        self._search_cache[cache_key] = (now, result)

        # Evict stale entries
        self._search_cache = {
            k: (t, v)
            for k, (t, v) in self._search_cache.items()
            if now - t < self._cache_ttl * 2
        }

        return result

    async def insight_forge(
        self,
        graph_id: str,
        query: str,
        num_sub_queries: int = 3,
    ) -> Any:
        """Deep multi-query analysis (no caching — used on-demand only)."""
        self._ensure_tools()
        return await asyncio.to_thread(
            self._tools.insight_forge, graph_id, query, num_sub_queries,
        )

    async def panorama_search(self, graph_id: str, query: str) -> Any:
        """Broad overview search."""
        self._ensure_tools()
        return await asyncio.to_thread(
            self._tools.panorama_search, graph_id, query,
        )

    # ── Graph info ──────────────────────────────────────────────────

    async def get_graph_info(self, graph_id: str) -> Any:
        """Get graph summary statistics."""
        self._ensure_builder()
        return await asyncio.to_thread(self._builder.get_graph_info, graph_id)

    async def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """Get detailed graph statistics."""
        self._ensure_tools()
        return await asyncio.to_thread(self._tools.get_graph_statistics, graph_id)
