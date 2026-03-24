"""Unit tests for the pilotage cost calculation engine.

Tests cover:
- Base cost calculation (UP × GT)
- Surcharge application (+25%)
- Reduction application (best-of)
- Standby pilotage
- TUP estimation
- Cancellation fees
- Full scale cost estimates
- Quick estimate for chatbot
- Edge cases (zero GT, empty inputs)
"""

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
    ManoeuvreResult,
    ScaleCostEstimate,
    calculate_manoeuvre_cost,
    estimate_tup,
    estimate_cancellation_fee,
    calculate_scale_cost,
    format_cost_summary,
    quick_estimate,
)


class TestBaseManoeuvreCalculation:
    """Test basic pilotage cost formula: T = UP × GT."""

    def test_normal_entry(self):
        """Standard entry manoeuvre at normal UP rate."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=20000,
        ))
        expected = round(UP_NORMAL * 20000, 2)
        assert result.total_cost == expected
        assert result.base_cost == expected
        assert result.surcharge_amount == 0.0
        assert result.reduction_amount == 0.0

    def test_normal_departure(self):
        """Standard departure uses normal UP rate."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.DEPARTURE,
            gt=15000,
        ))
        expected = round(UP_NORMAL * 15000, 2)
        assert result.total_cost == expected

    def test_shift_uses_reduced_rate(self):
        """Shift manoeuvre uses the lower UP_SHIFT_ALONG rate."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.SHIFT,
            gt=20000,
        ))
        expected = round(UP_SHIFT_ALONG * 20000, 2)
        assert result.total_cost == expected
        assert result.up_rate == UP_SHIFT_ALONG

    def test_zero_gt(self):
        """Zero GT should yield zero cost."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=0,
        ))
        assert result.total_cost == 0.0
        assert result.base_cost == 0.0

    def test_negative_gt_treated_as_zero(self):
        """Negative GT should be clamped to zero."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=-5000,
        ))
        assert result.total_cost == 0.0

    def test_custom_up_override(self):
        """Custom UP rate overrides the default."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            custom_up=5.0,
        ))
        assert result.total_cost == 50000.0
        assert result.up_rate == 5.0


class TestSurcharges:
    """Test surcharge application (+25% each)."""

    def test_no_propulsion_surcharge(self):
        """No propulsion adds 25% to base cost."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION],
        ))
        base = round(UP_NORMAL * 10000, 2)
        surcharge = round(base * 0.25, 2)
        assert result.surcharge_amount == surcharge
        assert result.total_cost == round(base + surcharge, 2)

    def test_special_assistance_surcharge(self):
        """Special assistance adds 25%."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            surcharges=[SurchargeType.SPECIAL_ASSISTANCE],
        ))
        base = round(UP_NORMAL * 10000, 2)
        assert result.surcharge_amount == round(base * 0.25, 2)

    def test_cumulative_surcharges(self):
        """Both surcharges stack: +50% total."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION, SurchargeType.SPECIAL_ASSISTANCE],
        ))
        base = round(UP_NORMAL * 10000, 2)
        surcharge = round(base * 0.50, 2)
        assert result.surcharge_amount == surcharge


class TestReductions:
    """Test reduction application (best-of, not cumulative)."""

    def test_regular_line_reduction(self):
        """Regular line discount: -25%."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            reductions=[ReductionType.REGULAR_LINE],
        ))
        base = round(UP_NORMAL * 10000, 2)
        reduction = round(base * 0.25, 2)
        assert result.reduction_amount == reduction
        assert result.total_cost == round(base - reduction, 2)

    def test_cabotage_reduction(self):
        """Cabotage discount: -10%."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            reductions=[ReductionType.CABOTAGE],
        ))
        base = round(UP_NORMAL * 10000, 2)
        assert result.reduction_amount == round(base * 0.10, 2)

    def test_technical_call_reduction(self):
        """Technical call discount: -30%."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            reductions=[ReductionType.TECHNICAL_CALL],
        ))
        base = round(UP_NORMAL * 10000, 2)
        assert result.reduction_amount == round(base * 0.30, 2)

    def test_best_reduction_wins(self):
        """When multiple reductions apply, only the highest is used."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            reductions=[ReductionType.CABOTAGE, ReductionType.TECHNICAL_CALL],
        ))
        base = round(UP_NORMAL * 10000, 2)
        # Technical call (-30%) is higher than cabotage (-10%)
        assert result.reduction_amount == round(base * 0.30, 2)


class TestSurchargeAndReduction:
    """Test combined surcharge + reduction."""

    def test_surcharge_and_reduction_combined(self):
        """Surcharge and reduction both apply to the base cost."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION],
            reductions=[ReductionType.REGULAR_LINE],
        ))
        base = round(UP_NORMAL * 10000, 2)
        surcharge = round(base * 0.25, 2)
        reduction = round(base * 0.25, 2)
        assert result.total_cost == round(base + surcharge - reduction, 2)


class TestStandbyPilotage:
    """Test standby (pilotagem 'à ordem') calculation."""

    def test_standby_cost(self):
        """Standby: hourly rate × hours + 25% of base."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.STANDBY,
            gt=10000,
            standby_hours=2.0,
        ))
        base = round(UP_NORMAL * 10000, 2)
        standby = round(STANDBY_HOURLY_RATE * 2.0 + base * 0.25, 2)
        assert result.standby_cost == standby
        assert result.total_cost == round(base + standby, 2)

    def test_no_standby_for_entry(self):
        """Standby cost only applies to STANDBY type."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            standby_hours=3.0,
        ))
        assert result.standby_cost == 0.0


