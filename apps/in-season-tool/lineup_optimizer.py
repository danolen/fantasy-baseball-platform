"""
Weekly hitter lineup optimizer — Phase 1a v1 (greedy, Monday-lock).

This module is deliberately free of Streamlit / pandas-display concerns so it
can be unit tested and later replaced with a MILP implementation (v3) without
changing the calling code.

Inputs:
    players: iterable of dicts with at least these keys:
        nfbc_id (any hashable), player_name, pos_raw, pos_array (list[str]),
        team, num_g, dollars, dollars_per_game, home_games, away_games,
        vs_rhp, vs_lhp, bats, and one boolean-ish `is_<slot>_eligible` per
        slot. `dollars` is the score we greedily maximize.
    slot_counts: mapping of slot name -> int (e.g. {"C": 2, "OF": 5, ...}).

Fill order (v1, fixed):
    Exact-position slots first (most constrained → least), then flex slots:
    C, SS, 2B, 3B, 1B, OF, MI, CI, UTIL.

Algorithm (v1):
    For each slot in order, assign its `count` spots to the unassigned
    eligible players with the highest `dollars`. Ties broken by
    dollars_per_game, then num_g, then nfbc_id (deterministic).

Known limitations (fix in later versions):
    - No per-game platoon reasoning (v3, uses weekend SP matchup mart)
    - No split scoring for Mon-Thu vs Fri-Sun (v2)
    - No MILP global optimum — a "better 1B at CI" / "better SS at MI"
      situation can produce a suboptimal assignment. Documented below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


SLOT_FILL_ORDER = ["C", "SS", "2B", "3B", "1B", "OF", "MI", "CI", "UTIL"]

# Maps slot name -> pos_array tokens that satisfy it. UTIL accepts any hitter.
SLOT_ELIGIBILITY: dict[str, tuple[str, ...] | None] = {
    "C": ("C",),
    "1B": ("1B",),
    "2B": ("2B",),
    "3B": ("3B",),
    "SS": ("SS",),
    "OF": ("OF",),
    "MI": ("2B", "SS"),
    "CI": ("1B", "3B"),
    "UTIL": None,
}


@dataclass
class Assignment:
    slot: str
    player: dict[str, Any]


@dataclass
class LineupResult:
    starters: list[Assignment] = field(default_factory=list)
    bench: list[dict[str, Any]] = field(default_factory=list)
    unfilled_slots: list[str] = field(default_factory=list)
    total_score: float = 0.0

    def starter_ids(self) -> set[Any]:
        return {a.player["nfbc_id"] for a in self.starters}


def _is_eligible(player: Mapping[str, Any], slot: str) -> bool:
    tokens = SLOT_ELIGIBILITY[slot]
    pos_array = player.get("pos_array") or []
    if tokens is None:
        return True
    return any(t in pos_array for t in tokens)


def _sort_key(player: Mapping[str, Any]) -> tuple:
    return (
        -float(player.get("dollars") or 0.0),
        -float(player.get("dollars_per_game") or 0.0),
        -int(player.get("num_g") or 0),
        str(player.get("nfbc_id")),
    )


def optimize_lineup(
    players: Iterable[Mapping[str, Any]],
    slot_counts: Mapping[str, int],
) -> LineupResult:
    """Greedy assignment. Returns a LineupResult."""
    pool = [dict(p) for p in players]
    assigned: set[Any] = set()
    result = LineupResult()

    for slot in SLOT_FILL_ORDER:
        needed = int(slot_counts.get(slot, 0))
        if needed <= 0:
            continue
        candidates = [
            p for p in pool
            if p["nfbc_id"] not in assigned and _is_eligible(p, slot)
        ]
        candidates.sort(key=_sort_key)
        taken = candidates[:needed]
        for p in taken:
            assigned.add(p["nfbc_id"])
            result.starters.append(Assignment(slot=slot, player=p))
            result.total_score += float(p.get("dollars") or 0.0)
        short = needed - len(taken)
        for _ in range(short):
            result.unfilled_slots.append(slot)

    result.bench = [p for p in pool if p["nfbc_id"] not in assigned]
    result.bench.sort(key=_sort_key)
    return result
