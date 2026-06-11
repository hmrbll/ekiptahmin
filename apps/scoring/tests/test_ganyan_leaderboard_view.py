"""Public leaderboard view — cumulative tier counts and the Adet/Puan toggle.

Product rules:
- Counts are cumulative: an exact-score hit also counts as a correct goal
  difference and a correct result (mirrors the weighted tiebreaker
  semantics). A diff hit also counts as a correct result.
- Every stat cell carries both faces (hit count + points earned) so the
  client-side toggle can switch without a round-trip.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.scoring.models import GanyanScore
from apps.tournament.models import BracketSlot, Stage, Tournament

User = get_user_model()


@pytest.fixture
def tournament(db):
    return Tournament.objects.create(
        name="Test Cup", slug="test-cup", is_active=True,
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
    )


@pytest.fixture
def slot(tournament):
    stage = Stage.objects.create(
        tournament=tournament, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
    )
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage, position="GroupA-M1",
        scheduled_kickoff=timezone.now() - timedelta(hours=3),
    )


def _user(nickname):
    email = f"{nickname.lower()}@x.com"
    return User.objects.create_user(email=email, username=email, nickname=nickname)


def _score(user, slot, outcome, **points):
    defaults = {"score_exact": 0, "score_diff": 0, "score_result": 0, "score_penalty": 0}
    defaults.update(points)
    total = sum(Decimal(str(v)) for v in defaults.values())
    return GanyanScore.objects.create(
        user=user, slot=slot, outcome=outcome, total=total, **defaults,
    )


@pytest.mark.django_db
class TestCumulativeCounts:
    def _row_for(self, client, nickname):
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 200
        return r.context["rows"], r.content.decode()

    def test_exact_hit_counts_in_all_three_columns(self, client, slot):
        _score(_user("Ali"), slot, GanyanScore.EXACT,
               score_exact="33.33", score_diff="33.33", score_result="33.33")
        rows, _ = self._row_for(client, "Ali")
        row = rows[0]
        assert (row["exact"], row["diff"], row["result"]) == (1, 1, 1)

    def test_diff_hit_counts_as_result_too(self, client, slot):
        _score(_user("Veli"), slot, GanyanScore.DIFF,
               score_diff="25.00", score_result="25.00")
        rows, _ = self._row_for(client, "Veli")
        row = rows[0]
        assert (row["exact"], row["diff"], row["result"]) == (0, 1, 1)

    def test_result_hit_counts_only_as_result(self, client, slot):
        _score(_user("Can"), slot, GanyanScore.RESULT, score_result="20.00")
        rows, _ = self._row_for(client, "Can")
        row = rows[0]
        assert (row["exact"], row["diff"], row["result"]) == (0, 0, 1)

    def test_miss_counts_only_as_wrong(self, client, slot):
        score = _score(_user("Zeki"), slot, GanyanScore.MISS)
        score.wrong_count_contribution = 1
        score.save()
        rows, _ = self._row_for(client, "Zeki")
        row = rows[0]
        assert (row["exact"], row["diff"], row["result"], row["wrong"]) == (0, 0, 0, 1)

    def test_counts_accumulate_across_matches(self, client, slot, tournament):
        # Same user: one exact + one result across two matches.
        other_slot = BracketSlot.objects.create(
            tournament=tournament, stage=slot.stage, position="GroupA-M2",
            scheduled_kickoff=timezone.now() - timedelta(hours=1),
        )
        u = _user("Ali")
        _score(u, slot, GanyanScore.EXACT,
               score_exact="33.33", score_diff="33.33", score_result="33.33")
        _score(u, other_slot, GanyanScore.RESULT, score_result="20.00")
        rows, _ = self._row_for(client, "Ali")
        row = rows[0]
        assert (row["exact"], row["diff"], row["result"]) == (1, 1, 2)


@pytest.mark.django_db
class TestAdetPuanToggle:
    def test_cells_carry_both_count_and_points_faces(self, client, slot):
        _score(_user("Ali"), slot, GanyanScore.EXACT,
               score_exact="33.33", score_diff="33.33", score_result="33.33")
        r = client.get(reverse("leaderboard"))
        content = r.content.decode()
        assert 'data-lb-toggle="adet"' in content
        assert 'data-lb-toggle="puan"' in content
        assert 'data-lb-val="adet"' in content
        assert 'data-lb-val="puan"' in content
        # Points face rendered with two decimals on the desktop table.
        assert "33.33" in content or "33,33" in content

    def test_points_columns_exposed_in_context(self, client, slot):
        _score(_user("Ali"), slot, GanyanScore.EXACT,
               score_exact="33.33", score_diff="33.33", score_result="33.33")
        r = client.get(reverse("leaderboard"))
        row = r.context["rows"][0]
        assert row["points_exact"] == Decimal("33.33")
        assert row["points_diff"] == Decimal("33.33")
        assert row["points_result"] == Decimal("33.33")
        assert row["points_penalty"] == Decimal("0")
