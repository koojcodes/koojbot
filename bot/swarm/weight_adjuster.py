"""
Trader Archetype Weight Adjuster — tunes influence_weight based on prediction accuracy.

The adjuster reads from the accuracy_tracker's archetype stats and modifies
the trader profiles' influence_weight values. Better-performing archetypes
get more weight; worse-performing ones get less.

Adjustments are bounded:
  - Minimum weight floor: 0.10 (never completely silence an archetype)
  - Maximum weight ceiling: 0.99
  - Maximum single adjustment: ±0.15 from baseline
  - Minimum predictions required: 5 (don't adjust with too little data)

The adjustment formula:
  new_weight = baseline_weight + (accuracy - 0.5) * adjustment_scale

Where:
  - baseline_weight is the original hardcoded weight
  - accuracy is the archetype's historical accuracy (0-1)
  - adjustment_scale controls how aggressively to adjust (default 0.30)
"""
from __future__ import annotations

from typing import Dict, List, Optional

from bot.config import settings
from bot.utils.logger import log


# Bounds
_MIN_WEIGHT = 0.10
_MAX_WEIGHT = 0.99
_MAX_ADJUSTMENT = 0.15
_MIN_PREDICTIONS = 5
_ADJUSTMENT_SCALE = 0.30


class WeightAdjuster:
    """
    Adjusts trader archetype influence weights based on accuracy data.

    Usage:
        adjuster = WeightAdjuster()
        adjustments = await adjuster.compute_adjustments()
        await adjuster.apply_adjustments()
    """

    def __init__(self):
        self._baseline_weights: Dict[str, float] = {}
        self._initialized = False

    def _ensure_baselines(self):
        """Cache the original (hardcoded) baseline weights."""
        if self._initialized:
            return
        from .trader_profiles import get_all_profiles
        for profile in get_all_profiles():
            self._baseline_weights[profile.archetype] = profile.influence_weight
        self._initialized = True

    async def compute_adjustments(self) -> Dict[str, Dict[str, float]]:
        """
        Compute weight adjustments based on accuracy data.

        Returns:
            Dict mapping archetype -> {
                "baseline": original weight,
                "accuracy": historical accuracy,
                "total_predictions": int,
                "new_weight": adjusted weight,
                "delta": change from baseline,
            }
        """
        self._ensure_baselines()

        from .accuracy_tracker import AccuracyTracker
        tracker = AccuracyTracker()
        await tracker.init()

        accuracy_map = await tracker.get_archetype_accuracy_map()

        adjustments: Dict[str, Dict[str, float]] = {}

        for archetype, baseline in self._baseline_weights.items():
            if archetype not in accuracy_map:
                # No data — keep baseline
                adjustments[archetype] = {
                    "baseline": baseline,
                    "accuracy": -1,
                    "total_predictions": 0,
                    "new_weight": baseline,
                    "delta": 0.0,
                }
                continue

            accuracy = accuracy_map[archetype]

            # Compute adjustment: accuracy above 0.5 = boost, below = penalty
            raw_delta = (accuracy - 0.5) * _ADJUSTMENT_SCALE
            clamped_delta = max(-_MAX_ADJUSTMENT, min(_MAX_ADJUSTMENT, raw_delta))

            new_weight = baseline + clamped_delta
            new_weight = max(_MIN_WEIGHT, min(_MAX_WEIGHT, new_weight))

            adjustments[archetype] = {
                "baseline": baseline,
                "accuracy": accuracy,
                "total_predictions": -1,  # Filled below
                "new_weight": round(new_weight, 3),
                "delta": round(new_weight - baseline, 3),
            }

        # Fill in total_predictions from full stats
        stats = await tracker.get_archetype_stats()
        for s in stats:
            if s.archetype in adjustments:
                adjustments[s.archetype]["total_predictions"] = s.total_predictions

        return adjustments

    async def apply_adjustments(self) -> List[str]:
        """
        Apply weight adjustments to the live trader profiles.

        Modifies the TRADER_ARCHETYPES list in-place so that future
        simulations use the updated weights.

        Returns list of adjustment descriptions.
        """
        adjustments = await self.compute_adjustments()

        from .trader_profiles import TRADER_ARCHETYPES

        changes: List[str] = []

        for profile in TRADER_ARCHETYPES:
            adj = adjustments.get(profile.archetype)
            if not adj or adj["total_predictions"] < _MIN_PREDICTIONS:
                continue

            old_weight = profile.influence_weight
            new_weight = adj["new_weight"]

            if abs(new_weight - old_weight) < 0.005:
                continue  # No meaningful change

            profile.influence_weight = new_weight
            direction = "+" if new_weight > old_weight else ""
            change_desc = (
                f"{profile.name} ({profile.archetype}): "
                f"{old_weight:.2f} -> {new_weight:.2f} "
                f"({direction}{adj['delta']:.3f}) "
                f"[accuracy: {adj['accuracy']:.0%} over {adj['total_predictions']} predictions]"
            )
            changes.append(change_desc)
            log.info(f"[WEIGHT ADJ] {change_desc}")

        if changes:
            log.info(f"[WEIGHT ADJ] Applied {len(changes)} weight adjustment(s)")
        else:
            log.info("[WEIGHT ADJ] No adjustments needed (insufficient data or no changes)")

        return changes

    async def get_current_weights(self) -> Dict[str, Dict[str, float]]:
        """Get current weights with accuracy data for display."""
        self._ensure_baselines()
        from .trader_profiles import get_all_profiles
        from .accuracy_tracker import AccuracyTracker

        tracker = AccuracyTracker()
        await tracker.init()
        stats = await tracker.get_archetype_stats()
        stats_map = {s.archetype: s for s in stats}

        result: Dict[str, Dict[str, float]] = {}
        for profile in get_all_profiles():
            s = stats_map.get(profile.archetype)
            result[profile.archetype] = {
                "name": profile.name,
                "baseline_weight": self._baseline_weights.get(profile.archetype, profile.influence_weight),
                "current_weight": profile.influence_weight,
                "accuracy": s.accuracy if s else -1,
                "total_predictions": s.total_predictions if s else 0,
            }
        return result
