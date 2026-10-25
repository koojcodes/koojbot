"""
Knowledge Graph Builder Service.

Wraps Zep Cloud's Standalone Graph API to build and manage knowledge graphs
for Max the Trader. Handles graph creation, ontology setup, text chunking,
episode ingestion, and status polling.

Adapted from MiroFish backend/app/services/graph_builder.py.
Flask TaskManager replaced with simple callback-based progress reporting.
Threading replaced with async wrappers (see async_wrappers.py).
"""
from __future__ import annotations

import json
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from zep_cloud import EpisodeData, EntityEdgeSourceTarget
from zep_cloud.client import Zep

from bot.config import settings
from bot.utils.logger import log

from .text_processor import TextProcessor
from .zep_paging import fetch_all_nodes, fetch_all_edges

# Persistent graph state file
_GRAPH_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "knowledge_store" / "graph_state.json"

ProgressCallback = Optional[Callable[[str, float], None]]


@dataclass
class GraphInfo:
    """Summary of a built knowledge graph."""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


class GraphBuilderService:
    """
    Builds and manages Zep knowledge graphs.

    Usage:
        builder = GraphBuilderService()
        graph_id = builder.get_or_create_graph("MaxTrader KB")
        builder.add_episodes(graph_id, text, ontology)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ZEP_API_KEY
        if not self.api_key:
            raise ValueError(
                "ZEP_API_KEY not configured. Set it in .env to use knowledge graph features."
            )
        self.client = Zep(api_key=self.api_key)

    # ── Graph lifecycle ─────────────────────────────────────────────

    def get_or_create_graph(self, name: str = "MaxTrader Knowledge Graph") -> str:
        """Return the persisted graph ID, or create a new graph and persist it."""
        state = self._load_graph_state()
        if state.get("graph_id"):
            log.info(f"[GRAPH] Using existing graph: {state['graph_id']}")
            return state["graph_id"]

        graph_id = self.create_graph(name)
        self._save_graph_state({"graph_id": graph_id, "name": name})
        return graph_id

    def create_graph(self, name: str) -> str:
        """Create a new Zep standalone graph."""
        graph_id = f"maxtrader_{uuid.uuid4().hex[:16]}"
        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description="Max the Trader knowledge graph — trading rules, market entities, and relationships",
        )
        log.info(f"[GRAPH] Created new graph: {graph_id}")
        return graph_id

    def delete_graph(self, graph_id: str) -> None:
        """Delete a graph and clear persisted state."""
        self.client.graph.delete(graph_id=graph_id)
        self._save_graph_state({})
        log.info(f"[GRAPH] Deleted graph: {graph_id}")

    # ── Ontology ────────────────────────────────────────────────────

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        """Apply an ontology definition (entity types + edge types) to a graph."""
        from pydantic import Field
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

        warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

        RESERVED_NAMES = {"uuid", "name", "group_id", "name_embedding", "summary", "created_at"}

        def safe_attr_name(attr_name: str) -> str:
            if attr_name.lower() in RESERVED_NAMES:
                return f"entity_{attr_name}"
            return attr_name

        # Build entity type classes dynamically
        entity_types = {}
        for entity_def in ontology.get("entity_types", []):
            ename = entity_def["name"]
            description = entity_def.get("description", f"A {ename} entity.")

            attrs: Dict[str, Any] = {"__doc__": description}
            annotations: Dict[str, Any] = {}

            for attr_def in entity_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])
                attr_desc = attr_def.get("description", attr_name)
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[EntityText]

            attrs["__annotations__"] = annotations
            entity_class = type(ename, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[ename] = entity_class

        # Build edge type classes dynamically
        edge_definitions = {}
        for edge_def in ontology.get("edge_types", []):
            ename = edge_def["name"]
            description = edge_def.get("description", f"A {ename} relationship.")

            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in edge_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])
                attr_desc = attr_def.get("description", attr_name)
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]

            attrs["__annotations__"] = annotations
            class_name = "".join(word.capitalize() for word in ename.split("_"))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description

            source_targets = []
            for st in edge_def.get("source_targets", []):
                source_targets.append(
                    EntityEdgeSourceTarget(
                        source=st.get("source", "Entity"),
                        target=st.get("target", "Entity"),
                    )
                )

            if source_targets:
                edge_definitions[ename] = (edge_class, source_targets)

        if entity_types or edge_definitions:
            self.client.graph.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types if entity_types else None,
                edges=edge_definitions if edge_definitions else None,
            )
            log.info(
                f"[GRAPH] Ontology set: {len(entity_types)} entity types, "
                f"{len(edge_definitions)} edge types"
            )

    # ── Episode ingestion ───────────────────────────────────────────

    def add_episodes(
        self,
        graph_id: str,
        text: str,
        ontology: Optional[Dict[str, Any]] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3,
        progress_callback: ProgressCallback = None,
    ) -> List[str]:
        """
        Chunk text and add episodes to the graph.

        If ontology is provided and the graph doesn't have one yet,
        it will be applied first.

        Returns list of episode UUIDs.
        """
        # Optionally set ontology
        if ontology:
            self.set_ontology(graph_id, ontology)

        # Chunk text
        chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
        if not chunks:
            log.warning("[GRAPH] No text chunks to ingest")
            return []

        log.info(f"[GRAPH] Ingesting {len(chunks)} chunks into graph {graph_id}")

        episode_uuids: List[str] = []
        total_chunks = len(chunks)

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size

            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    f"Sending batch {batch_num}/{total_batches} ({len(batch_chunks)} chunks)",
                    progress,
                )

            episodes = [EpisodeData(data=chunk, type="text") for chunk in batch_chunks]

            try:
                batch_result = self.client.graph.add_batch(
                    graph_id=graph_id, episodes=episodes
                )

                if batch_result and isinstance(batch_result, list):
                    for ep in batch_result:
                        ep_uuid = getattr(ep, "uuid_", None) or getattr(ep, "uuid", None)
                        if ep_uuid:
                            episode_uuids.append(ep_uuid)

                # Brief pause to avoid rate limiting
                time.sleep(1)

            except Exception as e:
                log.error(f"[GRAPH] Batch {batch_num} failed: {e}")
                raise

        log.info(f"[GRAPH] Ingested {len(episode_uuids)} episodes")

        # Wait for Zep to process
        if episode_uuids:
            self._wait_for_episodes(episode_uuids, progress_callback)

        return episode_uuids

    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: ProgressCallback = None,
        timeout: int = 300,
    ) -> None:
        """Poll until all episodes are processed or timeout."""
        if not episode_uuids:
            return

        start_time = time.time()
        pending = set(episode_uuids)
        completed = 0
        total = len(episode_uuids)

        log.info(f"[GRAPH] Waiting for {total} episodes to process...")

        while pending:
            if time.time() - start_time > timeout:
                log.warning(
                    f"[GRAPH] Timeout waiting for episodes: "
                    f"{completed}/{total} completed"
                )
                break

            for ep_uuid in list(pending):
                try:
                    episode = self.client.graph.episode.get(uuid_=ep_uuid)
                    if getattr(episode, "processed", False):
                        pending.remove(ep_uuid)
                        completed += 1
                except Exception:
                    pass

            if progress_callback:
                progress_callback(
                    f"Zep processing: {completed}/{total} episodes done",
                    completed / total if total > 0 else 0,
                )

            if pending:
                time.sleep(3)

        log.info(f"[GRAPH] Processing complete: {completed}/{total} episodes")

    # ── Graph info ──────────────────────────────────────────────────

    def get_graph_info(self, graph_id: str) -> GraphInfo:
        """Get summary statistics for a graph."""
        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)

        entity_types: set[str] = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in ("Entity", "Node"):
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types),
        )

    # ── Persistence ─────────────────────────────────────────────────

    @staticmethod
    def _load_graph_state() -> Dict[str, Any]:
        try:
            if _GRAPH_STATE_PATH.exists():
                return json.loads(_GRAPH_STATE_PATH.read_text())
        except Exception:
            pass
        return {}

    @staticmethod
    def _save_graph_state(state: Dict[str, Any]) -> None:
        _GRAPH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GRAPH_STATE_PATH.write_text(json.dumps(state, indent=2))
