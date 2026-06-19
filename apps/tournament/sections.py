"""Group a tournament's matches into ordered "round" tab sections.

Shared by the public all-predictions page and the results log so both tab the
same way: the three group matchdays first (Grup İlk/İkinci/Üçüncü Maçlar), then
the knockout stages in bracket order (Son 32 → Final).
"""

import re

from .models import BracketSlot

# Turkish ordinals for the three group matchdays, used to label the group tabs.
GROUP_MATCHDAY_LABELS = {1: "İlk", 2: "İkinci", 3: "Üçüncü"}
_GROUP_MATCH_NO_RE = re.compile(r"-M(\d+)$")

# Knockout stage ordering + Turkish labels for the round tabs.
KNOCKOUT_STAGE_ORDER = ["R32", "R16", "QF", "SF", "THIRD", "FINAL"]
KNOCKOUT_LABELS = {
    "R32": "Son 32",
    "R16": "Son 16",
    "QF": "Çeyrek Final",
    "SF": "Yarı Final",
    "THIRD": "3.lük",
    "FINAL": "Final",
}


def group_matchday(position: str) -> int:
    """Matchday (1/2/3) for a 'GroupX-Mn' slot. Each 4-team group plays its six
    matches across three matchdays, two per day: M1-2 → 1, M3-4 → 2, M5-6 → 3."""
    m = _GROUP_MATCH_NO_RE.search(position)
    if not m:
        return 3
    return (int(m.group(1)) + 1) // 2


def slot_section(slot: BracketSlot) -> tuple[int, str, str]:
    """(sort_order, key, label) for the round tab this slot belongs to.

    Group matchdays come first (Grup İlk/İkinci/Üçüncü Maçlar), then the
    knockout stages in bracket order (Son 32 → Final).
    """
    kind = slot.stage.kind
    if kind == "GROUP":
        md = group_matchday(slot.position)
        return (md - 1, f"group-md{md}", f"Grup {GROUP_MATCHDAY_LABELS[md]} Maçlar")
    order = (
        KNOCKOUT_STAGE_ORDER.index(kind)
        if kind in KNOCKOUT_STAGE_ORDER
        else len(KNOCKOUT_STAGE_ORDER)
    )
    return (3 + order, f"ko-{kind}", KNOCKOUT_LABELS.get(kind, slot.stage.get_kind_display()))


def group_matches_into_sections(matches: list[dict]) -> tuple[list[dict], str]:
    """Bucket the kickoff-ordered match list into ordered tab sections.

    Each match dict must carry a ``"slot"`` (a :class:`BracketSlot`) and an
    ``"actual"`` (its result, or a falsy value when unplayed). Returns
    (sections, default_key); each section is {"key", "label", "matches"} and
    matches keep their incoming order within it. The default tab is the
    earliest section that still has an unplayed match (the round "in
    progress"), falling back to the last section when every match is scored.
    """
    by_key: dict[str, dict] = {}
    order_by_key: dict[str, int] = {}
    for m in matches:
        order, key, label = slot_section(m["slot"])
        sec = by_key.get(key)
        if sec is None:
            sec = by_key[key] = {"key": key, "label": label, "matches": []}
            order_by_key[key] = order
        sec["matches"].append(m)

    sections = [by_key[k] for k in sorted(by_key, key=lambda k: order_by_key[k])]
    if not sections:
        return [], ""
    default_key = sections[-1]["key"]
    for sec in sections:
        if any(not m["actual"] for m in sec["matches"]):
            default_key = sec["key"]
            break
    return sections, default_key
