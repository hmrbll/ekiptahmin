"""Public leaderboard view — cumulative tier counts, the Adet/Puan toggle
and the round tabs.

Product rules:
- Counts are cumulative: an exact-score hit also counts as a correct goal
  difference and a correct result (mirrors the weighted tiebreaker
  semantics). A diff hit also counts as a correct result.
- Every stat cell carries both faces (hit count + points earned) so the
  client-side toggle can switch without a round-trip.
- The board is tabbed by round: "Genel" (overall, default) plus one tab per
  scored round section — the same sections the results page tabs by. Each
  round tab ranks users on that round's matches only; unscored rounds get
  no tab, and the tab bar is omitted while "Genel" is the only tab.
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
        # Overall ("Genel") tab is always tabs[0].
        return r.context["tabs"][0]["rows"], r.content.decode()

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
        row = r.context["tabs"][0]["rows"][0]
        assert row["points_exact"] == Decimal("33.33")
        assert row["points_diff"] == Decimal("33.33")
        assert row["points_result"] == Decimal("33.33")
        assert row["points_penalty"] == Decimal("0")


@pytest.mark.django_db
class TestRoundTabs:
    def _ko_slot(self, tournament, kind, order, position):
        stage = Stage.objects.create(
            tournament=tournament, kind=kind, order=order,
            points_exact=6, points_diff=4, points_result=2,
        )
        return BracketSlot.objects.create(
            tournament=tournament, stage=stage, position=position,
            scheduled_kickoff=timezone.now() - timedelta(hours=1),
        )

    def _tabs(self, client):
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 200
        return r.context["tabs"], r.content.decode()

    def test_overall_plus_one_tab_per_scored_section(self, client, slot, tournament):
        r32_slot = self._ko_slot(tournament, Stage.R32, 4, "R32-1")
        u = _user("Ali")
        _score(u, slot, GanyanScore.EXACT, score_exact="30.00")
        _score(u, r32_slot, GanyanScore.RESULT, score_result="10.00")
        tabs, content = self._tabs(client)
        assert [t["key"] for t in tabs] == ["overall", "group-md1", "ko-R32"]
        assert [t["label"] for t in tabs] == ["Genel", "Grup İlk Maçlar", "Son 32"]
        assert "round-tab-bar" in content
        # Overall is the default (visible) panel.
        assert 'data-default-key="overall"' in content

    def test_round_tab_sums_only_that_rounds_matches(self, client, slot, tournament):
        r32_slot = self._ko_slot(tournament, Stage.R32, 4, "R32-1")
        u = _user("Ali")
        _score(u, slot, GanyanScore.EXACT, score_exact="30.00")
        _score(u, r32_slot, GanyanScore.RESULT, score_result="10.00")
        tabs, _ = self._tabs(client)
        by_key = {t["key"]: t for t in tabs}
        assert by_key["overall"]["rows"][0]["total"] == Decimal("40.00")
        assert by_key["group-md1"]["rows"][0]["total"] == Decimal("30.00")
        assert by_key["ko-R32"]["rows"][0]["total"] == Decimal("10.00")

    def test_round_tab_reranks_independently_of_overall(self, client, slot, tournament):
        # Ali leads overall, Veli leads the R32 tab.
        r32_slot = self._ko_slot(tournament, Stage.R32, 4, "R32-1")
        ali, veli = _user("Ali"), _user("Veli")
        _score(ali, slot, GanyanScore.EXACT, score_exact="50.00")
        _score(ali, r32_slot, GanyanScore.RESULT, score_result="5.00")
        _score(veli, slot, GanyanScore.RESULT, score_result="10.00")
        _score(veli, r32_slot, GanyanScore.EXACT, score_exact="20.00")
        tabs, _ = self._tabs(client)
        by_key = {t["key"]: t for t in tabs}
        assert [r["nickname"] for r in by_key["overall"]["rows"]] == ["Ali", "Veli"]
        assert [r["nickname"] for r in by_key["ko-R32"]["rows"]] == ["Veli", "Ali"]

    def test_unscored_section_gets_no_tab(self, client, slot, tournament):
        # R32 exists but only as NO_RESULT rows → no R32 tab yet.
        r32_slot = self._ko_slot(tournament, Stage.R32, 4, "R32-1")
        u = _user("Ali")
        _score(u, slot, GanyanScore.EXACT, score_exact="30.00")
        _score(u, r32_slot, GanyanScore.NO_RESULT)
        tabs, _ = self._tabs(client)
        assert [t["key"] for t in tabs] == ["overall", "group-md1"]

    def test_user_absent_from_round_they_have_no_scores_in(self, client, slot, tournament):
        r32_slot = self._ko_slot(tournament, Stage.R32, 4, "R32-1")
        ali, veli = _user("Ali"), _user("Veli")
        _score(ali, slot, GanyanScore.EXACT, score_exact="30.00")
        _score(ali, r32_slot, GanyanScore.RESULT, score_result="10.00")
        _score(veli, slot, GanyanScore.RESULT, score_result="10.00")
        tabs, _ = self._tabs(client)
        by_key = {t["key"]: t for t in tabs}
        assert [r["nickname"] for r in by_key["overall"]["rows"]] == ["Ali", "Veli"]
        assert [r["nickname"] for r in by_key["ko-R32"]["rows"]] == ["Ali"]

    def test_tab_bar_hidden_when_only_overall_exists(self, client, slot):
        # An unscored section gets no tab, but its NO_RESULT rows still keep
        # the user on the overall board (with zeros) — leaving "Genel" as the
        # only tab, for which no tab bar should render.
        _score(_user("Ali"), slot, GanyanScore.NO_RESULT)
        tabs, content = self._tabs(client)
        assert [t["key"] for t in tabs] == ["overall"]
        assert "round-tab-bar" not in content
