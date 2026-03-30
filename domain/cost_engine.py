"""Pilotage cost calculation engine for the Port of Setúbal.

Implements the tariff structure defined in the Port Pilotage Regulation
(Regulamento de Tarifas da APSS 2024) with support for:
- Base pilotage fee: T = UP × √GT (Art. 15º, nº 1)
- Multiple manoeuvre types (entry, departure, shift, anchoring)
- Surcharges (+25% for special conditions, Art. 15º nº 3)
- Reductions (regular line, cabotage, technical calls, Art. 16º)
- Standby pilotage 'à ordem' (Art. 15º nº 4)
- Cancellation fees (Art. 15º nº 7)
- TUP calculation based on vessel type (Art. 9º)
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
# Constants — Setúbal 2024 Tariff Schedule (based on APSS Regulation)
# ---------------------------------------------------------------------------

# Base pilotage unit (€/√GT) — Art. 15º, nº 2 a)
UP_NORMAL = 9.2578        # Standard manoeuvres (entry, exit, berth, unberth)
UP_SHIFT_ALONG = 3.3628   # Running along the quay (shift)

# Standby pilotage — Art. 15º, nº 4
STANDBY_HOURLY_RATE = 74.6432    # €/hour waiting time (exact from regulation)
STANDBY_BASE_SURCHARGE = 0.25    # +25% of base fee

# Surcharge factors — Art. 15º, nº 3
SURCHARGE_NO_PROPULSION = 0.25      # +25% vessel without propulsion
SURCHARGE_SPECIAL_ASSISTANCE = 0.25  # +25% special assistance (needles, etc.)

# Reduction factors — Art. 16º
REDUCTION_REGULAR_LINE_TIERED = {  # based on number of calls in last 365 days
    6: 0.10, 25: 0.15, 53: 0.20, 101: 0.25
}
REDUCTION_CABOTAGE = 0.10        # -10% cabotage (Art. 16º, nº 1 c))
REDUCTION_TECHNICAL_CALL = 0.30  # -30% technical/emergency call (Art. 16º, nº 1 a))

# TUP rates (€/GT per 24h) — Art. 9º
TUP_RATES = {
    "contentores": (0.1144, 0.0263),
    "roll-on_roll-off": (0.1186, 0.0274),
    "passageiros": (0.0620, 0.0263),
    "tanque": (0.1459, 0.0274),
    "restantes": (0.1459, 0.0274),
}

# Cancellation fee schedule — Art. 15º, nº 7
# Values represent the percentage of the base manoeuvre cost that remains payable.
CANCELLATION_FEE_PERCENT = {
    "cancelled_2h_before": 0.30,        # 70% reduction
    "cancelled_1h_after": 0.50,         # 50% reduction
    "cancelled_after_1h": 1.00,         # 100% payable
    "weather_embarked": 0.25,           # 75% reduction (pilot already embarked)
}
# Note: Alterations (agravamentos) are not directly implemented in this simplified engine;
# they would increase the fee (see Art. 15º, nº 7 e-h).


class ManoeuvreType(str, Enum):
    """Types of pilotage manoeuvres, each billed individually."""
    ENTRY = "entry"
    DEPARTURE = "departure"
    SHIFT = "shift"
    ANCHORING = "anchoring"
    TRIALS = "trials"
    STANDBY = "standby"


class SurchargeType(str, Enum):
    """Possible surcharge conditions (both apply cumulatively)."""
    NO_PROPULSION = "no_propulsion"
    SPECIAL_ASSISTANCE = "special_assistance"


class ReductionType(str, Enum):
    """Possible reduction conditions (only the highest applies)."""
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
        standby_hours: Hours of standby time to add to the manoeuvre total.
                       For normal manoeuvres, this can represent the excess time
                       billed as pilotagem à ordem after the first 3 hours.
        custom_up: Override UP rate (€/√GT) if different from default.
        regular_line_calls: Number of calls in last 365 days (for tiered reduction).
    """
    manoeuvre_type: ManoeuvreType
    gt: float
    surcharges: List[SurchargeType] = field(default_factory=list)
    reductions: List[ReductionType] = field(default_factory=list)
    standby_hours: float = 0.0
    custom_up: Optional[float] = None
    regular_line_calls: Optional[int] = None


