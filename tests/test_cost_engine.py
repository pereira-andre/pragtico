"""Unit tests for the pilotage cost calculation engine (APSS 2024).

Formula: T = UP × √GT (Art. 15º)
TUP: per vessel type with 1st/subsequent period rates (Art. 9º)
Reductions: tiered by regular line calls (Art. 16º)
"""

import math
import unittest

from domain.cost_engine import (
    UP_NORMAL,
    UP_SHIFT_ALONG,
    STANDBY_HOURLY_RATE,
    TUP_RATES,
    ManoeuvreInput,
    ManoeuvreType,
    SurchargeType,
    ReductionType,
    calculate_manoeuvre_cost,
    calculate_tup,
    estimate_tup,
    calculate_cancellation_fee,
    calculate_scale_cost,
    format_cost_summary,
    quick_estimate,
)


class TestBaseFormula(unittest.TestCase):
    """T = UP × √GT (Art. 15º)"""

    def test_entry(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=20000))
        assert r.base_cost == round(UP_NORMAL * math.sqrt(20000), 2)
        assert r.total_cost == r.base_cost

    def test_departure(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.DEPARTURE, gt=15000))
        assert r.total_cost == round(UP_NORMAL * math.sqrt(15000), 2)

    def test_shift_uses_reduced_rate(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.SHIFT, gt=20000))
        assert r.up_rate == UP_SHIFT_ALONG
        assert r.total_cost == round(UP_SHIFT_ALONG * math.sqrt(20000), 2)

    def test_zero_gt(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=0))
        assert r.total_cost == 0.0

    def test_negative_gt(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=-100))
        assert r.total_cost == 0.0

    def test_custom_up(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=10000, custom_up=5.0))
        assert r.total_cost == round(5.0 * math.sqrt(10000), 2)

    def test_realistic_20k(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=20000))
        assert 1000 < r.total_cost < 2000  # ~1309€

    def test_realistic_50k(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=50000))
        assert 1500 < r.total_cost < 3000  # ~2070€


