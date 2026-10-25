"""
Knowledge base — persistent storage and retrieval of the bot's learned trading knowledge.

One JSON document per category lives in knowledge_store/ at the project root.
Each document is a "living document" that gets updated by the KnowledgeLearnerAgent
whenever a new video is processed.

Document schema:
{
    "category": "entry_setup",
    "version": 7,
    "last_updated": "2025-01-15T10:30:00Z",
    "summary": "2-3 sentence synthesis of everything we know in this category",
    "consolidated_rules": [
        {
            "id": "rule_001",
            "rule": "RSI below 32 on 15m is a strong BTC long entry signal",
            "asset": "BTC",        // or "general"
            "timeframe": "15m",    // or "general"
            "confidence": 0.85,    // 0.0 - 1.0
            "confirmed_by": 3,     // number of sources confirming this
            "status": "confirmed", // "confirmed" | "tentative" | "contradicted"
            "notes": ""
        }
    ],
    "open_contradictions": [
        {
            "description": "Creator A uses RSI 30, Creator B uses RSI 35 — unresolved",
            "added_date": "2025-01-15"
        }
    ],
    "source_videos": [
        {
            "url": "https://youtube.com/watch?v=...",
            "title": "How I Trade BTC 15m",
            "channel": "TraderGareth",
            "processed_at": "2025-01-15T10:30:00Z"
        }
    ]
}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bot.knowledge.categories import (
    CATEGORY_NAMES,
    TRADITIONAL_CATEGORY_NAMES,
    KALSHI_CATEGORY_NAMES,
    AGENT_KNOWLEDGE_PROFILES,
    category_description,
    get_agent_track,
)
from bot.utils.logger import log

# ── Storage paths ──────────────────────────────────────────────────────────────
# Two sub-directories under knowledge_store/:
#   traditional/ — chart patterns, indicators, entries/exits, classic risk mgmt
#   kalshi/      — prediction market edge, crowd pricing, contract mechanics
_STORE_DIR = Path(__file__).parent.parent.parent / "knowledge_store"
_TRAD_DIR  = _STORE_DIR / "traditional"
_KALS_DIR  = _STORE_DIR / "kalshi"
_AGENT_DIR = _STORE_DIR / "agents"

_TRAD_DIR.mkdir(parents=True, exist_ok=True)
_KALS_DIR.mkdir(parents=True, exist_ok=True)
_AGENT_DIR.mkdir(parents=True, exist_ok=True)


def _track_dir(track: str) -> Path:
    return _KALS_DIR if track == "kalshi" else _TRAD_DIR


def _category_path(category: str, track: str = "traditional") -> Path:
    return _track_dir(track) / f"{category}.json"


def _default_agent_for_track(track: str) -> str:
    return "btc15m_kalshi" if track == "kalshi" else "stocks_etf_paper_shell"


def _agent_category_path(category: str, agent_id: str, track: str) -> Path:
    agent_track_dir = _AGENT_DIR / agent_id / track
    agent_track_dir.mkdir(parents=True, exist_ok=True)
    return agent_track_dir / f"{category}.json"


def _registry(track: str) -> list[str]:
    """Return the list of valid category names for a given track."""
    return KALSHI_CATEGORY_NAMES if track == "kalshi" else TRADITIONAL_CATEGORY_NAMES


# ── Empty document template ────────────────────────────────────────────────────

def _empty_document(category: str) -> dict:
    return {
        "category": category,
        "version": 0,
        "last_updated": "",
        "summary": f"No knowledge accumulated yet for {category_description(category)}.",
        "consolidated_rules": [],
        "open_contradictions": [],
        "source_videos": [],
    }


# ── Read / write ───────────────────────────────────────────────────────────────

def load_category(category: str, track: str = "traditional", agent_id: str | None = None) -> dict:
    """Load a knowledge category document. Returns empty template if not yet created."""
    effective_agent = agent_id or _default_agent_for_track(track)
    path = _agent_category_path(category, effective_agent, track)
    if not path.exists():
        # Backward compatibility: read legacy track-level files if agent-scoped files
        # have not been created yet. Only bootstrap the track's default agent.
        if effective_agent == _default_agent_for_track(track):
            legacy = _category_path(category, track)
            if legacy.exists():
                try:
                    with legacy.open() as f:
                        return json.load(f)
                except Exception:
                    pass
        return _empty_document(category)
    try:
        with path.open() as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"[KB:{track}:{effective_agent}] Could not load {category}: {e} — using empty document")
        return _empty_document(category)


def save_category(category: str, data: dict, track: str = "traditional", agent_id: str | None = None) -> None:
    """Write a knowledge category document to disk."""
    effective_agent = agent_id or _default_agent_for_track(track)
    path = _agent_category_path(category, effective_agent, track)
    data["version"] = data.get("version", 0) + 1
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        with path.open("w") as f:
            json.dump(data, f, indent=2, default=str)
        log.info(f"[KB:{track}:{effective_agent}] Saved {category} v{data['version']}")
    except Exception as e:
        log.error(f"[KB:{track}:{effective_agent}] Could not save {category}: {e}")


def load_all(track: str = "traditional", agent_id: str | None = None) -> dict[str, dict]:
    """Load all category documents for a given track."""
    return {cat: load_category(cat, track, agent_id=agent_id) for cat in _registry(track)}


# ── Query helpers ──────────────────────────────────────────────────────────────

def has_processed_video(url: str, agent_id: str | None = None) -> bool:
    """
    Return True if this URL has already been ingested.
    - If agent_id is provided, checks only that agent's track.
    - Otherwise checks all known agent stores and legacy stores.
    """
    if agent_id:
        track = get_agent_track(agent_id)
        for cat in _registry(track):
            doc = load_category(cat, track, agent_id=agent_id)
            for v in doc.get("source_videos", []):
                if v.get("url") == url:
                    return True
        return False

    for aid, profile in AGENT_KNOWLEDGE_PROFILES.items():
        track = profile.get("track", "traditional")
        for cat in _registry(track):
            doc = load_category(cat, track, agent_id=aid)
            for v in doc.get("source_videos", []):
                if v.get("url") == url:
                    return True
    # Legacy fallback
    for track in ("traditional", "kalshi"):
        for cat in _registry(track):
            legacy = _category_path(cat, track)
            if not legacy.exists():
                continue
            try:
                with legacy.open() as f:
                    doc = json.load(f)
                for v in doc.get("source_videos", []):
                    if v.get("url") == url:
                        return True
            except Exception:
                continue
    return False


def get_all_source_videos(agent_id: str | None = None) -> list[dict]:
    """Return a deduplicated list of processed sources (optionally per agent)."""
    seen: set[str] = set()
    videos: list[dict] = []

    if agent_id:
        track = get_agent_track(agent_id)
        candidates = [(agent_id, track)]
    else:
        candidates = [(aid, profile.get("track", "traditional")) for aid, profile in AGENT_KNOWLEDGE_PROFILES.items()]

    for aid, track in candidates:
        for cat in _registry(track):
            doc = load_category(cat, track, agent_id=aid)
            for v in doc.get("source_videos", []):
                if v.get("url") not in seen:
                    seen.add(v.get("url", ""))
                    videos.append(v)
    return videos


def count_total_rules(agent_id: str | None = None) -> int:
    """Count total consolidated rules (optionally per agent)."""
    total = 0
    if agent_id:
        track = get_agent_track(agent_id)
        candidates = [(agent_id, track)]
    else:
        candidates = [(aid, profile.get("track", "traditional")) for aid, profile in AGENT_KNOWLEDGE_PROFILES.items()]

    for aid, track in candidates:
        for cat in _registry(track):
            doc = load_category(cat, track, agent_id=aid)
            total += len(doc.get("consolidated_rules", []))
    return total


# ── Apply updates from KnowledgeLearnerAgent ──────────────────────────────────

def apply_updates(
    updates: list[dict],
    source_url: str,
    video_title: str,
    channel_name: str,
    track: str = "traditional",
    agent_id: str | None = None,
) -> dict:
    """
    Apply a list of structured updates (from KnowledgeLearnerAgent) to the knowledge base.

    Each update has the form:
        {
            "category": "entry_setup",
            "action": "ADD" | "CONFIRM" | "UPDATE" | "CONTRADICT",
            "rule": "<rule text>",           # for ADD / UPDATE / CONTRADICT
            "rule_id": "rule_001",           # for CONFIRM / UPDATE / CONTRADICT
            "asset": "BTC",                  # or "general"
            "timeframe": "15m",              # or "general"
            "confidence": 0.75,
            "reasoning": "<why this update>"
        }

    Returns a learning report dict.
    """
    source_entry = {
        "url": source_url,
        "title": video_title,
        "channel": channel_name,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Group updates by category — only accept valid categories for this track
    valid_cats = set(_registry(track))
    by_category: dict[str, list[dict]] = {}
    for u in updates:
        cat = u.get("category", "")
        if cat not in valid_cats:
            continue
        by_category.setdefault(cat, []).append(u)

    confirmed_count = 0
    new_count = 0
    contradiction_count = 0
    updated_count = 0
    categories_touched: list[str] = []

    for category, cat_updates in by_category.items():
        doc = load_category(category, track, agent_id=agent_id)
        rules: list[dict] = doc.get("consolidated_rules", [])
        contradictions: list[dict] = doc.get("open_contradictions", [])

        # Build a rule_id → index map for fast lookup
        rule_index: dict[str, int] = {r["id"]: i for i, r in enumerate(rules)}
        next_rule_num = len(rules) + 1

        for u in cat_updates:
            action = u.get("action", "ADD").upper()
            reasoning = u.get("reasoning", "")

            if action == "ADD":
                rule_id = f"rule_{next_rule_num:03d}"
                new_rule = {
                    "id": rule_id,
                    "rule": u.get("rule", ""),
                    "asset": u.get("asset", "general"),
                    "timeframe": u.get("timeframe", "general"),
                    "confidence": float(u.get("confidence", 0.6)),
                    "confirmed_by": 1,
                    "status": "tentative",
                    "notes": reasoning,
                }
                rules.append(new_rule)
                rule_index[rule_id] = len(rules) - 1
                next_rule_num += 1
                new_count += 1
                log.info(f"[KB] {category}: ADD {rule_id} — {u.get('rule', '')[:60]}")

            elif action == "CONFIRM":
                rid = u.get("rule_id", "")
                if rid in rule_index:
                    idx = rule_index[rid]
                    rules[idx]["confirmed_by"] = rules[idx].get("confirmed_by", 1) + 1
                    # Promote from tentative if confirmed by multiple sources
                    if rules[idx]["confirmed_by"] >= 2:
                        rules[idx]["status"] = "confirmed"
                    # Boost confidence slightly on confirmation
                    rules[idx]["confidence"] = min(
                        0.95,
                        rules[idx].get("confidence", 0.6) + 0.05
                    )
                    confirmed_count += 1
                    log.info(f"[KB] {category}: CONFIRM {rid}")

            elif action == "UPDATE":
                rid = u.get("rule_id", "")
                if rid in rule_index:
                    idx = rule_index[rid]
                    if u.get("rule"):
                        rules[idx]["rule"] = u["rule"]
                    if u.get("confidence"):
                        rules[idx]["confidence"] = float(u["confidence"])
                    rules[idx]["notes"] = reasoning
                    updated_count += 1
                    log.info(f"[KB] {category}: UPDATE {rid}")

            elif action == "CONTRADICT":
                rid = u.get("rule_id", "")
                contradiction_count += 1
                # Add to open contradictions for human/AI review
                contradiction_entry = {
                    "description": u.get("reasoning", "Contradiction detected"),
                    "existing_rule_id": rid,
                    "conflicting_rule": u.get("rule", ""),
                    "added_date": datetime.now(timezone.utc).date().isoformat(),
                    "source": source_url,
                }
                contradictions.append(contradiction_entry)
                # Mark the existing rule as "contradicted" if confidence drops
                if rid in rule_index:
                    idx = rule_index[rid]
                    rules[idx]["confidence"] = max(
                        0.1,
                        rules[idx].get("confidence", 0.6) - 0.10
                    )
                    rules[idx]["status"] = "contradicted"
                log.info(f"[KB] {category}: CONTRADICT {rid} — {u.get('reasoning', '')[:60]}")

        # Register source video in this category (if not already present)
        existing_urls = {v.get("url") for v in doc.get("source_videos", [])}
        if source_url not in existing_urls:
            doc.setdefault("source_videos", []).append(source_entry)

        # Write updates back
        doc["consolidated_rules"] = rules
        doc["open_contradictions"] = contradictions
        save_category(category, doc, track, agent_id=agent_id)
        categories_touched.append(category)

    return {
        "new_rules": new_count,
        "confirmed_rules": confirmed_count,
        "updated_rules": updated_count,
        "contradictions": contradiction_count,
        "categories_touched": categories_touched,
        "source_url": source_url,
        "video_title": video_title,
        "channel_name": channel_name,
        "agent_id": agent_id or _default_agent_for_track(track),
    }


# ── Context formatting for injection into agents ──────────────────────────────

def format_for_prompt(
    categories: list[str] | None = None,
    max_rules_per_cat: int = 8,
    track: str = "traditional",
    agent_id: str | None = None,
) -> str:
    """
    Format the knowledge base as a compact text block for injection into
    Claude prompts. Only includes confirmed/tentative rules (not contradicted).

    categories: limit to specific categories (None = all for the track)
    max_rules_per_cat: max rules per category to include (avoids token bloat)
    track: "traditional" | "kalshi"
    """
    cats = categories or _registry(track)
    header = (
        "═══ KALSHI PREDICTION MARKET KNOWLEDGE BASE ═══"
        if track == "kalshi"
        else "═══ ACCUMULATED TRADING KNOWLEDGE BASE ═══"
    )
    if agent_id:
        header = f"{header}\nAgent: {agent_id}"
    lines = [header]
    has_any_rules = False

    for cat in cats:
        doc = load_category(cat, track, agent_id=agent_id)
        rules = [
            r for r in doc.get("consolidated_rules", [])
            if r.get("status") != "contradicted"
        ]
        if not rules:
            continue

        has_any_rules = True
        lines.append(f"\n[{cat.upper().replace('_', ' ')}] (v{doc.get('version', 0)}, "
                     f"{len(rules)} rules)")

        # Summary if present and meaningful
        summary = doc.get("summary", "")
        if summary and "No knowledge accumulated" not in summary:
            lines.append(f"  Summary: {summary}")

        # Top rules (sorted by confidence desc)
        top_rules = sorted(rules, key=lambda r: -r.get("confidence", 0))[:max_rules_per_cat]
        for r in top_rules:
            asset = r.get("asset", "general")
            tf = r.get("timeframe", "general")
            conf = r.get("confidence", 0.5)
            status_tag = "✅" if r.get("status") == "confirmed" else "🔸"
            scope = f"[{asset}/{tf}]" if asset != "general" or tf != "general" else ""
            lines.append(
                f"  {status_tag} {scope} {r['rule']} "
                f"(conf: {conf:.0%}, confirmed by {r.get('confirmed_by', 1)} source(s))"
            )

        # Open contradictions
        if doc.get("open_contradictions"):
            lines.append(f"  ⚠️ Open contradictions: {len(doc['open_contradictions'])}")

    if not has_any_rules:
        return "KNOWLEDGE BASE: Empty — no trading knowledge accumulated yet."

    lines.append("\n═══════════════════════════════════════════")
    return "\n".join(lines)


def format_relevant_context(
    asset: str = "BTC",
    timeframe: str = "15m",
    track: str = "kalshi",
    agent_id: str | None = "btc15m_kalshi",
) -> str:
    """
    Return a focused knowledge base context string for a specific asset/timeframe.
    Used by DayTraderResearcher to inject relevant learned rules into its prompt.
    Defaults to track="kalshi" since this function is called by the Kalshi pipeline.
    """
    label = (
        f"KALSHI PREDICTION MARKET RULES (BTC {timeframe}):"
        if track == "kalshi"
        else f"LEARNED TRADING RULES (relevant to {asset} {timeframe}):"
    )
    lines = [label]
    found_any = False

    for cat in _registry(track):
        doc = load_category(cat, track, agent_id=agent_id)
        rules = doc.get("consolidated_rules", [])
        relevant = [
            r for r in rules
            if r.get("status") != "contradicted"
            and (
                r.get("asset", "general") in ("general", asset)
                and r.get("timeframe", "general") in ("general", timeframe, "general")
            )
        ]
        if not relevant:
            continue

        found_any = True
        lines.append(f"\n{cat.replace('_', ' ').title()}:")
        for r in sorted(relevant, key=lambda x: -x.get("confidence", 0))[:5]:
            conf = r.get("confidence", 0.5)
            status = "✅" if r.get("status") == "confirmed" else "🔸"
            lines.append(f"  {status} {r['rule']} (conf: {conf:.0%})")

    if not found_any:
        return ""

    return "\n".join(lines)


def summarize_agent_knowledge(agent_id: str) -> dict:
    """Return a compact summary of knowledge for one agent."""
    track = get_agent_track(agent_id)
    categories = _registry(track)
    total_rules = 0
    contradictions = 0
    per_category: list[dict] = []
    for cat in categories:
        doc = load_category(cat, track, agent_id=agent_id)
        rules = doc.get("consolidated_rules", [])
        total_rules += len(rules)
        contradictions += len(doc.get("open_contradictions", []))
        per_category.append(
            {
                "category": cat,
                "rules": len(rules),
                "confirmed": sum(1 for r in rules if r.get("status") == "confirmed"),
                "tentative": sum(1 for r in rules if r.get("status") == "tentative"),
            }
        )
    return {
        "agent_id": agent_id,
        "track": track,
        "total_rules": total_rules,
        "contradictions": contradictions,
        "sources": len(get_all_source_videos(agent_id=agent_id)),
        "categories": per_category,
    }
