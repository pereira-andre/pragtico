"""Unit tests for the pilotage cost calculation engine.

CRITICAL: The formula is T = UP × √GT (Art. 15º APSS 2024), NOT UP × GT.
All expected values use math.sqrt(gt).
"""

import math
import pytest

from cost_engine import (
    UP_NORMAL,
    UP_SHIFT_ALONG,
    STANDBY_HOURLY_RATE,
    TUP_RATE_PER_GT_DAY,
    ManoeuvreInput,
    ManoeuvreType,
    SurchargeType,
    ReductionType,
    calculate_manoeuvre_cost,
    estimate_tup,
    estimate_cancellation_fee,
    calculate_scale_cost,
    format_cost_summary,
    quick_estimate,
)


class TestBaseManoeuvreCalculation:
    """Test basic pilotage cost formula: T = UP × √GT."""

    def test_normal_entry(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=20000,
        ))
        expected = round(UP_NORMAL * math.sqrt(20000), 2)
        assert result.total_cost == expected
        assert result.base_cost == expected

    def test_normal_departure(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.DEPARTURE, gt=15000,
        ))
        expected = round(UP_NORMAL * math.sqrt(15000), 2)
        assert result.total_cost == expected

    def test_shift_uses_reduced_rate(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.SHIFT, gt=20000,
        ))
        expected = round(UP_SHIFT_ALONG * math.sqrt(20000), 2)
        assert result.total_cost == expected
        assert result.up_rate == UP_SHIFT_ALONG

    def test_zero_gt(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=0,
        ))
        assert result.total_cost == 0.0

    def test_negative_gt_treated_as_zero(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=-5000,
        ))
        assert result.total_cost == 0.0

    def test_custom_up_override(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000, custom_up=5.0,
        ))
        expected = round(5.0 * math.sqrt(10000), 2)
        assert result.total_cost == expected

    def test_realistic_values(self):
        """Verify a realistic example: GT=32540, entry manoeuvre."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=32540,
        ))
        expected = round(UP_NORMAL * math.sqrt(32540), 2)
        assert result.total_cost == expected
        # Should be ~1669€, NOT ~301k€
        assert result.total_cost < 5000
        assert result.total_cost > 500


class TestSurcharges:
    def test_no_propulsion_surcharge(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        surcharge = round(base * 0.25, 2)
        assert result.surcharge_amount == surcharge
        assert result.total_cost == round(base + surcharge, 2)

    def test_cumulative_surcharges(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION, SurchargeType.SPECIAL_ASSISTANCE],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert result.surcharge_amount == round(base * 0.50, 2)


class TestReductions:
    def test_regular_line_reduction(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.REGULAR_LINE],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        reduction = round(base * 0.25, 2)
        assert result.reduction_amount == reduction
        assert result.total_cost == round(base - reduction, 2)

    def test_best_reduction_wins(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.CABOTAGE, ReductionType.TECHNICAL_CALL],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert result.reduction_amount == round(base * 0.30, 2)


class TestStandbyPilotage:
    def test_standby_cost(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.STANDBY, gt=10000, standby_hours=2.0,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        standby = round(STANDBY_HOURLY_RATE * 2.0 + base * 0.25, 2)
        assert result.standby_cost == standby
        assert result.total_cost == round(base + standby, 2)

    def test_no_standby_for_entry(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000, standby_hours=3.0,
        ))
        assert result.standby_cost == 0.0


class TestTUP:
    def test_tup_one_day(self):
        assert estimate_tup(20000, 1.0) == round(TUP_RATE_PER_GT_DAY * 20000, 2)

    def test_tup_multiple_days(self):
        assert estimate_tup(20000, 3.0) == round(TUP_RATE_PER_GT_DAY * 20000 * 3, 2)


class TestCancellationFees:
    def test_no_fee_if_early(self):
        assert estimate_cancellation_fee(1000.0, "more_than_24h") == 0.0

    def test_full_fee_no_show(self):
        assert estimate_cancellation_fee(1000.0, "no_show") == 1000.0


class TestScaleCostEstimate:
    def test_entry_plus_departure(self):
        estimate = calculate_scale_cost(
            vessel_name="Test Ship", gt=20000,
            manoeuvres=[
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.ENTRY, gt=20000),
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.DEPARTURE, gt=20000),
            ],
            stay_days=2, include_tup=True,
        )
        entry_cost = round(UP_NORMAL * math.sqrt(20000), 2)
        assert estimate.pilotage_total == round(entry_cost * 2, 2)
        assert estimate.tup_estimate == round(TUP_RATE_PER_GT_DAY * 20000 * 2, 2)
        assert len(estimate.manoeuvres) == 2
        # Pilotage should be ~2618€ not ~370k€
        assert estimate.pilotage_total < 10000


class TestFormatSummary:
    def test_format_produces_text(self):
        estimate = calculate_scale_cost(
            vessel_name="MSC Lyria", gt=32540,
            manoeuvres=[
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.ENTRY, gt=32540),
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.DEPARTURE, gt=32540),
            ],
        )
        text = format_cost_summary(estimate)
        assert "MSC Lyria" in text
        assert "€" in text


class TestQuickEstimate:
    def test_quick_entry(self):
        result = quick_estimate(20000, "entry")
        expected = round(UP_NORMAL * math.sqrt(20000), 2)
        assert result["total_cost"] == expected

    def test_quick_shift(self):
        result = quick_estimate(20000, "mudança")
        expected = round(UP_SHIFT_ALONG * math.sqrt(20000), 2)
        assert result["total_cost"] == expected

    def test_quick_departure_pt(self):
        result = quick_estimate(15000, "saída")
        expected = round(UP_NORMAL * math.sqrt(15000), 2)
        assert result["total_cost"] == expected


class TestBreakdownContents:
    def test_breakdown_shows_sqrt(self):
        """Breakdown should show √GT formula."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
        ))
        assert any("√" in line for line in result.breakdown)

    def test_breakdown_shows_surcharge(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION],
        ))
        assert any("Agravamento" in line for line in result.breakdown)

    def test_breakdown_shows_reduction(self):
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.REGULAR_LINE],
        ))
        assert any("Redução" in line or "Reducao" in line for line in result.breakdown)


class TestSanityChecks:
    """Verify costs are in realistic ranges per APSS 2024 tariff."""

    def test_small_vessel_entry(self):
        """A 5000 GT vessel entry should cost ~654€."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=5000,
        ))
        assert 500 < result.total_cost < 1000

    def test_medium_vessel_entry(self):
        """A 20000 GT vessel entry should cost ~1309€."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=20000,
        ))
        assert 1000 < result.total_cost < 2000

    def test_large_vessel_entry(self):
        """A 50000 GT vessel entry should cost ~2069€."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=50000,
        ))
        assert 1500 < result.total_cost < 3000

    def test_shift_cheaper_than_entry(self):
        """Shift should cost ~36% of normal manoeuvre (3.3628/9.2578)."""
        entry = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY, gt=20000,
        ))
        shift = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.SHIFT, gt=20000,
        ))
        assert shift.total_cost < entry.total_cost * 0.5
