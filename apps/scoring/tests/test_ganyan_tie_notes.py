"""Ganyan leaderboard tie notes — wording must be concrete.

Product rule: a tie note always states WHICH criterion decided the order and
each user's value on that criterion. Vague phrasings ("resolved during the
tournament" etc.) are banned. Pure unit tests — `describe_ties` only reads
the entry dataclasses, no DB needed.
"""

from decimal import Decimal

from django.utils.formats import number_format

from apps.scoring.ganyan_leaderboard import GanyanLeaderboardEntry, describe_ties


def _entry(nickname: str, total: str, weighted_exact: str = "0",
           weighted_diff: str = "0", weighted_result: str = "0", wrong: int = 0):
    total_d = Decimal(total)
    return GanyanLeaderboardEntry(
        user=None, rank=0, total=total_d, counts={}, score_breakdown={},
        tiebreakers=(
            -total_d,
            -Decimal(weighted_exact),
            -Decimal(weighted_diff),
            -Decimal(weighted_result),
            wrong,
        ),
        nickname=nickname,
    )


def test_no_notes_when_totals_unique():
    entries = [_entry("Ali", "30.00"), _entry("Veli", "20.00")]
    assert describe_ties(entries) == []


def test_decisive_criterion_named_with_per_user_values():
    entries = [
        _entry("Ali", "25.00", weighted_exact="3.40"),
        _entry("Veli", "25.00", weighted_exact="2.55"),
    ]
    notes = describe_ties(entries)
    assert len(notes) == 1
    note = notes[0]
    assert "ağırlıklı tam skor sayısı" in note
    assert f"Ali {number_format(Decimal('3.40'), decimal_pos=2)}" in note
    assert f"Veli {number_format(Decimal('2.55'), decimal_pos=2)}" in note


def test_wrong_count_criterion_shows_plain_integers():
    entries = [
        _entry("Ali", "25.00", wrong=1),
        _entry("Veli", "25.00", wrong=4),
    ]
    notes = describe_ties(entries)
    assert len(notes) == 1
    assert "az yanlış" in notes[0]
    assert "Ali 1" in notes[0]
    assert "Veli 4" in notes[0]


def test_truly_tied_note_lists_all_criteria_and_no_vague_wording():
    entries = [_entry("Ali", "25.00"), _entry("Veli", "25.00")]
    notes = describe_ties(entries)
    assert len(notes) == 1
    note = notes[0]
    assert "aynı sırayı paylaşıyorlar" in note
    assert "alfabetik" in note
    assert "ağırlıklı tam skor sayısı" in note  # criteria spelled out
    assert "turnuvada değerlendirilir" not in note


def test_three_way_group_reports_highest_priority_differing_criterion():
    entries = [
        _entry("Ali", "25.00", weighted_exact="3.00"),
        _entry("Veli", "25.00", weighted_exact="2.00"),
        _entry("Zeki", "25.00", weighted_exact="2.00", wrong=5),
    ]
    notes = describe_ties(entries)
    assert len(notes) == 1
    assert "ağırlıklı tam skor sayısı" in notes[0]
    assert "Zeki" in notes[0]