@dataclass
class ManoeuvreResult:
    """Result of a single manoeuvre cost calculation.

    Attributes:
        manoeuvre_type: Type of manoeuvre calculated.
        gt: Gross tonnage used.
        up_rate: UP rate applied (€/√GT).
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
        vessel_type: Type of vessel (for TUP calculation).
        manoeuvres: List of individual manoeuvre results.
        pilotage_total: Total pilotage cost (€).
        tup_estimate: Estimated TUP cost (€).
        stay_days: Estimated stay in port (days).
        grand_total: Pilotage + TUP estimate (€).
        notes: Additional notes about the estimate.
    """
    vessel_name: str
    gt: float
    vessel_type: str
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
    """Determine the UP rate for a given manoeuvre type (Art. 15º, nº 2 a)."""
    if custom_up is not None and custom_up > 0:
        return custom_up
    if manoeuvre_type == ManoeuvreType.SHIFT:
        return UP_SHIFT_ALONG
    return UP_NORMAL


def _surcharge_factor(surcharges: List[SurchargeType]) -> float:
    """Calculate cumulative surcharge factor (Art. 15º, nº 3).

    Both conditions add 25% each, so total can be up to 50% if both apply.
    """
    factor = 0.0
    if SurchargeType.NO_PROPULSION in surcharges:
        factor += SURCHARGE_NO_PROPULSION
    if SurchargeType.SPECIAL_ASSISTANCE in surcharges:
        factor += SURCHARGE_SPECIAL_ASSISTANCE
    return factor


def _reduction_factor(reductions: List[ReductionType],
                      regular_line_calls: Optional[int] = None) -> float:
    """Calculate the best applicable reduction factor (Art. 16º).

    Only the highest reduction applies (not cumulative). For regular line,
    the percentage depends on the number of calls in the last 365 days.
    """
    factors = []
    if ReductionType.TECHNICAL_CALL in reductions:
        factors.append(REDUCTION_TECHNICAL_CALL)
    if ReductionType.CABOTAGE in reductions:
        factors.append(REDUCTION_CABOTAGE)
    if ReductionType.REGULAR_LINE in reductions and regular_line_calls is not None:
        # Determine tiered reduction based on number of calls
        for threshold, pct in sorted(REDUCTION_REGULAR_LINE_TIERED.items()):
            if regular_line_calls >= threshold:
                factors.append(pct)
    return max(factors) if factors else 0.0


