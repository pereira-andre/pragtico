"""Admin-only operational flow tests for port calls and maneuvers."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable

from core import services
from core.form_helpers import (
    ensure_maneuver_hour_capacity_for_approval,
    ensure_portal_berth_is_available,
    ensure_portal_berth_is_physically_available,
)
from core.operational_actions import answer_slash_query
from core.operational_sources import answer_direct_operational_query
from core.portal_notifications import latest_maneuver_by_type
from domain.chat_action_templates import build_slash_help
from domain.chat_actions import parse_slash_command


TEST_VESSEL_PREFIX = "TESTE QA"


def _label_now() -> str:
    return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")


def _state_from_steps(steps: list[dict]) -> str:
    if any(step.get("state") == "failed" for step in steps):
        return "failed"
    if any(step.get("state") == "warning" for step in steps):
        return "warning"
    return "passed"


def _status_badge(state: str) -> str:
    return {
        "passed": "online",
        "failed": "offline",
        "warning": "degraded",
    }.get(state, "degraded")


def cleanup_operational_test_records() -> dict:
    """Delete previous records created by this controlled admin test suite."""
    started = time.perf_counter()
    deleted: list[dict] = []
    errors: list[str] = []
    try:
        records = services.store.list_port_calls()
    except Exception as exc:
        return {
            "kind": "cleanup",
            "state": "failed",
            "state_label": "Falhou",
            "state_badge": "offline",
            "started_at_label": _label_now(),
            "finished_at_label": _label_now(),
            "duration_ms": 0,
            "deleted": [],
            "deleted_count": 0,
            "errors": [f"Não foi possível listar escalas: {exc}"],
        }

    targets = [
        record
        for record in records
        if str(record.get("vessel_name") or "").strip().upper().startswith(TEST_VESSEL_PREFIX)
    ]
    for record in targets:
        try:
            removed = services.store.delete_port_call(record["id"])
            deleted.append(
                {
                    "id": removed.get("id"),
                    "reference_code": removed.get("reference_code"),
                    "vessel_name": removed.get("vessel_name"),
                }
            )
        except Exception as exc:
            errors.append(f"{record.get('reference_code') or record.get('id')}: {exc}")

    state = "failed" if errors else "passed"
    return {
        "kind": "cleanup",
        "state": state,
        "state_label": "Falhou" if errors else "Limpeza concluída",
        "state_badge": _status_badge(state),
        "started_at_label": _label_now(),
        "finished_at_label": _label_now(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "errors": errors,
    }


def operational_test_inventory() -> dict:
    """Return current retained test records so the admin page can show cleanup state."""
    try:
        records = services.store.list_port_calls()
    except Exception as exc:
        return {"count": 0, "items": [], "error": str(exc)}
    items = [
        {
            "id": record.get("id"),
            "reference_code": record.get("reference_code"),
            "vessel_name": record.get("vessel_name"),
            "status_label": record.get("status_label") or record.get("status"),
        }
        for record in records
        if str(record.get("vessel_name") or "").strip().upper().startswith(TEST_VESSEL_PREFIX)
    ]
    return {"count": len(items), "items": items, "error": ""}


class OperationalFlowSuite:
    def __init__(self, *, actor_username: str, cleanup_after: bool = True):
        self.actor_username = actor_username or "admin"
        self.cleanup_after = cleanup_after
        self.started_at = datetime.now().astimezone()
        self.started_perf = time.perf_counter()
        self.run_token = self.started_at.strftime("%H%M%S")
        self.created_ids: list[str] = []
        self.retained_records: list[dict] = []
        self.scenarios: list[dict] = []
        self.seed_base = 9100000 + int(self.started_at.timestamp()) % 800000

    def _future(self, *, days: int, hour: int, minute: int = 0) -> str:
        candidate = (self.started_at + timedelta(days=days)).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= self.started_at:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    def _event_time(self, *, days: int, hour: int, minute: int = 0) -> str:
        return self._future(days=days, hour=hour, minute=minute)

    def _imo(self, index: int) -> str:
        return str(self.seed_base + index)[-7:].zfill(7)

    def _profile(self, index: int, *, vessel_type: str = "Carga geral") -> dict:
        return {
            "vessel_short_name": "",
            "vessel_imo": self._imo(index),
            "vessel_call_sign": f"TQA{self.run_token[-4:]}{index:02d}",
            "vessel_flag": "Portugal",
            "vessel_type": vessel_type,
            "vessel_loa_m": "148.0",
            "vessel_beam_m": "23.0",
            "vessel_gt_t": "12600",
            "vessel_dwt_t": "18200",
            "vessel_max_draft_m": "8.6",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "unknown",
        }

    def _new_scenario(self, module: str, name: str, purpose: str) -> dict:
        scenario = {
            "module": module,
            "name": name,
            "purpose": purpose,
            "state": "running",
            "state_badge": "degraded",
            "steps": [],
        }
        self.scenarios.append(scenario)
        return scenario

    def _record_step(
        self,
        scenario: dict,
        *,
        label: str,
        element: str,
        expected: str,
        observed: str,
        state: str,
    ) -> None:
        scenario["steps"].append(
            {
                "label": label,
                "element": element,
                "expected": expected,
                "observed": observed,
                "state": state,
                "state_badge": _status_badge(state),
            }
        )

    def _step(
        self,
        scenario: dict,
        label: str,
        element: str,
        expected: str,
        action: Callable[[], Any],
        *,
        expected_error: str = "",
    ) -> Any:
        try:
            value = action()
        except Exception as exc:
            observed = str(exc)
            if expected_error and expected_error.casefold() in observed.casefold():
                self._record_step(
                    scenario,
                    label=label,
                    element=element,
                    expected=expected,
                    observed=observed,
                    state="passed",
                )
                return exc
            self._record_step(
                scenario,
                label=label,
                element=element,
                expected=expected,
                observed=observed,
                state="failed",
            )
            return None

        if expected_error:
            self._record_step(
                scenario,
                label=label,
                element=element,
                expected=expected,
                observed="A operação passou, mas devia ter sido bloqueada.",
                state="failed",
            )
            return value

        self._record_step(
            scenario,
            label=label,
            element=element,
            expected=expected,
            observed=self._summarize(value),
            state="passed",
        )
        return value

    def _check(
        self,
        scenario: dict,
        label: str,
        element: str,
        expected: str,
        condition: bool,
        observed: str,
    ) -> None:
        self._record_step(
            scenario,
            label=label,
            element=element,
            expected=expected,
            observed=observed,
            state="passed" if condition else "failed",
        )

    def _finish_scenario(self, scenario: dict) -> None:
        scenario["state"] = _state_from_steps(scenario["steps"])
        scenario["state_badge"] = _status_badge(scenario["state"])

    def _summarize(self, value: Any) -> str:
        if isinstance(value, dict):
            vessel = value.get("vessel_name")
            ref = value.get("reference_code") or value.get("id")
            status = value.get("status_label") or value.get("status")
            if vessel or ref:
                return " · ".join(str(part) for part in (ref, vessel, status) if part)
        return "OK"

    def _create_port_call(
        self,
        scenario: dict,
        *,
        index: int,
        name: str,
        eta: str,
        berth: str,
        last_port: str,
        next_port: str,
        vessel_type: str = "Carga geral",
        constraints: list[str] | None = None,
        tug_count: str = "2",
    ) -> dict | None:
        full_name = f"{TEST_VESSEL_PREFIX} {name} {self.run_token}"
        profile = self._profile(index, vessel_type=vessel_type)
        record = self._step(
            scenario,
            "Criar escala",
            "Registo de escala e manobra de entrada inicial",
            "Escala criada em estado pendente, com entrada inicial pendente.",
            lambda: services.store.create_port_call(
                vessel_name=full_name,
                eta=eta,
                created_by=self.actor_username,
                constraints=constraints or [],
                berth=berth,
                last_port=last_port,
                next_port=next_port,
                notes=f"Cenário automático de teste: {name}.",
                **profile,
            ),
        )
        if isinstance(record, dict):
            self.created_ids.append(record["id"])
            self.retained_records.append(
                {
                    "id": record.get("id"),
                    "reference_code": record.get("reference_code"),
                    "vessel_name": record.get("vessel_name"),
                }
            )
        return record if isinstance(record, dict) else None

    def _latest(self, port_call: dict, maneuver_type: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, maneuver_type)
        if not maneuver:
            raise ValueError(f"Sem manobra {maneuver_type} na escala.")
        return maneuver

    def _approve_entry(self, port_call: dict) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "entry")
        if not maneuver:
            raise ValueError("Sem entrada pendente.")
        ensure_maneuver_hour_capacity_for_approval(current, "entry")
        ensure_portal_berth_is_available(
            maneuver.get("destination", ""),
            current_port_call_id=current["id"],
            target_planned_at=maneuver.get("planned_at"),
        )
        return services.store.approve_port_call(
            current["id"],
            decided_by=self.actor_username,
            approval_note="Teste: entrada validada.",
        )

    def _approve_departure(self, port_call: dict) -> dict:
        current = services.store.get_port_call(port_call["id"])
        ensure_maneuver_hour_capacity_for_approval(current, "departure")
        return services.store.approve_port_call(
            current["id"],
            decided_by=self.actor_username,
            approval_note="Teste: saída validada.",
        )

    def _approve_shift(self, port_call: dict) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "shift")
        if not maneuver:
            raise ValueError("Sem mudança pendente.")
        ensure_maneuver_hour_capacity_for_approval(current, "shift")
        ensure_portal_berth_is_available(
            maneuver.get("destination", ""),
            current_port_call_id=current["id"],
            target_planned_at=maneuver.get("planned_at"),
            label="Cais destino",
        )
        return services.store.approve_shift_plan(
            current["id"],
            decided_by=self.actor_username,
            approval_note="Teste: mudança validada.",
        )

    def _complete_entry(self, port_call: dict, *, at: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "entry")
        if not maneuver:
            raise ValueError("Sem entrada para concluir.")
        berth = ensure_portal_berth_is_physically_available(
            maneuver.get("destination") or current.get("berth", ""),
            current_port_call_id=current["id"],
        )
        return services.store.mark_port_call_arrived(
            current["id"],
            arrived_at=at,
            updated_by=self.actor_username,
            berth=berth,
        )

    def _complete_departure(self, port_call: dict, *, at: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        return services.store.mark_port_call_departed(
            current["id"],
            departed_at=at,
            updated_by=self.actor_username,
            next_port=current.get("next_port", ""),
        )

    def _complete_shift(self, port_call: dict, *, at: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "shift")
        if not maneuver:
            raise ValueError("Sem mudança para concluir.")
        ensure_portal_berth_is_physically_available(
            maneuver.get("destination") or current.get("shift_destination_berth", ""),
            current_port_call_id=current["id"],
            label="Cais destino",
        )
        return services.store.mark_shift_completed(
            current["id"],
            shifted_at=at,
            updated_by=self.actor_username,
        )

    def _scenario_natural_flow(self) -> None:
        scenario = self._new_scenario(
            "Fluxo de escala",
            "Fluxo natural de escala",
            "Cria uma escala, planeia saída ainda antes da entrada, valida, conclui entrada, valida saída e fecha a escala.",
        )
        port_call = self._create_port_call(
            scenario,
            index=1,
            name="FLUXO PRINCIPAL",
            eta=self._future(days=2, hour=8),
            berth="Secil W",
            last_port="Sines",
            next_port="Vigo",
            vessel_type="Graneis sólidos",
            constraints=["daylight"],
        )
        if not port_call:
            self._finish_scenario(scenario)
            return

        entry = self._latest(port_call, "entry")
        self._check(
            scenario,
            "Entrada inicial",
            "Manobra criada automaticamente",
            "A entrada nasce pendente e com destino no cais previsto.",
            entry.get("state") == "pending" and entry.get("destination") == "Secil W",
            f"{entry.get('type')} · {entry.get('state')} · {entry.get('destination')}",
        )
        port_call = self._step(
            scenario,
            "Planear saída antes da entrada",
            "Planeamento de manobras posteriores",
            "A saída pode ser marcada sem esperar pela entrada concluída.",
            lambda: services.store.schedule_departure_plan(
                port_call["id"],
                planned_departure_at=self._future(days=3, hour=18),
                updated_by=self.actor_username,
                next_port="Vigo",
                constraints=["daylight"],
                departure_plan_note="Teste: saída já planeada antes da entrada.",
                draft_m="7.9",
                tug_count="2",
            ),
        ) or port_call
        if not isinstance(port_call, dict):
            self._finish_scenario(scenario)
            return

        port_call = self._step(
            scenario,
            "Aprovar entrada",
            "Validação da pilotagem",
            "A entrada fica aprovada e guarda quem aprovou.",
            lambda: self._approve_entry(port_call),
        ) or port_call
        approved_entry = self._latest(port_call, "entry")
        self._check(
            scenario,
            "Registo do validador",
            "Quem aprovou a manobra",
            "A entrada tem decided_by preenchido.",
            bool(approved_entry.get("decided_by")),
            approved_entry.get("decided_by") or "--",
        )

        port_call = self._step(
            scenario,
            "Concluir entrada",
            "Ocupação física do cais",
            "A escala passa a navio em porto e o cais fica ocupado.",
            lambda: self._complete_entry(port_call, at=self._event_time(days=2, hour=9)),
        ) or port_call
        self._check(
            scenario,
            "Estado em porto",
            "Estado da escala após ATA",
            "Estado in_port.",
            port_call.get("status") == "in_port",
            port_call.get("status", "--"),
        )

        entry_id = self._latest(port_call, "entry").get("id")
        port_call = self._step(
            scenario,
            "Registar pilotagem da entrada",
            "Registo executante e ficha de manobra",
            "A entrada recebe report_note e reported_by.",
            lambda: services.store.attach_entry_report(
                port_call["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=2, hour=8, minute=10),
                maneuver_finished_at=self._event_time(days=2, hour=9),
                draft_m="7.8",
                notes="Teste: entrada concluída sem ocorrências.",
                maneuver_id=entry_id,
            ),
        ) or port_call

        port_call = self._step(
            scenario,
            "Aprovar saída",
            "Validação de saída",
            "A saída fica aprovada e pronta para conclusão.",
            lambda: self._approve_departure(port_call),
        ) or port_call
        port_call = self._step(
            scenario,
            "Concluir saída",
            "Fecho operacional da escala",
            "A escala passa para departed.",
            lambda: self._complete_departure(port_call, at=self._event_time(days=3, hour=18, minute=45)),
        ) or port_call
        departure_id = self._latest(port_call, "departure").get("id")
        port_call = self._step(
            scenario,
            "Registar pilotagem da saída",
            "Arquivo da manobra",
            "A saída recebe report_note e executante.",
            lambda: services.store.attach_departure_report(
                port_call["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=3, hour=18),
                maneuver_finished_at=self._event_time(days=3, hour=18, minute=45),
                draft_m="7.6",
                notes="Teste: saída concluída e escala encerrada.",
                maneuver_id=departure_id,
            ),
        ) or port_call
        final = services.store.get_port_call(port_call["id"])
        self._check(
            scenario,
            "Estado final",
            "Escala concluída",
            "Estado departed com entrada e saída concluídas.",
            final.get("status") == "departed"
            and self._latest(final, "entry").get("state") == "completed"
            and self._latest(final, "departure").get("state") == "completed",
            f"{final.get('status')} · entrada {self._latest(final, 'entry').get('state')} · saída {self._latest(final, 'departure').get('state')}",
        )
        snapshot = services.store.get_port_activity_snapshot(window_days=3650)
        self._check(
            scenario,
            "Live feed operacional",
            "Saídas recentes/arquivo",
            "A saída concluída aparece no snapshot operacional.",
            any(row.get("port_call_id") == final["id"] for row in snapshot.get("departed", [])),
            f"{len(snapshot.get('departed', []))} saída(s) no snapshot.",
        )
        self._finish_scenario(scenario)

    def _scenario_shift_flow(self) -> None:
        scenario = self._new_scenario(
            "Fluxo de escala",
            "Mudança de cais",
            "Valida que uma escala pode entrar, mudar de cais, registar a mudança e sair no fim.",
        )
        port_call = self._create_port_call(
            scenario,
            index=2,
            name="MUDANCA",
            eta=self._future(days=4, hour=7),
            berth="Cais 10 / Autoeuropa",
            last_port="Southampton",
            next_port="Leixões",
            vessel_type="Roll-on/Roll-off",
        )
        if not port_call:
            self._finish_scenario(scenario)
            return
        port_call = self._step(
            scenario,
            "Aprovar e concluir entrada",
            "Entrada antes da mudança",
            "Navio fica em porto no cais inicial.",
            lambda: self._complete_entry(
                self._approve_entry(port_call),
                at=self._event_time(days=4, hour=8),
            ),
        ) or port_call
        port_call = self._step(
            scenario,
            "Planear mudança",
            "Manobra shift",
            "A mudança fica pendente para outro cais.",
            lambda: services.store.schedule_shift_plan(
                port_call["id"],
                planned_shift_at=self._future(days=4, hour=13),
                updated_by=self.actor_username,
                destination_berth="Cais 11 / Autoeuropa",
                constraints=[],
                shift_plan_note="Teste: mudança para cais adjacente.",
                draft_m="8.1",
                tug_count="1",
            ),
        ) or port_call
        port_call = self._step(
            scenario,
            "Aprovar mudança",
            "Validação do destino",
            "A mudança fica aprovada se o destino estiver livre.",
            lambda: self._approve_shift(port_call),
        ) or port_call
        port_call = self._step(
            scenario,
            "Concluir mudança",
            "Atualização do cais atual",
            "O cais atual passa para Cais 11 / Autoeuropa.",
            lambda: self._complete_shift(port_call, at=self._event_time(days=4, hour=13, minute=35)),
        ) or port_call
        shift_id = self._latest(port_call, "shift").get("id")
        port_call = self._step(
            scenario,
            "Registar pilotagem da mudança",
            "Registo executante da mudança",
            "A mudança recebe relatório.",
            lambda: services.store.attach_shift_report(
                port_call["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=4, hour=13),
                maneuver_finished_at=self._event_time(days=4, hour=13, minute=35),
                draft_m="8.0",
                notes="Teste: mudança concluída.",
                maneuver_id=shift_id,
            ),
        ) or port_call
        self._check(
            scenario,
            "Cais atualizado",
            "Localização do navio",
            "A escala guarda o novo cais.",
            port_call.get("berth") == "Cais 11 / Autoeuropa",
            port_call.get("berth", "--"),
        )
        port_call = self._step(
            scenario,
            "Planear e concluir saída",
            "Fecho após mudança",
            "A escala também consegue fechar depois da mudança.",
            lambda: self._complete_departure(
                self._approve_departure(
                    services.store.schedule_departure_plan(
                        port_call["id"],
                        planned_departure_at=self._future(days=5, hour=10),
                        updated_by=self.actor_username,
                        next_port="Leixões",
                        constraints=[],
                        departure_plan_note="Teste: saída depois de mudança.",
                        draft_m="8.0",
                        tug_count="2",
                    )
                ),
                at=self._event_time(days=5, hour=10, minute=40),
            ),
        ) or port_call
        self._check(
            scenario,
            "Estado final",
            "Escala com mudança e saída",
            "Estado departed.",
            port_call.get("status") == "departed",
            port_call.get("status", "--"),
        )
        self._finish_scenario(scenario)

    def _scenario_berth_release(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Bloqueio por cais ocupado",
            "Confirma que a entrada seguinte só aprova após saída validada e só conclui depois da saída concluída.",
        )
        occupant = self._create_port_call(
            scenario,
            index=3,
            name="OCUPANTE SAPEC",
            eta=self._future(days=6, hour=7),
            berth="SAPEC Sólidos",
            last_port="Aveiro",
            next_port="Casablanca",
            vessel_type="Graneis sólidos",
        )
        incoming = self._create_port_call(
            scenario,
            index=4,
            name="ENTRADA SAPEC",
            eta=self._future(days=6, hour=15),
            berth="SAPEC Sólidos",
            last_port="Lisboa",
            next_port="Faro",
            vessel_type="Carga geral",
        )
        if not occupant or not incoming:
            self._finish_scenario(scenario)
            return
        occupant = self._complete_entry(
            self._approve_entry(occupant),
            at=self._event_time(days=6, hour=8),
        )
        self._step(
            scenario,
            "Bloquear aprovação com cais ocupado",
            "Validação de cais na aprovação",
            "A entrada posterior não pode ser aprovada enquanto o ocupante não tem saída/mudança aprovada.",
            lambda: self._approve_entry(incoming),
            expected_error="ocupado",
        )
        occupant = services.store.schedule_departure_plan(
            occupant["id"],
            planned_departure_at=self._future(days=6, hour=12),
            updated_by=self.actor_username,
            next_port="Casablanca",
            constraints=[],
            departure_plan_note="Teste: saída liberta SAPEC.",
            draft_m="8.2",
            tug_count="2",
        )
        occupant = self._approve_departure(occupant)
        incoming = self._step(
            scenario,
            "Aprovar entrada após saída aprovada",
            "Libertação operacional planeada",
            "A entrada posterior pode ser aprovada porque a saída do ocupante já está aprovada e é anterior.",
            lambda: self._approve_entry(incoming),
        ) or incoming
        self._step(
            scenario,
            "Bloquear conclusão física",
            "Cais livre de facto",
            "Mesmo aprovada, a entrada não conclui até a saída anterior estar concluída.",
            lambda: self._complete_entry(incoming, at=self._event_time(days=6, hour=15, minute=40)),
            expected_error="ainda está ocupado",
        )
        occupant = self._complete_departure(occupant, at=self._event_time(days=6, hour=12, minute=40))
        incoming = self._step(
            scenario,
            "Concluir após saída física",
            "Ocupação real do cais",
            "Depois da saída concluída, a entrada posterior conclui.",
            lambda: self._complete_entry(incoming, at=self._event_time(days=6, hour=15, minute=40)),
        ) or incoming
        self._check(
            scenario,
            "Resultado do cais",
            "Navio posterior em SAPEC",
            "O segundo navio fica em porto no cais pretendido.",
            incoming.get("status") == "in_port" and incoming.get("berth") == "SAPEC Sólidos",
            f"{incoming.get('status')} · {incoming.get('berth')}",
        )
        self._finish_scenario(scenario)

    def _scenario_abort_keeps_block(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Aborto mantém bloqueio",
            "Confirma que uma saída aprovada e depois abortada deixa de libertar o cais para o navio seguinte.",
        )
        occupant = self._create_port_call(
            scenario,
            index=5,
            name="ABORTO OCUPANTE",
            eta=self._future(days=7, hour=7),
            berth="Teporset",
            last_port="Huelva",
            next_port="Tarragona",
            vessel_type="Graneis líquidos",
            constraints=["gas"],
        )
        incoming = self._create_port_call(
            scenario,
            index=6,
            name="ABORTO SEGUINTE",
            eta=self._future(days=7, hour=16),
            berth="Teporset",
            last_port="Sines",
            next_port="Lisboa",
            vessel_type="Carga geral",
        )
        if not occupant or not incoming:
            self._finish_scenario(scenario)
            return
        occupant = self._complete_entry(
            self._approve_entry(occupant),
            at=self._event_time(days=7, hour=8),
        )
        occupant = services.store.schedule_departure_plan(
            occupant["id"],
            planned_departure_at=self._future(days=7, hour=12),
            updated_by=self.actor_username,
            next_port="Tarragona",
            constraints=["gas"],
            departure_plan_note="Teste: saída será abortada.",
            draft_m="7.3",
            tug_count="2",
        )
        occupant = self._approve_departure(occupant)
        occupant = services.store.abort_departure_plan(
            occupant["id"],
            updated_by=self.actor_username,
            aborted_reason="Teste: operação abortada.",
        )
        self._step(
            scenario,
            "Bloquear após aborto",
            "Cais continua ocupado",
            "A entrada seguinte continua bloqueada porque a saída foi abortada.",
            lambda: self._approve_entry(incoming),
            expected_error="ocupado",
        )
        self._finish_scenario(scenario)

    def _scenario_capacity_limit(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Limite de 4 manobras por hora",
            "Aprova quatro manobras na mesma hora e valida que a quinta fica bloqueada.",
        )
        berths = [
            "Secil E",
            "Cais Palmeiras",
            "TMS 1 - Cais 3",
            "TMS 1 - Cais 4",
            "TMS 1 - Cais 5",
        ]
        port_calls: list[dict] = []
        for offset, berth in enumerate(berths, start=10):
            record = self._create_port_call(
                scenario,
                index=offset,
                name=f"CAPACIDADE {offset}",
                eta=self._future(days=8, hour=15),
                berth=berth,
                last_port="Lisboa",
                next_port="Setúbal",
                vessel_type="Carga geral",
            )
            if record:
                port_calls.append(record)
        for record in port_calls[:4]:
            self._step(
                scenario,
                f"Aprovar {record.get('vessel_name', '')}",
                "Capacidade horária",
                "As primeiras quatro aprovações na mesma hora passam.",
                lambda record=record: self._approve_entry(record),
            )
        if len(port_calls) >= 5:
            self._step(
                scenario,
                "Bloquear quinta aprovação",
                "Capacidade horária",
                "A quinta manobra aprovada na mesma hora é recusada.",
                lambda: self._approve_entry(port_calls[4]),
                expected_error="4 manobras aprovadas",
            )
        snapshot = services.store.get_port_activity_snapshot(window_days=3650)
        approved_count = sum(
            1
            for row in snapshot.get("planned_maneuvers", []) or []
            if row.get("situation_class") == "approved"
            and row.get("planned_label") == "15:00"
            and str(row.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        )
        self._check(
            scenario,
            "Snapshot de planeamento",
            "Manobras aprovadas visíveis",
            "O planeamento mostra quatro aprovações de teste nessa hora.",
            approved_count == 4,
            f"{approved_count} aprovada(s) às 15:00.",
        )
        self._finish_scenario(scenario)

    def _scenario_anchorages_and_duplicates(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Fundeadouros e duplicados",
            "Confirma que fundeadouros não bloqueiam como cais e que IMO ativo duplicado é recusado.",
        )
        first = self._create_port_call(
            scenario,
            index=20,
            name="FUNDEADOURO A",
            eta=self._future(days=9, hour=9),
            berth="Fundeadouro Norte",
            last_port="Leixões",
            next_port="Sines",
            vessel_type="Carga geral",
        )
        second = self._create_port_call(
            scenario,
            index=21,
            name="FUNDEADOURO B",
            eta=self._future(days=9, hour=10),
            berth="Fundeadouro Norte",
            last_port="Vigo",
            next_port="Lisboa",
            vessel_type="Carga geral",
        )
        if not first or not second:
            self._finish_scenario(scenario)
            return
        first = self._complete_entry(self._approve_entry(first), at=self._event_time(days=9, hour=9, minute=30))
        self._step(
            scenario,
            "Aprovar segunda entrada em fundeadouro",
            "Fundeadouro não conta como cais ocupado",
            "Outra entrada para Fundeadouro Norte pode ser aprovada.",
            lambda: self._approve_entry(second),
        )
        duplicate_profile = self._profile(22, vessel_type="Carga geral")
        duplicate_profile["vessel_imo"] = first.get("vessel_imo", "")
        self._step(
            scenario,
            "Recusar IMO duplicado",
            "Validação de escala ativa",
            "Uma escala ativa com o mesmo IMO é recusada.",
            lambda: services.store.create_port_call(
                vessel_name=f"{TEST_VESSEL_PREFIX} IMO DUPLICADO {self.run_token}",
                eta=self._future(days=9, hour=11),
                created_by=self.actor_username,
                constraints=[],
                berth="ALSTOM",
                last_port="Lisboa",
                next_port="Faro",
                notes="Teste: duplicado deve falhar.",
                **duplicate_profile,
            ),
            expected_error="Já existe uma escala ativa com o IMO",
        )
        self._finish_scenario(scenario)

    def _scenario_slash_commands(self) -> None:
        scenario = self._new_scenario(
            "Comandos /",
            "Consultas e comandos slash",
            "Valida parsing, ajuda atualizada, planeamento completo e comandos filtrados por estado.",
        )
        parsed = self._step(
            scenario,
            "Interpretar /planeamento",
            "Parser de comandos",
            "O comando é reconhecido como consulta de planeamento.",
            lambda: parse_slash_command("/planeamento", "piloto"),
        )
        self._check(
            scenario,
            "Resultado do parser",
            "Alias /planeamento",
            "Intent query e command planning.",
            isinstance(parsed, dict) and parsed.get("intent") == "query" and parsed.get("command") == "planning",
            f"{(parsed or {}).get('intent')} · {(parsed or {}).get('command')}",
        )

        help_text = self._step(
            scenario,
            "Gerar /help admin",
            "Ajuda de comandos",
            "A ajuda inclui os comandos operacionais atuais.",
            lambda: build_slash_help("admin"),
        )
        self._check(
            scenario,
            "Comandos listados",
            "/help",
            "Inclui planeamento, edição/cancelamento e relatórios.",
            isinstance(help_text, str)
            and "/planeamento" in help_text
            and "/editar-manobra" in help_text
            and "/cancelar-manobra" in help_text
            and "/registar-manobra" in help_text,
            "Encontrados: "
            + ", ".join(
                command
                for command in ("/planeamento", "/editar-manobra", "/cancelar-manobra", "/registar-manobra")
                if isinstance(help_text, str) and command in help_text
            ),
        )

        planning_payload = self._step(
            scenario,
            "Executar /planeamento",
            "Resposta de planeamento",
            "Lista todas as manobras no planeamento com estado, rota, ID e agência.",
            lambda: answer_slash_query("planning", "", "piloto"),
        )
        planning_answer = (planning_payload or {}).get("answer", "") if isinstance(planning_payload, dict) else ""
        self._check(
            scenario,
            "Conteúdo /planeamento",
            "Formato da resposta",
            "Contém estado, manobra e agente/agência.",
            "Planeamento" in planning_answer
            and "Estado:" in planning_answer
            and "Manobra:" in planning_answer
            and "Agente/agência:" in planning_answer,
            planning_answer[:240] or "--",
        )

        approved_payload = self._step(
            scenario,
            "Executar /manobras-planeadas",
            "Filtro de aprovadas",
            "Mostra só manobras aprovadas/planeadas.",
            lambda: answer_slash_query("planning_approved", "", "piloto"),
        )
        approved_answer = (approved_payload or {}).get("answer", "") if isinstance(approved_payload, dict) else ""
        self._check(
            scenario,
            "Filtro aprovadas",
            "Aliases de planeamento",
            "A resposta usa cabeçalho de aprovadas.",
            "Manobras planeadas/aprovadas" in approved_answer or "Não há manobras planeadas/aprovadas" in approved_answer,
            approved_answer[:180] or "--",
        )

        pending_payload = self._step(
            scenario,
            "Executar /manobras-previstas",
            "Filtro de pendentes",
            "Mostra só manobras pendentes/previstas.",
            lambda: answer_slash_query("planning_pending", "", "piloto"),
        )
        pending_answer = (pending_payload or {}).get("answer", "") if isinstance(pending_payload, dict) else ""
        self._check(
            scenario,
            "Filtro pendentes",
            "Aliases de planeamento",
            "A resposta usa cabeçalho de pendentes.",
            "Manobras previstas/pendentes" in pending_answer or "Não há manobras previstas/pendentes" in pending_answer,
            pending_answer[:180] or "--",
        )

        snapshot = services.store.get_port_activity_snapshot(window_days=3650)
        pending_row = next(
            (
                row
                for row in snapshot.get("planned_maneuvers", []) or []
                if row.get("situation_class") == "pending"
                and str(row.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
                and row.get("maneuver_id")
            ),
            None,
        )
        if pending_row:
            command = (
                "/editar-manobra\n"
                f"ID da manobra: {str(pending_row.get('maneuver_id'))[:8]}\n"
                f"Tipo de manobra: {str(pending_row.get('maneuver_type') or 'entrada')}\n"
                "Hora prevista: 12/05/2026, 19:00\n"
                "Motivo da alteração: Teste operacional\n"
            )
            parsed_edit = self._step(
                scenario,
                "Interpretar /editar-manobra",
                "Comando de alteração por ID curto",
                "O comando gera proposta de edição sem exigir ref da escala quando há ID da manobra.",
                lambda: parse_slash_command(command, "admin"),
            )
            proposal = (parsed_edit or {}).get("proposal") if isinstance(parsed_edit, dict) else {}
            self._check(
                scenario,
                "Campos reconhecidos",
                "/editar-manobra",
                "Tem action edit_maneuver_plan, ID da manobra e motivo.",
                isinstance(proposal, dict)
                and proposal.get("action") == "edit_maneuver_plan"
                and (proposal.get("target") or {}).get("maneuver_id")
                and (proposal.get("fields") or {}).get("change_reason"),
                str(proposal)[:260] if proposal else "--",
            )
        else:
            self._record_step(
                scenario,
                label="Interpretar /editar-manobra",
                element="Comando de alteração por ID curto",
                expected="Existir pelo menos uma manobra pendente de teste para validar o parser.",
                observed="Não foi encontrada manobra pendente de teste no snapshot.",
                state="warning",
            )

        unknown = self._step(
            scenario,
            "Comando desconhecido",
            "Tratamento de erro",
            "Um comando inválido devolve ajuda, sem exceção.",
            lambda: parse_slash_command("/comando-que-nao-existe", "admin"),
        )
        self._check(
            scenario,
            "Erro controlado",
            "Slash inválido",
            "Resposta contém comando não reconhecido.",
            isinstance(unknown, dict) and "Comando não reconhecido" in unknown.get("answer", ""),
            (unknown or {}).get("answer", "")[:180] if isinstance(unknown, dict) else "--",
        )
        self._finish_scenario(scenario)

    def _scenario_live_feed_snapshot(self) -> None:
        scenario = self._new_scenario(
            "Live feed/snapshot",
            "Dados públicos operacionais",
            "Valida se o snapshot alimenta chegadas previstas, navios em porto, planeamento e saídas recentes.",
        )
        snapshot = self._step(
            scenario,
            "Carregar snapshot",
            "Fonte operacional",
            "O snapshot devolve todas as coleções esperadas.",
            lambda: services.store.get_port_activity_snapshot(window_days=3650),
        )
        if not isinstance(snapshot, dict):
            self._finish_scenario(scenario)
            return
        for key in ("arrivals", "in_port", "departed", "planned_maneuvers", "stats"):
            self._check(
                scenario,
                f"Chave {key}",
                "Estrutura do live feed",
                f"A coleção {key} existe no snapshot.",
                key in snapshot,
                f"{key}: {type(snapshot.get(key)).__name__}",
            )

        test_arrivals = [
            item
            for item in snapshot.get("arrivals", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Chegadas previstas com ETA",
            "Previsões de chegada",
            "As chegadas previstas de teste têm ETA e manobra de entrada identificável.",
            bool(test_arrivals)
            and all((item.get("eta") or item.get("eta_label")) for item in test_arrivals)
            and any(
                latest_maneuver_by_type(item, "entry") and latest_maneuver_by_type(item, "entry").get("id")
                for item in test_arrivals
            ),
            f"{len(test_arrivals)} chegada(s) de teste.",
        )

        test_planned = [
            item
            for item in snapshot.get("planned_maneuvers", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Planeamento detalhado",
            "Manobras públicas",
            "Cada linha de planeamento tem tipo, estado, origem/destino, ID da manobra e agente.",
            bool(test_planned)
            and all(
                item.get("maneuver_label")
                and item.get("situation_label")
                and item.get("maneuver_id")
                and item.get("local_origin")
                and item.get("local_destination")
                and item.get("agent_label")
                for item in test_planned
            ),
            f"{len(test_planned)} manobra(s) de teste no planeamento.",
        )

        test_departed = [
            item
            for item in snapshot.get("departed", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Saídas recentes",
            "Arquivo público recente",
            "As escalas fechadas aparecem nas saídas recentes.",
            bool(test_departed),
            f"{len(test_departed)} saída(s) de teste.",
        )

        test_in_port = [
            item
            for item in snapshot.get("in_port", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Navios em porto",
            "Ocupação e localização",
            "O snapshot mostra navios de teste em porto com cais/localização.",
            bool(test_in_port) and all(item.get("berth_label") or item.get("berth") for item in test_in_port),
            f"{len(test_in_port)} navio(s) de teste em porto.",
        )

        planning_answer = self._step(
            scenario,
            "Pergunta natural sobre planeamento",
            "Resposta direta operacional",
            "A pergunta 'Que navios estão no planeamento?' usa o snapshot e lista manobras.",
            lambda: answer_direct_operational_query("Que navios estão no planeamento?"),
        )
        planning_text = (planning_answer or {}).get("answer", "") if isinstance(planning_answer, dict) else ""
        self._check(
            scenario,
            "Resposta de planeamento",
            "Live feed no chat",
            "A resposta contém manobras, navio e ID curto.",
            "Manobras planeadas" in planning_text
            and TEST_VESSEL_PREFIX in planning_text
            and "manobra" in planning_text,
            planning_text[:260] or "--",
        )

        arrivals_answer = self._step(
            scenario,
            "Pergunta natural sobre chegadas",
            "Previsões de chegada no chat",
            "A resposta mostra ETA real, porto de origem, cais destino e entrada.",
            lambda: answer_direct_operational_query("Algum navio nas previsões de chegada?"),
        )
        arrivals_text = (arrivals_answer or {}).get("answer", "") if isinstance(arrivals_answer, dict) else ""
        self._check(
            scenario,
            "Resposta de chegadas",
            "Live feed no chat",
            "A resposta não deve cair em ETA Sem hora para as escalas de teste.",
            TEST_VESSEL_PREFIX in arrivals_text
            and "ETA Sem hora" not in arrivals_text
            and "entrada" in arrivals_text,
            arrivals_text[:260] or "--",
        )

        ships_answer = self._step(
            scenario,
            "Pergunta natural sobre navios em cais",
            "Navios em porto no chat",
            "A resposta devolve contagem e detalhe operacional.",
            lambda: answer_direct_operational_query("Temos quantos navios em cais?"),
        )
        ships_text = (ships_answer or {}).get("answer", "") if isinstance(ships_answer, dict) else ""
        self._check(
            scenario,
            "Resposta de ocupação",
            "Live feed no chat",
            "A resposta menciona navios/cais ou ocupação.",
            any(token in ships_text.casefold() for token in ("navio", "cais", "ocupação", "ocupacao")),
            ships_text[:220] or "--",
        )
        self._finish_scenario(scenario)

    def _build_module_summaries(self) -> list[dict]:
        modules: dict[str, dict] = {}
        for scenario in self.scenarios:
            module_name = scenario.get("module") or "Geral"
            module = modules.setdefault(
                module_name,
                {
                    "name": module_name,
                    "scenarios": [],
                    "scenario_count": 0,
                    "total_steps": 0,
                    "passed_steps": 0,
                    "failed_steps": 0,
                    "warning_steps": 0,
                    "state": "passed",
                    "state_badge": "online",
                },
            )
            module["scenarios"].append(scenario)
            module["scenario_count"] += 1
            for step in scenario.get("steps", []):
                module["total_steps"] += 1
                if step.get("state") == "failed":
                    module["failed_steps"] += 1
                elif step.get("state") == "warning":
                    module["warning_steps"] += 1
                else:
                    module["passed_steps"] += 1

        ordered = list(modules.values())
        for module in ordered:
            module["state"] = (
                "failed"
                if module["failed_steps"]
                else "warning"
                if module["warning_steps"]
                else "passed"
            )
            module["state_badge"] = _status_badge(module["state"])
        return ordered

    def _append_cleanup_result(self, title: str, result: dict | None) -> None:
        if not result:
            return
        scenario = self._new_scenario(
            "Limpeza",
            title,
            "Regista a limpeza automática dos dados TESTE QA para manter a base controlada.",
        )
        errors = result.get("errors") or []
        self._record_step(
            scenario,
            label=title,
            element="Dados temporários TESTE QA",
            expected="Limpeza sem erros.",
            observed=(
                "; ".join(errors)
                if errors
                else f"{result.get('deleted_count', 0)} escala(s) removida(s)."
            ),
            state="failed" if errors or result.get("state") == "failed" else "passed",
        )
        self._finish_scenario(scenario)

    def run(self) -> dict:
        pre_cleanup = cleanup_operational_test_records()
        self._append_cleanup_result("Limpeza inicial", pre_cleanup)
        cleanup_result: dict | None = None
        try:
            self._scenario_natural_flow()
            self._scenario_shift_flow()
            self._scenario_berth_release()
            self._scenario_abort_keeps_block()
            self._scenario_capacity_limit()
            self._scenario_anchorages_and_duplicates()
            self._scenario_slash_commands()
            self._scenario_live_feed_snapshot()
        finally:
            if self.cleanup_after:
                cleanup_result = cleanup_operational_test_records()
                self._append_cleanup_result("Limpeza final", cleanup_result)

        modules = self._build_module_summaries()
        total_steps = sum(len(scenario["steps"]) for scenario in self.scenarios)
        failed_steps = sum(
            1
            for scenario in self.scenarios
            for step in scenario["steps"]
            if step.get("state") == "failed"
        )
        warning_steps = sum(
            1
            for scenario in self.scenarios
            for step in scenario["steps"]
            if step.get("state") == "warning"
        )
        passed_steps = total_steps - failed_steps - warning_steps
        state = "failed" if failed_steps else "warning" if warning_steps else "passed"
        finished_at = datetime.now().astimezone()
        return {
            "kind": "run",
            "state": state,
            "state_label": "Falhou" if state == "failed" else "Com avisos" if state == "warning" else "Tudo passou",
            "state_badge": _status_badge(state),
            "started_at_label": self.started_at.strftime("%d/%m/%Y %H:%M:%S"),
            "finished_at_label": finished_at.strftime("%d/%m/%Y %H:%M:%S"),
            "duration_ms": int((time.perf_counter() - self.started_perf) * 1000),
            "scenario_count": len(self.scenarios),
            "total_steps": total_steps,
            "passed_steps": passed_steps,
            "failed_steps": failed_steps,
            "warning_steps": warning_steps,
            "pre_cleanup": pre_cleanup,
            "cleanup": cleanup_result,
            "cleanup_after": self.cleanup_after,
            "retained_records": [] if self.cleanup_after else self.retained_records,
            "modules": modules,
            "scenarios": self.scenarios,
        }


def run_operational_flow_suite(*, actor_username: str, cleanup_after: bool = True) -> dict:
    return OperationalFlowSuite(actor_username=actor_username, cleanup_after=cleanup_after).run()
