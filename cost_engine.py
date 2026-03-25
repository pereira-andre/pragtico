"""Pilotage cost calculation engine for the Port of Setúbal.

Implements the tariff structure defined in the Port Pilotage Regulation
(Regulamento de Tarifas da APSS 2024 - Art. 15º) with support for:
- Base pilotage fee: T = UP × √GT (Art. 15º, nº 1)
- Multiple manoeuvre types (entry, departure, shift, anchoring)
- Surcharges (+25% for special conditions, Art. 15º nº 3)
- Reductions (regular line, cabotage, technical calls, Art. 16º)
- Standby pilotage 'à ordem' (Art. 15º nº 4)
- Cancellation fees (Art. 15º nº 7)
- TUP estimation (Art. 9º)
- Complete cost breakdown with formatted output

CRITICAL: The formula is T = UP × √GT (square root of GT), NOT UP × GT.
Reference: Projeto de Regulamento de Tarifas da APSS para 2024, Art. 15º.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Constants — Setúbal 2024 Tariff Schedule
# ---------------------------------------------------------------------------

# Base pilotage unit (€/GT) — Art. 15º
UP_NORMAL = 9.2578        # Standard manoeuvres (entry, exit, berth, unberth)
UP_SHIFT_ALONG = 3.3628   # Running along the quay (shift)

# Standby pilotage — Art. 15º
STANDBY_HOURLY_RATE = 74.64     # €/hour waiting time
STANDBY_BASE_SURCHARGE = 0.25   # +25% of base fee

# Surcharge factors
SURCHARGE_NO_PROPULSION = 0.25      # +25% vessel without propulsion
SURCHARGE_SPECIAL_ASSISTANCE = 0.25  # +25% special assistance (needles, etc.)

# Reduction factors
REDUCTION_REGULAR_LINE = 0.25    # -25% regular line service
REDUCTION_CABOTAGE = 0.10        # -10% cabotage
REDUCTION_TECHNICAL_CALL = 0.30  # -30% technical/emergency call

# TUP estimation (€/GT per day) — Art. 9º (approximate)
TUP_RATE_PER_GT_DAY = 0.1144

# Cancellation fee schedule (% of manoeuvre cost)
CANCELLATION_SCHEDULE = {
    "more_than_24h": 0.00,     # 0% if cancelled > 24h before
    "12_to_24h": 0.25,         # 25% if cancelled 12-24h before
    "6_to_12h": 0.50,          # 50% if cancelled 6-12h before
    "less_than_6h": 0.75,      # 75% if cancelled < 6h before
    "no_show": 1.00,           # 100% if no-show
}


class ManoeuvreType(str, Enum):
    """Types of pilotage manoeuvres, each billed individually."""
    ENTRY = "entry"
    DEPARTURE = "departure"
    SHIFT = "shift"
    ANCHORING = "anchoring"
    TRIALS = "trials"
    STANDBY = "standby"


class SurchargeType(str, Enum):
    """Possible surcharge conditions."""
    NO_PROPULSION = "no_propulsion"
    SPECIAL_ASSISTANCE = "special_assistance"


class ReductionType(str, Enum):
    """Possible reduction conditions."""
    REGULAR_LINE = "regular_line"
    CABOTAGE = "cabotage"
    TECHNICAL_CALL = "technical_call"


@dataclass
class ManoeuvreInput:
    """Input parameters for a single manoeuvre cost calculation.

    Attributes:
        manoeuvre_type: Type of the manoeuvre.
        gt: Gross tonnage of the vessel.
        surcharges: List of applicable surcharge conditions.
        reductions: List of applicable reduction conditions.
        standby_hours: Hours of standby time (only for STANDBY type).
        custom_up: Override UP rate (€/GT) if different from default.
    """
    manoeuvre_type: ManoeuvreType
    gt: float
    surcharges: List[SurchargeType] = field(default_factory=list)
    reductions: List[ReductionType] = field(default_factory=list)
    standby_hours: float = 0.0
    custom_up: Optional[float] = None


@dataclass
class ManoeuvreResult:
    """Result of a single manoeuvre cost calculation.

    Attributes:
        manoeuvre_type: Type of manoeuvre calculated.
        gt: Gross tonnage used.
        up_rate: UP rate applied (€/GT).
        base_cost: Base pilotage cost before adjustments (€).
        surcharge_amount: Total surcharge amount (€).
        reduction_amount: Total reduction amount (€).
        standby_cost: Standby component cost (€).
        total_cost: Final cost after all adjustments (€).
        breakdown: Human-readable breakdown lines.
    """
    manoeuvre_type: str
    gt: float
    up_rate: float
    base_cost: float
    surcharge_amount: float
    reduction_amount: float
    standby_cost: float
    total_cost: float
    breakdown: List[str] = field(default_factory=list)


@dataclass
class ScaleCostEstimate:
    """Complete cost estimate for a port call (scale).

    Attributes:
        vessel_name: Name of the vessel.
        gt: Gross tonnage.
        manoeuvres: List of individual manoeuvre results.
        pilotage_total: Total pilotage cost (€).
        tup_estimate: Estimated TUP cost (€).
        stay_days: Estimated stay in port (days).
        grand_total: Pilotage + TUP estimate (€).
        notes: Additional notes about the estimate.
    """
    vessel_name: str
    gt: float
    manoeuvres: List[ManoeuvreResult] = field(default_factory=list)
    pilotage_total: float = 0.0
    tup_estimate: float = 0.0
    stay_days: float = 0.0
    grand_total: float = 0.0
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core Calculation Functions
# ---------------------------------------------------------------------------

def _get_up_rate(manoeuvre_type: ManoeuvreType, custom_up: Optional[float] = None) -> float:
    """Determine the UP rate for a given manoeuvre type.

    Parameters:
        manoeuvre_type: The type of manoeuvre.
        custom_up: Optional override rate.

    Returns:
        UP rate in €/GT.
    """
    if custom_up is not None and custom_up > 0:
        return custom_up
    if manoeuvre_type == ManoeuvreType.SHIFT:
        return UP_SHIFT_ALONG
    return UP_NORMAL


def _surcharge_factor(surcharges: List[SurchargeType]) -> float:
    """Calculate cumulative surcharge factor.

    Parameters:
        surcharges: List of applicable surcharge conditions.

    Returns:
        Cumulative surcharge factor (e.g., 0.25 for +25%).
    """
    factor = 0.0
    if SurchargeType.NO_PROPULSION in surcharges:
        factor += SURCHARGE_NO_PROPULSION
    if SurchargeType.SPECIAL_ASSISTANCE in surcharges:
        factor += SURCHARGE_SPECIAL_ASSISTANCE
    return factor


def _reduction_factor(reductions: List[ReductionType]) -> float:
    """Calculate the best applicable reduction factor.

    Only the highest reduction applies (not cumulative).

    Parameters:
        reductions: List of applicable reduction conditions.

    Returns:
        Best reduction factor (e.g., 0.30 for -30%).
    """
    factors = []
    if ReductionType.REGULAR_LINE in reductions:
        factors.append(REDUCTION_REGULAR_LINE)
    if ReductionType.CABOTAGE in reductions:
        factors.append(REDUCTION_CABOTAGE)
    if ReductionType.TECHNICAL_CALL in reductions:
        factors.append(REDUCTION_TECHNICAL_CALL)
    return max(factors) if factors else 0.0


def calculate_manoeuvre_cost(manoeuvre: ManoeuvreInput) -> ManoeuvreResult:
    """Calculate the cost of a single pilotage manoeuvre.

    Parameters:
        manoeuvre: Input parameters for the calculation.

    Returns:
        ManoeuvreResult with full breakdown.
    """
    gt = max(manoeuvre.gt, 0.0)
    up_rate = _get_up_rate(manoeuvre.manoeuvre_type, manoeuvre.custom_up)
    base_cost = up_rate * math.sqrt(gt)
    breakdown = [f"Base: {up_rate:.4f} €/√GT × √{gt:.0f} (={math.sqrt(gt):.2f}) = {base_cost:.2f} €"]

    # Surcharges
    surcharge_pct = _surcharge_factor(manoeuvre.surcharges)
    surcharge_amount = base_cost * surcharge_pct
    if surcharge_amount > 0:
        labels = [s.value.replace("_", " ").title() for s in manoeuvre.surcharges]
        breakdown.append(
            f"Agravamento (+{surcharge_pct * 100:.0f}%): +{surcharge_amount:.2f} € "
            f"[{', '.join(labels)}]"
        )

    # Reductions
    reduction_pct = _reduction_factor(manoeuvre.reductions)
    reduction_amount = base_cost * reduction_pct
    if reduction_amount > 0:
        labels = [r.value.replace("_", " ").title() for r in manoeuvre.reductions]
        breakdown.append(
            f"Redução (-{reduction_pct * 100:.0f}%): -{reduction_amount:.2f} € "
            f"[{', '.join(labels)}]"
        )

    # Standby component
    standby_cost = 0.0
    if manoeuvre.manoeuvre_type == ManoeuvreType.STANDBY and manoeuvre.standby_hours > 0:
        standby_hourly = STANDBY_HOURLY_RATE * manoeuvre.standby_hours
        standby_base = base_cost * STANDBY_BASE_SURCHARGE
        standby_cost = standby_hourly + standby_base
        breakdown.append(
            f"Pilotagem à ordem: {STANDBY_HOURLY_RATE:.2f} €/h × {manoeuvre.standby_hours:.1f}h "
            f"+ 25% base ({standby_base:.2f} €) = {standby_cost:.2f} €"
        )

    total = base_cost + surcharge_amount - reduction_amount + standby_cost
    breakdown.append(f"Total manobra: {total:.2f} €")

    type_labels = {
        ManoeuvreType.ENTRY: "Entrada",
        ManoeuvreType.DEPARTURE: "Saída",
        ManoeuvreType.SHIFT: "Mudança de cais",
        ManoeuvreType.ANCHORING: "Fundeadouro",
        ManoeuvreType.TRIALS: "Experiências",
        ManoeuvreType.STANDBY: "Pilotagem à ordem",
    }

    return ManoeuvreResult(
        manoeuvre_type=type_labels.get(manoeuvre.manoeuvre_type, manoeuvre.manoeuvre_type.value),
        gt=gt,
        up_rate=up_rate,
        base_cost=round(base_cost, 2),
        surcharge_amount=round(surcharge_amount, 2),
        reduction_amount=round(reduction_amount, 2),
        standby_cost=round(standby_cost, 2),
        total_cost=round(total, 2),
        breakdown=breakdown,
    )


def estimate_tup(gt: float, stay_days: float = 1.0) -> float:
    """Estimate the Port Usage Tariff (TUP).

    Parameters:
        gt: Gross tonnage.
        stay_days: Number of days in port.

    Returns:
        Estimated TUP cost in euros.
    """
    return round(TUP_RATE_PER_GT_DAY * gt * max(stay_days, 1.0), 2)


def estimate_cancellation_fee(
    base_manoeuvre_cost: float,
    timing: str = "less_than_6h",
) -> float:
    """Estimate cancellation/alteration fee.

    Parameters:
        base_manoeuvre_cost: The base cost of the cancelled manoeuvre.
        timing: When the cancellation occurred relative to scheduled time.

    Returns:
        Cancellation fee in euros.
    """
    factor = CANCELLATION_SCHEDULE.get(timing, 0.75)
    return round(base_manoeuvre_cost * factor, 2)


def calculate_scale_cost(
    vessel_name: str,
    gt: float,
    manoeuvres: List[ManoeuvreInput],
    stay_days: float = 1.0,
    include_tup: bool = True,
) -> ScaleCostEstimate:
    """Calculate complete cost estimate for a port call.

    Parameters:
        vessel_name: Name of the vessel.
        gt: Gross tonnage.
        manoeuvres: List of manoeuvre inputs to calculate.
        stay_days: Estimated days in port (for TUP calculation).
        include_tup: Whether to include TUP estimate.

    Returns:
        ScaleCostEstimate with full breakdown.
    """
    results = []
    pilotage_total = 0.0
    for m in manoeuvres:
        m.gt = gt  # Ensure consistent GT across all manoeuvres
        result = calculate_manoeuvre_cost(m)
        results.append(result)
        pilotage_total += result.total_cost

    tup = estimate_tup(gt, stay_days) if include_tup else 0.0
    grand_total = pilotage_total + tup

    notes = [
        "Valores estimados com base no tarifário de pilotagem de Setúbal 2024.",
        "Não inclui rebocadores (serviço privado), amarração, lanchas ou resíduos.",
        "Os valores reais podem ser inferiores por acordos comerciais ou GT reduzido.",
    ]
    if include_tup:
        notes.append(f"TUP estimada para {stay_days:.0f} dia(s): {tup:.2f} €.")

    return ScaleCostEstimate(
        vessel_name=vessel_name,
        gt=gt,
        manoeuvres=results,
        pilotage_total=round(pilotage_total, 2),
        tup_estimate=tup,
        stay_days=stay_days,
        grand_total=round(grand_total, 2),
        notes=notes,
    )


def format_cost_summary(estimate: ScaleCostEstimate) -> str:
    """Format a scale cost estimate as a human-readable summary.

    Parameters:
        estimate: The calculated estimate.

    Returns:
        Multi-line formatted string.
    """
    lines = [
        f"Estimativa de custos de pilotagem — {estimate.vessel_name}",
        f"GT: {estimate.gt:.0f} t",
        "",
    ]

    for i, m in enumerate(estimate.manoeuvres, start=1):
        lines.append(f"Manobra {i}: {m.manoeuvre_type}")
        for b in m.breakdown:
            lines.append(f"  {b}")
        lines.append("")

    lines.append(f"Total pilotagem: {estimate.pilotage_total:.2f} €")
    if estimate.tup_estimate > 0:
        lines.append(
            f"TUP estimada ({estimate.stay_days:.0f} dia(s)): {estimate.tup_estimate:.2f} €"
        )
    lines.append(f"Total estimado: {estimate.grand_total:.2f} €")
    lines.append("")

    for note in estimate.notes:
        lines.append(f"• {note}")

    return "\n".join(lines)


def quick_estimate(gt: float, manoeuvre_type: str = "entry") -> dict:
    """Quick cost estimate for chatbot integration.

    Parameters:
        gt: Gross tonnage.
        manoeuvre_type: Type string ('entry', 'departure', 'shift').

    Returns:
        Dictionary with cost breakdown suitable for JSON response.
    """
    type_map = {
        "entry": ManoeuvreType.ENTRY,
        "entrada": ManoeuvreType.ENTRY,
        "departure": ManoeuvreType.DEPARTURE,
        "saida": ManoeuvreType.DEPARTURE,
        "saída": ManoeuvreType.DEPARTURE,
        "shift": ManoeuvreType.SHIFT,
        "mudanca": ManoeuvreType.SHIFT,
        "mudança": ManoeuvreType.SHIFT,
    }
    m_type = type_map.get(manoeuvre_type.lower().strip(), ManoeuvreType.ENTRY)

    result = calculate_manoeuvre_cost(ManoeuvreInput(
        manoeuvre_type=m_type,
        gt=gt,
    ))

    return {
        "manoeuvre_type": result.manoeuvre_type,
        "gt": result.gt,
        "up_rate": result.up_rate,
        "base_cost": result.base_cost,
        "total_cost": result.total_cost,
        "breakdown": result.breakdown,
        "currency": "EUR",
        "tariff_year": 2024,
        "note": "Valor estimado sem reduções/agravamentos. Não inclui rebocadores nem TUP.",
    }