def calculate_manoeuvre_cost(manoeuvre: ManoeuvreInput) -> ManoeuvreResult:
    """Calculate the cost of a single pilotage manoeuvre (Art. 15º)."""
    gt = max(manoeuvre.gt, 0.0)
    up_rate = _get_up_rate(manoeuvre.manoeuvre_type, manoeuvre.custom_up)
    base_cost = up_rate * math.sqrt(gt)
    breakdown = [f"Base: {up_rate:.4f} €/√GT × √{gt:.0f} (={math.sqrt(gt):.2f}) = {base_cost:.2f} €"]

    # Surcharges (Art. 15º, nº 3)
    surcharge_pct = _surcharge_factor(manoeuvre.surcharges)
    surcharge_amount = base_cost * surcharge_pct
    if surcharge_amount > 0:
        labels = [s.value.replace("_", " ").title() for s in manoeuvre.surcharges]
        breakdown.append(
            f"Agravamento (+{surcharge_pct * 100:.0f}%): +{surcharge_amount:.2f} € "
            f"[{', '.join(labels)}]"
        )

    # Reductions (Art. 16º)
    reduction_pct = _reduction_factor(manoeuvre.reductions, manoeuvre.regular_line_calls)
    reduction_amount = base_cost * reduction_pct
    if reduction_amount > 0:
        labels = [r.value.replace("_", " ").title() for r in manoeuvre.reductions]
        breakdown.append(
            f"Redução (-{reduction_pct * 100:.0f}%): -{reduction_amount:.2f} € "
            f"[{', '.join(labels)}]"
        )

    # Standby component (Art. 15º, nº 4)
    standby_cost = 0.0
    if manoeuvre.standby_hours > 0:
        standby_hourly = STANDBY_HOURLY_RATE * manoeuvre.standby_hours
        standby_base = base_cost * STANDBY_BASE_SURCHARGE
        standby_cost = standby_hourly + standby_base
        if manoeuvre.manoeuvre_type == ManoeuvreType.STANDBY:
            breakdown.append(
                f"Pilotagem à ordem: {STANDBY_HOURLY_RATE:.2f} €/h × {manoeuvre.standby_hours:.1f}h "
                f"+ 25% base ({standby_base:.2f} €) = {standby_cost:.2f} €"
            )
        else:
            breakdown.append(
                f"Horas à ordem: {STANDBY_HOURLY_RATE:.2f} €/h × {manoeuvre.standby_hours:.1f}h "
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


def calculate_tup(gt: float, vessel_type: str, stay_days: float) -> float:
    """Calculate Port Usage Tariff (TUP) according to Art. 9º.

    Parameters:
        gt: Gross tonnage.
        vessel_type: One of 'contentores', 'roll-on_roll-off', 'passageiros',
                     'tanque', 'restantes'.
        stay_days: Number of days (or fraction) in port.

    Returns:
        TUP cost in euros.
    """
    # Normalize vessel type
    vt = vessel_type.lower().strip()
    if vt not in TUP_RATES:
        vt = "restantes"
    first_rate, subsequent_rate = TUP_RATES[vt]

    if stay_days <= 1.0:
        # First period only
        tup = first_rate * gt
    else:
        full_days = int(stay_days)  # integer part
        fraction = stay_days - full_days
        # First day
        total = first_rate * gt
        # Full subsequent days
        total += subsequent_rate * gt * (full_days - 1)
        # Partial last day (if any) – charge as a full subsequent day (períodos indivisíveis)
        if fraction > 0:
            total += subsequent_rate * gt
        tup = total
    return round(tup, 2)


def estimate_tup(gt: float, vessel_type: str, stay_days: float = 1.0) -> float:
    """Alias for calculate_tup."""
    return calculate_tup(gt, vessel_type, stay_days)


def calculate_cancellation_fee(base_manoeuvre_cost: float,
                               cancellation_type: str,
                               with_pilot_embarked: bool = False) -> float:
    """Calculate cancellation/alteration fee according to Art. 15º, nº 7.

    Parameters:
        base_manoeuvre_cost: The base pilotage cost of the cancelled manoeuvre (before any
                             surcharges/reductions).
        cancellation_type: One of '2h_before', '1h_after', 'after_1h'.
        with_pilot_embarked: If True and weather conditions, use the weather reduction.

    Returns:
        Cancellation fee in euros.
    """
    if with_pilot_embarked:
        # Weather cancellation with pilot already on board
        percent = CANCELLATION_FEE_PERCENT["weather_embarked"]
    else:
        percent = CANCELLATION_FEE_PERCENT.get(
            f"cancelled_{cancellation_type}",
            CANCELLATION_FEE_PERCENT["cancelled_after_1h"]
        )
    return round(base_manoeuvre_cost * percent, 2)


def calculate_scale_cost(
    vessel_name: str,
    gt: float,
    vessel_type: str,
    manoeuvres: List[ManoeuvreInput],
    stay_days: float = 1.0,
    include_tup: bool = True,
) -> ScaleCostEstimate:
    """Calculate complete cost estimate for a port call.

    Parameters:
        vessel_name: Name of the vessel.
        gt: Gross tonnage.
        vessel_type: Type of vessel (for TUP calculation).
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

    tup = calculate_tup(gt, vessel_type, stay_days) if include_tup else 0.0
    grand_total = pilotage_total + tup

    notes = [
        "Valores estimados com base no tarifário de pilotagem de Setúbal 2024.",
        "Não inclui rebocadores (serviço privado), amarração, lanchas ou resíduos.",
        "Os valores reais podem ser inferiores por acordos comerciais ou GT reduzido.",
    ]
    if include_tup:
        notes.append(f"TUP calculada para {stay_days:.1f} dia(s): {tup:.2f} € (tipo: {vessel_type}).")

    return ScaleCostEstimate(
        vessel_name=vessel_name,
        gt=gt,
        vessel_type=vessel_type,
        manoeuvres=results,
        pilotage_total=round(pilotage_total, 2),
        tup_estimate=tup,
        stay_days=stay_days,
        grand_total=round(grand_total, 2),
        notes=notes,
    )


def format_cost_summary(estimate: ScaleCostEstimate) -> str:
    """Format a scale cost estimate as a human-readable summary."""
    lines = [
        f"Estimativa de custos de pilotagem — {estimate.vessel_name}",
        f"GT: {estimate.gt:.0f} t",
        f"Tipo de navio: {estimate.vessel_type}",
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
            f"TUP ({estimate.stay_days:.1f} dia(s)): {estimate.tup_estimate:.2f} €"
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


# ---------------------------------------------------------------------------
# Terminal Interactive Interface
# ---------------------------------------------------------------------------

def _ask_yes_no(prompt: str) -> bool:
    """Ask a yes/no question, return True/False."""
    while True:
        answer = input(f"{prompt} (s/n): ").strip().lower()
        if answer in ('s', 'sim', 'y', 'yes'):
            return True
        if answer in ('n', 'nao', 'não', 'no'):
            return False
        print("Responda 's' ou 'n'.")


def _ask_int(prompt: str, min_val: int = 1) -> int:
    """Ask for an integer with validation."""
    while True:
        try:
            val = int(input(f"{prompt}: "))
            if val >= min_val:
                return val
            print(f"Valor deve ser >= {min_val}.")
        except ValueError:
            print("Por favor, introduza um número inteiro.")


def _ask_float(prompt: str, min_val: float = 0.0) -> float:
    """Ask for a float with validation."""
    while True:
        try:
            val = float(input(f"{prompt}: "))
            if val >= min_val:
                return val
            print(f"Valor deve ser >= {min_val}.")
        except ValueError:
            print("Por favor, introduza um número.")


def _choose_from_list(prompt: str, options: List[str]) -> str:
    """Display a list of options and ask user to choose one."""
    print(prompt)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        try:
            choice = int(input("Escolha (número): "))
            if 1 <= choice <= len(options):
                return options[choice - 1]
            print(f"Escolha um número entre 1 e {len(options)}.")
        except ValueError:
            print("Introduza um número.")


def _ask_manoeuvre_input(gt: float) -> ManoeuvreInput:
    """Interactive creation of a manoeuvre input."""
    # Choose manoeuvre type
    m_type_str = _choose_from_list(
        "Tipo de manobra:",
        ["Entrada", "Saída", "Mudança de cais", "Fundeadouro", "Experiências", "Pilotagem à ordem"]
    )
    m_type_map = {
        "Entrada": ManoeuvreType.ENTRY,
        "Saída": ManoeuvreType.DEPARTURE,
        "Mudança de cais": ManoeuvreType.SHIFT,
        "Fundeadouro": ManoeuvreType.ANCHORING,
        "Experiências": ManoeuvreType.TRIALS,
        "Pilotagem à ordem": ManoeuvreType.STANDBY,
    }
    m_type = m_type_map[m_type_str]

    surcharges = []
    # Agravamento por falta de propulsão própria
    if not _ask_yes_no("A embarcação tem propulsão própria?"):
        surcharges.append(SurchargeType.NO_PROPULSION)
    if _ask_yes_no("O piloto presta assistência especial (regulação/compensação de agulhas)?"):
        surcharges.append(SurchargeType.SPECIAL_ASSISTANCE)

    reductions = []
    regular_line_calls = None
    if _ask_yes_no("Aplica redução por linha regular?"):
        reductions.append(ReductionType.REGULAR_LINE)
        regular_line_calls = _ask_int("Número de escalas nos últimos 365 dias")
    if _ask_yes_no("Aplica redução por cabotagem nacional?"):
        reductions.append(ReductionType.CABOTAGE)
    if _ask_yes_no("Aplica redução por escala técnica (abastecimento/aguada/etc.)?"):
        reductions.append(ReductionType.TECHNICAL_CALL)

    standby_hours = 0.0
    if m_type == ManoeuvreType.STANDBY:
        standby_hours = _ask_float("Horas de pilotagem à ordem (excluindo o tempo normal de espera)")

    return ManoeuvreInput(
        manoeuvre_type=m_type,
        gt=gt,
        surcharges=surcharges,
        reductions=reductions,
        standby_hours=standby_hours,
        regular_line_calls=regular_line_calls,
    )


def main():
    print("=== Calculadora de Custos de Pilotagem - Porto de Setúbal ===\n")

    vessel_name = input("Nome do navio (opcional): ").strip()
    if not vessel_name:
        vessel_name = "Navio"

    gt = _ask_float("Arqueação bruta (GT)", min_val=0.0)

    # Choose vessel type for TUP
    vessel_type = _choose_from_list(
        "Tipo de navio para cálculo da TUP:",
        ["Contentores", "Roll-on Roll-off", "Passageiros", "Tanque", "Restantes"]
    )
    # Normalise to key used in TUP_RATES
    vessel_type_map = {
        "Contentores": "contentores",
        "Roll-on Roll-off": "roll-on_roll-off",
        "Passageiros": "passageiros",
        "Tanque": "tanque",
        "Restantes": "restantes",
    }
    vessel_type_key = vessel_type_map[vessel_type]

    n_manoeuvres = _ask_int("Número de manobras de pilotagem", min_val=1)

    manoeuvres = []
    for i in range(1, n_manoeuvres + 1):
        print(f"\n--- Manobra {i} ---")
        manoeuvres.append(_ask_manoeuvre_input(gt))

    include_tup = _ask_yes_no("Incluir Tarifa de Uso do Porto (TUP)?")
    stay_days = 1.0
    if include_tup:
        stay_days = _ask_float("Tempo de permanência no porto (dias)", min_val=0.1)

    print("\nA calcular...\n")
    estimate = calculate_scale_cost(
        vessel_name=vessel_name,
        gt=gt,
        vessel_type=vessel_type_key,
        manoeuvres=manoeuvres,
        stay_days=stay_days,
        include_tup=include_tup,
    )

    print(format_cost_summary(estimate))


if __name__ == "__main__":
    main()
