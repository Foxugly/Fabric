"""Bound what a command may store (I4).

An agent's output is written to the database verbatim — a `Get-Content .env`
included — and kept until the retention purge. Capping it bounds both the table
and what a leak of it would expose. Truncation is explicit: the stored text
ends with a marker, so nobody mistakes a cut output for a complete one.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

TRUNCATION_MARKER = "\n…[truncated by Fabric]"

#: Fixed caps for fields that are not command output proper.
ERROR_MAX_CHARS = 10_000
TOOL_INPUT_MAX_CHARS = 20_000


def output_max_chars() -> int:
    return max(int(getattr(settings, "FABRIC_COMMAND_OUTPUT_MAX_CHARS", 200_000)), 0)


def cap_strings(value: Any, budget: int) -> tuple[Any, int, bool]:
    """Cap the total length of every string in `value` to `budget` characters.

    Walks dicts and lists in order and spends the budget as it goes. Returns
    `(capped value, characters used, truncated?)`; a truncated string ends with
    `TRUNCATION_MARKER` (not counted against the budget).
    """
    budget = max(budget, 0)
    if isinstance(value, str):
        if len(value) <= budget:
            return value, len(value), False
        return value[:budget] + TRUNCATION_MARKER, budget, True
    if isinstance(value, dict):
        capped_dict: dict[Any, Any] = {}
        used = 0
        truncated = False
        for key, item in value.items():
            capped_item, spent, cut = cap_strings(item, budget - used)
            capped_dict[key] = capped_item
            used += spent
            truncated = truncated or cut
        return capped_dict, used, truncated
    if isinstance(value, list):
        capped_list: list[Any] = []
        used = 0
        truncated = False
        for item in value:
            capped_item, spent, cut = cap_strings(item, budget - used)
            capped_list.append(capped_item)
            used += spent
            truncated = truncated or cut
        return capped_list, used, truncated
    return value, 0, False


def cap_text(text: str, limit: int) -> str:
    capped, _, _ = cap_strings(text, limit)
    return str(capped)