class TestSurcharges(unittest.TestCase):
    def test_no_propulsion(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000, surcharges=[SurchargeType.NO_PROPULSION],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.surcharge_amount == round(base * 0.25, 2)
        assert r.total_cost == round(base + base * 0.25, 2)

    def test_both_surcharges_cumulative(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            surcharges=[SurchargeType.NO_PROPULSION, SurchargeType.SPECIAL_ASSISTANCE],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.surcharge_amount == round(base * 0.50, 2)


class TestTieredReductions(unittest.TestCase):
    """Art. 16º: 6-24→10%, 25-52→15%, 53-100→20%, >100→25%"""

    def test_regular_line_10_calls(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.REGULAR_LINE], regular_line_calls=10,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.10, 2)

    def test_regular_line_30_calls(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.REGULAR_LINE], regular_line_calls=30,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.15, 2)

    def test_regular_line_60_calls(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.REGULAR_LINE], regular_line_calls=60,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.20, 2)

    def test_regular_line_150_calls(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.REGULAR_LINE], regular_line_calls=150,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.25, 2)

    def test_technical_call_30pct(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.TECHNICAL_CALL],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.30, 2)

    def test_cabotage_10pct(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.CABOTAGE],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.10, 2)

    def test_best_reduction_wins(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000,
            reductions=[ReductionType.CABOTAGE, ReductionType.TECHNICAL_CALL],
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        assert r.reduction_amount == round(base * 0.30, 2)


class TestStandby(unittest.TestCase):
    def test_standby_cost(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.STANDBY, gt=10000, standby_hours=2.0,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        hourly = STANDBY_HOURLY_RATE * 2.0
        base_surcharge = base * 0.25
        assert r.standby_cost == round(hourly + base_surcharge, 2)

    def test_regular_manoeuvre_can_add_standby_hours(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000, standby_hours=1.5,
        ))
        base = round(UP_NORMAL * math.sqrt(10000), 2)
        hourly = STANDBY_HOURLY_RATE * 1.5
        base_surcharge = base * 0.25
        assert r.standby_cost == round(hourly + base_surcharge, 2)
        assert r.total_cost == round(base + hourly + base_surcharge, 2)
        assert any("Horas à ordem" in line for line in r.breakdown)


class TestTUPByVesselType(unittest.TestCase):
    """Art. 9º: TUP varies by vessel type and period"""

    def test_contentores_1_day(self):
        tup = calculate_tup(20000, "contentores", 1.0)
        assert tup == round(0.1144 * 20000, 2)

    def test_contentores_3_days(self):
        tup = calculate_tup(20000, "contentores", 3.0)
        expected = 0.1144 * 20000 + 0.0263 * 20000 * 2
        assert tup == round(expected, 2)

    def test_tanque_1_day(self):
        tup = calculate_tup(20000, "tanque", 1.0)
        assert tup == round(0.1459 * 20000, 2)

    def test_passageiros_2_days(self):
        tup = calculate_tup(20000, "passageiros", 2.0)
        expected = 0.0620 * 20000 + 0.0263 * 20000
        assert tup == round(expected, 2)

    def test_unknown_type_defaults_to_restantes(self):
        tup = calculate_tup(10000, "submarine", 1.0)
        assert tup == round(0.1459 * 10000, 2)

    def test_estimate_tup_alias(self):
        assert estimate_tup(20000, "contentores", 1.0) == calculate_tup(20000, "contentores", 1.0)


class TestCancellation(unittest.TestCase):
    def test_2h_before(self):
        fee = calculate_cancellation_fee(1000.0, "2h_before")
        assert fee == 300.0  # 30%

    def test_1h_after(self):
        fee = calculate_cancellation_fee(1000.0, "1h_after")
        assert fee == 500.0  # 50%

    def test_after_1h(self):
        fee = calculate_cancellation_fee(1000.0, "after_1h")
        assert fee == 1000.0  # 100%

    def test_weather_pilot_embarked(self):
        fee = calculate_cancellation_fee(1000.0, "any", with_pilot_embarked=True)
        assert fee == 250.0  # 25%


class TestScaleCost(unittest.TestCase):
    def test_entry_departure_with_tup(self):
        e = calculate_scale_cost(
            vessel_name="Test", gt=20000, vessel_type="contentores",
            manoeuvres=[
                ManoeuvreInput(ManoeuvreType.ENTRY, gt=20000),
                ManoeuvreInput(ManoeuvreType.DEPARTURE, gt=20000),
            ],
            stay_days=2, include_tup=True,
        )
        entry_cost = round(UP_NORMAL * math.sqrt(20000), 2)
        assert e.pilotage_total == round(entry_cost * 2, 2)
        assert e.tup_estimate == calculate_tup(20000, "contentores", 2)
        assert e.grand_total == round(e.pilotage_total + e.tup_estimate, 2)
        assert e.vessel_type == "contentores"


class TestFormatAndQuick(unittest.TestCase):
    def test_format(self):
        e = calculate_scale_cost(
            "MSC Lyria", gt=32540, vessel_type="contentores",
            manoeuvres=[ManoeuvreInput(ManoeuvreType.ENTRY, gt=32540)],
        )
        text = format_cost_summary(e)
        assert "MSC Lyria" in text
        assert "€" in text

    def test_quick_entry(self):
        r = quick_estimate(20000, "entry")
        assert r["total_cost"] == round(UP_NORMAL * math.sqrt(20000), 2)

    def test_quick_shift(self):
        r = quick_estimate(20000, "mudança")
        assert r["total_cost"] == round(UP_SHIFT_ALONG * math.sqrt(20000), 2)


class TestBreakdown(unittest.TestCase):
    def test_shows_sqrt(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(ManoeuvreType.ENTRY, gt=10000))
        assert any("√" in line for line in r.breakdown)

    def test_shows_surcharge(self):
        r = calculate_manoeuvre_cost(ManoeuvreInput(
            ManoeuvreType.ENTRY, gt=10000, surcharges=[SurchargeType.NO_PROPULSION],
        ))
        assert any("Agravamento" in line for line in r.breakdown)
