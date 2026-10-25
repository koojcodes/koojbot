"""
Zep Graph Search Tools.

Provides three tiers of graph search for the knowledge graph:
1. InsightForge — deep hybrid search with auto-generated sub-queries
2. PanoramaSearch — broad overview including expired/historical content
3. QuickSearch — fast semantic search for single queries

These are used by the ResearcherAgent (as a Claude tool) and the
future Deep Analysis Agent (ReACT pattern).

Adapted from MiroFish backend/app/services/zep_tools.py.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from zep_cloud.client import Zep

from bot.config import settings
from bot.utils.logger import log

from .zep_paging import fetch_all_nodes, fetch_all_edges


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """Simple search result with facts, edges, and nodes."""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count,
        }

    def to_text(self) -> str:
        """Format for LLM consumption."""
        parts = [f"Search query: {self.query}", f"Found {self.total_count} relevant items"]
        if self.facts:
            parts.append("\n### Relevant facts:")
            for i, fact in enumerate(self.facts, 1):
                parts.append(f"{i}. {fact}")
        return "\n".join(parts)


@dataclass
class NodeInfo:
    """Graph node information."""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
        }

    def to_text(self) -> str:
        entity_type = next(
            (label for label in self.labels if label not in ("Entity", "Node")),
            "Unknown",
        )
        return f"Entity: {self.name} (type: {entity_type})\nSummary: {self.summary}"


@dataclass
class EdgeInfo:
    """Graph edge (relationship) information."""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
        }

    def to_text(self, include_temporal: bool = False) -> str:
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        text = f"Relation: {source} --[{self.name}]--> {target}\nFact: {self.fact}"
        if include_temporal and self.valid_at:
            text += f"\nValid: {self.valid_at} - {self.invalid_at or 'present'}"
        return text

    @property
    def is_expired(self) -> bool:
        return self.expired_at is not None


@dataclass
class InsightForgeResult:
    """Deep multi-query search result."""
    query: str
    sub_queries: List[str]
    semantic_facts: List[str] = field(default_factory=list)
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)
    relationship_chains: List[str] = field(default_factory=list)
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships,
        }

    def to_text(self) -> str:
        parts = [
            "## Deep Analysis Results",
            f"Query: {self.query}",
            f"\n### Statistics",
            f"- Relevant facts: {self.total_facts}",
            f"- Entities involved: {self.total_entities}",
            f"- Relationship chains: {self.total_relationships}",
        ]
        if self.sub_queries:
            parts.append("\n### Sub-queries analyzed")
            for i, sq in enumerate(self.sub_queries, 1):
                parts.append(f"{i}. {sq}")
        if self.semantic_facts:
            parts.append("\n### Key facts")
            for i, fact in enumerate(self.semantic_facts, 1):
                parts.append(f'{i}. "{fact}"')
        if self.entity_insights:
            parts.append("\n### Key entities")
            for entity in self.entity_insights:
                parts.append(f"- **{entity.get('name', 'Unknown')}** ({entity.get('type', 'entity')})")
                if entity.get("summary"):
                    parts.append(f"  Summary: {entity['summary']}")
        if self.relationship_chains:
            parts.append("\n### Relationship chains")
            for chain in self.relationship_chains:
                parts.append(f"- {chain}")
        return "\n".join(parts)


@dataclass
class PanoramaResult:
    """Broad search result including historical/expired content."""
    query: str
    all_nodes: List[NodeInfo] = field(default_factory=list)
    all_edges: List[EdgeInfo] = field(default_factory=list)
    active_facts: List[str] = field(default_factory=list)
    historical_facts: List[str] = field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count,
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
        }

    def to_text(self) -> str:
        parts = [
            "## Panorama Search Results",
            f"Query: {self.query}",
            f"\n### Statistics",
            f"- Total nodes: {self.total_nodes}",
            f"- Total edges: {self.total_edges}",
            f"- Active facts: {self.active_count}",
            f"- Historical facts: {self.historical_count}",
        ]
        if self.active_facts:
            parts.append("\n### Active facts")
            for i, fact in enumerate(self.active_facts, 1):
                parts.append(f'{i}. "{fact}"')
        if self.historical_facts:
            parts.append("\n### Historical facts")
            for i, fact in enumerate(self.historical_facts, 1):
                parts.append(f'{i}. "{fact}"')
        return "\n".join(parts)


# ── Main service ────────────────────────────────────────────────────

class ZepToolsService:
    """
    Zep graph search service.

    Core tools:
    - search_graph / quick_search — fast semantic search
    - insight_forge — deep multi-query analysis
    - panorama_search — broad overview with historical data
    - get_graph_statistics — network-level stats

    Base tools:
    - get_all_nodes / get_all_edges
    - get_node_detail / get_node_edges
    - get_entities_by_type
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        llm_client: Optional[Any] = None,
    ):
        self.api_key = api_key or settings.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY not configured")
        self.client = Zep(api_key=self.api_key)
        self._llm_client = llm_client
        log.info("[ZEP] Tools service initialized")

    @property
    def llm(self):
        """Lazy-init the OpenAI-format LLM client for sub-query generation."""
        if self._llm_client is None:
            from .llm_client import SwarmLLMClient
            self._llm_client = SwarmLLMClient()
        return self._llm_client

    def _call_with_retry(self, func, operation_name: str, max_retries: int | None = None):
        """API call with exponential-backoff retry."""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY

        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    log.warning(
                        f"[ZEP] {operation_name} attempt {attempt + 1} failed: "
                        f"{str(e)[:100]}, retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    log.error(f"[ZEP] {operation_name} failed after {max_retries} attempts: {e}")
        raise last_exception

    # ── Quick Search (used by ResearcherAgent tool) ─────────────────

    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ) -> SearchResult:
        """
        Fast semantic + BM25 hybrid search on the graph.

        Falls back to local keyword matching if the Zep search API
        is unavailable.
        """
        log.info(f"[ZEP] Quick search: '{query[:50]}' on graph {graph_id}")

        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder",
                ),
                operation_name=f"graph search ({graph_id})",
            )

            facts: List[str] = []
            edges: List[Dict[str, Any]] = []
            nodes: List[Dict[str, Any]] = []

            if hasattr(search_results, "edges") and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, "fact") and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, "uuid_", None) or getattr(edge, "uuid", ""),
                        "name": getattr(edge, "name", ""),
                        "fact": getattr(edge, "fact", ""),
                    })

            if hasattr(search_results, "nodes") and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, "uuid_", None) or getattr(node, "uuid", ""),
                        "name": getattr(node, "name", ""),
                        "labels": getattr(node, "labels", []),
                        "summary": getattr(node, "summary", ""),
                    })
                    if hasattr(node, "summary") and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")

            log.info(f"[ZEP] Quick search found {len(facts)} facts")
            return SearchResult(
                facts=facts, edges=edges, nodes=nodes,
                query=query, total_count=len(facts),
            )

        except Exception as e:
            log.warning(f"[ZEP] Search API fallback to local: {e}")
            return self._local_search(graph_id, query, limit, scope)

    def _local_search(
        self, graph_id: str, query: str, limit: int = 10, scope: str = "edges",
    ) -> SearchResult:
        """Fallback: keyword matching on all edges/nodes."""
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.split() if len(w.strip()) > 1]

        def match_score(text: str) -> int:
            if not text:
                return 0
            text_lower = text.lower()
            if query_lower in text_lower:
                return 100
            return sum(10 for kw in keywords if kw in text_lower)

        facts: List[str] = []
        edges_out: List[Dict[str, Any]] = []
        nodes_out: List[Dict[str, Any]] = []

        try:
            if scope in ("edges", "both"):
                all_edges = self.get_all_edges(graph_id)
                scored = [(match_score(e.fact) + match_score(e.name), e) for e in all_edges]
                scored = [(s, e) for s, e in scored if s > 0]
                scored.sort(key=lambda x: x[0], reverse=True)
                for _, edge in scored[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_out.append(edge.to_dict())

            if scope in ("nodes", "both"):
                all_nodes = self.get_all_nodes(graph_id)
                scored = [(match_score(n.name) + match_score(n.summary), n) for n in all_nodes]
                scored = [(s, n) for s, n in scored if s > 0]
                scored.sort(key=lambda x: x[0], reverse=True)
                for _, node in scored[:limit]:
                    nodes_out.append(node.to_dict())
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")

        except Exception as e:
            log.error(f"[ZEP] Local search failed: {e}")

        return SearchResult(
            facts=facts, edges=edges_out, nodes=nodes_out,
            query=query, total_count=len(facts),
        )

    # ── InsightForge (deep multi-query search) ──────────────────────

    def insight_forge(
        self,
        graph_id: str,
        query: str,
        num_sub_queries: int = 3,
    ) -> InsightForgeResult:
        """
        Deep analysis: auto-generate sub-queries and search multiple dimensions.

        Uses the swarm LLM to decompose the query into sub-questions,
        then runs semantic search + entity lookup for each.
        """
        log.info(f"[ZEP] InsightForge: '{query[:50]}'")

        # Generate sub-queries via LLM
        sub_queries = self._generate_sub_queries(query, num_sub_queries)

        all_facts: List[str] = []
        entity_insights: List[Dict[str, Any]] = []
        relationship_chains: List[str] = []
        seen_facts: set[str] = set()

        # Search each sub-query
        for sq in [query] + sub_queries:
            result = self.quick_search(graph_id, sq, limit=8)
            for fact in result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)

        # Get top entities
        all_nodes = self.get_all_nodes(graph_id)
        for node in all_nodes[:20]:
            query_lower = query.lower()
            if (
                query_lower in node.name.lower()
                or query_lower in node.summary.lower()
                or any(sq.lower() in node.name.lower() for sq in sub_queries)
            ):
                entity_insights.append({
                    "name": node.name,
                    "type": next(
                        (l for l in node.labels if l not in ("Entity", "Node")),
                        "Unknown",
                    ),
                    "summary": node.summary,
                })

        # Build relationship chains from edges
        all_edges = self.get_all_edges(graph_id)
        node_map = {n.uuid: n.name for n in all_nodes}
        for edge in all_edges[:30]:
            source_name = node_map.get(edge.source_node_uuid, edge.source_node_uuid[:8])
            target_name = node_map.get(edge.target_node_uuid, edge.target_node_uuid[:8])
            if edge.fact and any(
                kw in edge.fact.lower() for kw in query.lower().split()
            ):
                relationship_chains.append(
                    f"{source_name} --[{edge.name}]--> {target_name}: {edge.fact}"
                )

        return InsightForgeResult(
            query=query,
            sub_queries=sub_queries,
            semantic_facts=all_facts[:30],
            entity_insights=entity_insights[:10],
            relationship_chains=relationship_chains[:15],
            total_facts=len(all_facts),
            total_entities=len(entity_insights),
            total_relationships=len(relationship_chains),
        )

    def _generate_sub_queries(self, query: str, num: int = 3) -> List[str]:
        """Use the swarm LLM to decompose a query into sub-questions."""
        try:
            result = self.llm.chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a research assistant. Given a trading/market query, "
                            "generate sub-questions that would help analyze it from multiple angles. "
                            f"Return exactly {num} sub-questions as a JSON array under key 'sub_queries'."
                        ),
                    },
                    {"role": "user", "content": f"Query: {query}"},
                ],
                temperature=0.3,
                max_tokens=500,
            )
            return result.get("sub_queries", [])[:num]
        except Exception as e:
            log.warning(f"[ZEP] Sub-query generation failed: {e}")
            return []

    # ── Panorama Search (broad overview) ────────────────────────────

    def panorama_search(self, graph_id: str, query: str) -> PanoramaResult:
        """
        Broad search returning all nodes/edges with active vs historical split.
        """
        log.info(f"[ZEP] Panorama search: '{query[:50]}'")

        all_nodes = self.get_all_nodes(graph_id)
        all_edges = self.get_all_edges(graph_id, include_temporal=True)

        active_facts: List[str] = []
        historical_facts: List[str] = []

        for edge in all_edges:
            if edge.fact:
                if edge.is_expired:
                    historical_facts.append(edge.fact)
                else:
                    active_facts.append(edge.fact)

        return PanoramaResult(
            query=query,
            all_nodes=all_nodes,
            all_edges=all_edges,
            active_facts=active_facts,
            historical_facts=historical_facts,
            total_nodes=len(all_nodes),
            total_edges=len(all_edges),
            active_count=len(active_facts),
            historical_count=len(historical_facts),
        )

    # ── Base node/edge accessors ────────────────────────────────────

    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """Get all nodes in a graph (paginated)."""
        nodes = fetch_all_nodes(self.client, graph_id)
        return [
            NodeInfo(
                uuid=str(getattr(n, "uuid_", None) or getattr(n, "uuid", "")),
                name=n.name or "",
                labels=n.labels or [],
                summary=n.summary or "",
                attributes=n.attributes or {},
            )
            for n in nodes
        ]

    def get_all_edges(
        self, graph_id: str, include_temporal: bool = True,
    ) -> List[EdgeInfo]:
        """Get all edges in a graph (paginated), optionally with temporal info."""
        edges = fetch_all_edges(self.client, graph_id)
        result = []
        for e in edges:
            info = EdgeInfo(
                uuid=str(getattr(e, "uuid_", None) or getattr(e, "uuid", "")),
                name=e.name or "",
                fact=e.fact or "",
                source_node_uuid=e.source_node_uuid or "",
                target_node_uuid=e.target_node_uuid or "",
            )
            if include_temporal:
                info.created_at = getattr(e, "created_at", None)
                info.valid_at = getattr(e, "valid_at", None)
                info.invalid_at = getattr(e, "invalid_at", None)
                info.expired_at = getattr(e, "expired_at", None)
            result.append(info)
        return result

    def get_entities_by_type(self, graph_id: str, entity_type: str) -> List[NodeInfo]:
        """Filter nodes by entity type label."""
        return [n for n in self.get_all_nodes(graph_id) if entity_type in n.labels]

    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """Get graph-level statistics (node/edge counts, type distributions)."""
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)

        entity_types: Dict[str, int] = {}
        for node in nodes:
            for label in node.labels:
                if label not in ("Entity", "Node"):
                    entity_types[label] = entity_types.get(label, 0) + 1

        relation_types: Dict[str, int] = {}
        for edge in edges:
            if edge.name:
                relation_types[edge.name] = relation_types.get(edge.name, 0) + 1

        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types,
            "expired_edges": sum(1 for e in edges if e.is_expired),
        }