class TestTUP:
    """Test Port Usage Tariff estimation."""

    def test_tup_one_day(self):
        """TUP for 1 day."""
        result = estimate_tup(20000, 1.0)
        assert result == round(TUP_RATE_PER_GT_DAY * 20000, 2)

    def test_tup_multiple_days(self):
        """TUP scales with stay days."""
        result = estimate_tup(20000, 3.0)
        assert result == round(TUP_RATE_PER_GT_DAY * 20000 * 3, 2)

    def test_tup_minimum_one_day(self):
        """TUP with zero days defaults to 1."""
        assert estimate_tup(10000, 0) == estimate_tup(10000, 1)


class TestCancellationFees:
    """Test cancellation/alteration fee schedule."""

    def test_no_fee_if_early(self):
        """No fee if cancelled >24h before."""
        assert estimate_cancellation_fee(1000.0, "more_than_24h") == 0.0

    def test_full_fee_no_show(self):
        """100% fee for no-show."""
        assert estimate_cancellation_fee(1000.0, "no_show") == 1000.0

    def test_partial_fee(self):
        """50% fee for 6-12h cancellation."""
        assert estimate_cancellation_fee(1000.0, "6_to_12h") == 500.0


class TestScaleCostEstimate:
    """Test complete scale (port call) cost estimation."""

    def test_entry_plus_departure(self):
        """Standard entry + departure scale."""
        estimate = calculate_scale_cost(
            vessel_name="Test Ship",
            gt=20000,
            manoeuvres=[
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.ENTRY, gt=20000),
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.DEPARTURE, gt=20000),
            ],
            stay_days=2,
            include_tup=True,
        )
        entry_cost = round(UP_NORMAL * 20000, 2)
        assert estimate.pilotage_total == round(entry_cost * 2, 2)
        assert estimate.tup_estimate == round(TUP_RATE_PER_GT_DAY * 20000 * 2, 2)
        assert estimate.grand_total == round(estimate.pilotage_total + estimate.tup_estimate, 2)
        assert len(estimate.manoeuvres) == 2
        assert estimate.vessel_name == "Test Ship"

    def test_no_tup(self):
        """Scale without TUP estimation."""
        estimate = calculate_scale_cost(
            vessel_name="Navio X",
            gt=10000,
            manoeuvres=[ManoeuvreInput(manoeuvre_type=ManoeuvreType.ENTRY, gt=10000)],
            include_tup=False,
        )
        assert estimate.tup_estimate == 0.0
        assert estimate.grand_total == estimate.pilotage_total


class TestFormatSummary:
    """Test human-readable formatting."""

    def test_format_produces_text(self):
        """Formatting should produce non-empty text."""
        estimate = calculate_scale_cost(
            vessel_name="MSC Lyria",
            gt=32540,
            manoeuvres=[
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.ENTRY, gt=32540),
                ManoeuvreInput(manoeuvre_type=ManoeuvreType.DEPARTURE, gt=32540),
            ],
        )
        text = format_cost_summary(estimate)
        assert "MSC Lyria" in text
        assert "32540" in text
        assert "€" in text
        assert "pilotagem" in text.lower()


class TestQuickEstimate:
    """Test chatbot quick estimate function."""

    def test_quick_entry(self):
        """Quick estimate for entry."""
        result = quick_estimate(20000, "entry")
        assert result["gt"] == 20000
        assert result["total_cost"] == round(UP_NORMAL * 20000, 2)
        assert result["currency"] == "EUR"

    def test_quick_departure_pt(self):
        """Quick estimate accepts Portuguese type names."""
        result = quick_estimate(15000, "saída")
        assert result["total_cost"] == round(UP_NORMAL * 15000, 2)

    def test_quick_shift(self):
        """Quick estimate for shift uses lower rate."""
        result = quick_estimate(20000, "mudança")
        assert result["total_cost"] == round(UP_SHIFT_ALONG * 20000, 2)

    def test_quick_unknown_type_defaults_entry(self):
        """Unknown type defaults to entry."""
        result = quick_estimate(10000, "unknown")
        assert result["total_cost"] == round(UP_NORMAL * 10000, 2)


class TestBreakdownContents:
    """Test that breakdown lines contain useful information."""

    def test_breakdown_has_formula(self):
        """Breakdown should show the UP × GT formula."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
        ))
        assert any("€/GT" in line for line in result.breakdown)
        assert any("Total" in line for line in result.breakdown)

    def test_breakdown_shows_surcharge(self):
        """Breakdown should mention surcharge when applied."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION],
        ))
        assert any("Agravamento" in line for line in result.breakdown)

    def test_breakdown_shows_reduction(self):
        """Breakdown should mention reduction when applied."""
        result = calculate_manoeuvre_cost(ManoeuvreInput(
            manoeuvre_type=ManoeuvreType.ENTRY,
            gt=10000,
            reductions=[ReductionType.REGULAR_LINE],
        ))
        assert any("Redução" in line or "Reducao" in line for line in result.breakdown)
