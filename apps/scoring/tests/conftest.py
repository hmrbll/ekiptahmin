"""Shared fixtures for scoring engine tests."""

from decimal import Decimal

import pytest

from apps.scoring.engine import RoundConfig, StageConfig


# Realistic 2026-style scoring config — values from data/wc2026/tournament.json.
# Tests use these unless they need a custom config.

@pytest.fixture
def stage_group():
    return StageConfig(points_exact=6, points_diff=4, points_result=2, penalty_loser_pct=Decimal("0.60"))


@pytest.fixture
def stage_r32():
    return StageConfig(points_exact=7, points_diff=5, points_result=3, penalty_loser_pct=Decimal("0.60"))


@pytest.fixture
def stage_r16():
    return StageConfig(points_exact=9, points_diff=6, points_result=3, penalty_loser_pct=Decimal("0.60"))


@pytest.fixture
def stage_qf():
    return StageConfig(points_exact=14, points_diff=9, points_result=5, penalty_loser_pct=Decimal("0.60"))


@pytest.fixture
def stage_sf():
    return StageConfig(points_exact=20, points_diff=14, points_result=7, penalty_loser_pct=Decimal("0.60"))


@pytest.fixture
def round_pre():
    """Pre-tournament round, full weight."""
    return RoundConfig(order=0, weight=Decimal("1.00"))


@pytest.fixture
def round_after_group():
    return RoundConfig(order=1, weight=Decimal("0.85"))


@pytest.fixture
def round_after_r32():
    return RoundConfig(order=2, weight=Decimal("0.75"))


@pytest.fixture
def round_after_r16():
    return RoundConfig(order=3, weight=Decimal("0.65"))
