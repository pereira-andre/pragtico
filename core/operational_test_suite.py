"""Admin-only operational flow tests for port calls and maneuvers."""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable

from blueprints.port_calls import (
    VESSEL_CATALOG_STATE_KEY,
    _build_vessel_catalog_options,
    _coerce_port_call_payload_with_catalog,
    _filter_vessel_catalog_options,
    _remove_vessel_catalog_record,
    _sync_vessel_catalog_record_to_active_port_calls,
    _upsert_vessel_catalog_record,
    _validate_vessel_catalog_record,
    _vessel_catalog_txt,
)
from core import services
from core.form_helpers import (
    ensure_maneuver_hour_capacity_for_approval,
    ensure_portal_berth_is_available,
    ensure_portal_berth_is_physically_available,
)
from core.maneuver_context import answer_slash_validation
from core.operational_actions import answer_slash_query, finalize_operational_proposal
from core.operational_sources import answer_direct_operational_query, build_operational_chat_sources
from core.portal_notifications import latest_maneuver_by_type
from domain.chat_action_config import SLASH_COMMAND_ALIASES
from domain.chat_action_templates import build_slash_help
from domain.chat_actions import parse_slash_command


TEST_VESSEL_PREFIX = "TESTE QA"
QUERY_SLASH_COMMANDS = {
    "local_warnings",
    "wave",
    "tides",
    "weather",
    "planning",
    "planning_approved",
    "planning_pending",
    "consult_scale",
    "consult_maneuver",
    "consult_scale_cost",
    "consult_maneuver_cost",
    "consult_vessel",
    "rule",
}


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

    def _check_state(
        self,
        scenario: dict,
        label: str,
        element: str,
        expected: str,
        observed: str,
        *,
        state: str,
    ) -> None:
        self._record_step(
            scenario,
            label=label,
            element=element,
            expected=expected,
            observed=observed,
            state=state,
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

    def _runtime_state_snapshot(self, key: str) -> dict | None:
        if not hasattr(services.store, "get_runtime_state"):
            return None
        value = services.store.get_runtime_state(key)
        return deepcopy(value) if isinstance(value, dict) else None

    def _restore_runtime_state(self, key: str, snapshot: dict | None) -> None:
        if not hasattr(services.store, "set_runtime_state"):
            return
        if snapshot is None and hasattr(services.store, "delete_runtime_state"):
            services.store.delete_runtime_state(key)
            return
        services.store.set_runtime_state(key, deepcopy(snapshot or {}))

    def _catalog_payload(
        self,
        index: int,
        *,
        name: str,
        vessel_type: str = "Carga geral",
        call_sign: str = "",
    ) -> dict:
        payload = {
            "vessel_name": f"{TEST_VESSEL_PREFIX} {name} {self.run_token}",
            **self._profile(index, vessel_type=vessel_type),
        }
        if call_sign:
            payload["vessel_call_sign"] = call_sign
        return payload

    def _created_records(self) -> list[dict]:
        records: list[dict] = []
        for port_call_id in self.created_ids:
            try:
                records.append(services.store.get_port_call(port_call_id))
            except Exception:
                continue
        return records

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
        departed_ids = {
            row.get("id") or row.get("port_call_id")
            for row in snapshot.get("departed", []) or []
        }
        archived_departure_ids = {
            row.get("port_call_id")
            for row in snapshot.get("archived_maneuvers", []) or []
            if row.get("maneuver_type") == "departure"
        }
        self._check(
            scenario,
            "Live feed operacional",
            "Saídas recentes/arquivo",
            "A saída concluída aparece nas saídas recentes e no arquivo de manobras.",
            final["id"] in departed_ids and final["id"] in archived_departure_ids,
            (
                f"{len(snapshot.get('departed', []))} saída(s); "
                f"{len(snapshot.get('archived_maneuvers', []))} manobra(s) arquivada(s)."
            ),
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

    def _scenario_vessel_catalog_management(self) -> None:
        scenario = self._new_scenario(
            "Catálogo de navios",
            "Gestão controlada de navios frequentes",
            "Valida criação/importação lógica, preenchimento por catálogo, sincronização com escalas ativas, filtros, exportação TXT e remoção sem deixar alterações definitivas.",
        )
        original_state = self._runtime_state_snapshot(VESSEL_CATALOG_STATE_KEY)
        catalog_only: dict = {}
        sync_record: dict = {}
        active_port_call: dict | None = None
        try:
            catalog_only_payload = self._catalog_payload(
                30,
                name="CATALOGO PURO",
                vessel_type="roro",
                call_sign=f"TQACAT{self.run_token[-3:]}",
            )
            catalog_only_payload.update(
                {
                    "service_rate_profile": "Linha regular",
                    "regular_line_calls_365d": "12",
                    "tup_reduction_profile": "regular_line",
                    "service_notes": "Teste controlado de ficha sem escala ativa.",
                }
            )
            catalog_only = self._step(
                scenario,
                "Criar ficha de catálogo",
                "Importação/upsert de navio",
                "A ficha é validada, normaliza aliases de tipo e fica guardada no catálogo.",
                lambda: _upsert_vessel_catalog_record(catalog_only_payload, updated_by=self.actor_username),
            ) or {}
            self._check(
                scenario,
                "Tipo canónico",
                "Alias Ro-Ro",
                "O alias roro passa para Roll-on/Roll-off.",
                isinstance(catalog_only, dict) and catalog_only.get("vessel_type") == "Roll-on/Roll-off",
                catalog_only.get("vessel_type", "--") if isinstance(catalog_only, dict) else "--",
            )

            filled_payload = self._step(
                scenario,
                "Preencher escala por catálogo",
                "Importação de escala com IMO existente",
                "Uma escala importada pode indicar só o IMO e herdar a ficha técnica do catálogo.",
                lambda: _coerce_port_call_payload_with_catalog(
                    {
                        "vessel_imo": catalog_only_payload["vessel_imo"],
                        "eta": self._future(days=13, hour=9),
                        "berth": "Cais 10 / Autoeuropa",
                        "last_port": "Southampton",
                        "next_port": "Vigo",
                        "draft_m": "7.8",
                        "tug_count": 2,
                        "constraints": [],
                        "notes": "Teste: escala preenchida a partir do catálogo.",
                    }
                ),
            ) or {}
            self._check(
                scenario,
                "Ficha herdada",
                "Dados do navio na escala importada",
                "Nome, tipo e dimensões vêm da ficha guardada.",
                isinstance(filled_payload, dict)
                and filled_payload.get("vessel_name") == catalog_only_payload["vessel_name"]
                and filled_payload.get("vessel_type") == "Roll-on/Roll-off"
                and filled_payload.get("vessel_loa_m") == catalog_only_payload["vessel_loa_m"],
                (
                    f"{filled_payload.get('vessel_name', '--')} · {filled_payload.get('vessel_type', '--')} · "
                    f"LOA {filled_payload.get('vessel_loa_m', '--')}"
                    if isinstance(filled_payload, dict)
                    else "--"
                ),
            )

            catalog_answer = self._step(
                scenario,
                "Consultar ficha por bot",
                "Ficha de catálogo sem escala ativa",
                "A consulta por nome/call sign encontra o navio guardado.",
                lambda: answer_direct_operational_query(
                    f"Dados navio {catalog_only_payload['vessel_name']} call sign {catalog_only_payload['vessel_call_sign']}"
                ),
            )
            catalog_text = (catalog_answer or {}).get("answer", "") if isinstance(catalog_answer, dict) else ""
            self._check(
                scenario,
                "Resposta ficha catálogo",
                "Dados técnicos do navio",
                "A resposta mostra ficha, IMO, indicativo e dados técnicos.",
                catalog_only_payload["vessel_name"] in catalog_text
                and catalog_only_payload["vessel_call_sign"] in catalog_text
                and "Ficha de catálogo" in catalog_text
                and "GT" in catalog_text,
                catalog_text[:260] or "--",
            )

            active_port_call = self._create_port_call(
                scenario,
                index=31,
                name="CATALOGO SINCRONIZAR",
                eta=self._future(days=13, hour=11),
                berth="Cais 11 / Autoeuropa",
                last_port="Leixões",
                next_port="Sines",
                vessel_type="Carga geral",
            )
            if active_port_call:
                sync_payload = {
                    **_validate_vessel_catalog_record(
                        {
                            "vessel_name": active_port_call.get("vessel_name", ""),
                            "vessel_imo": active_port_call.get("vessel_imo", ""),
                            "vessel_call_sign": active_port_call.get("vessel_call_sign", ""),
                            "vessel_flag": active_port_call.get("vessel_flag", ""),
                            "vessel_type": "roro",
                            "vessel_loa_m": "199.9",
                            "vessel_beam_m": "32.2",
                            "vessel_gt_t": "52000",
                            "vessel_dwt_t": "18000",
                            "vessel_max_draft_m": "9.8",
                            "vessel_bow_thruster": "yes",
                            "vessel_stern_thruster": "unknown",
                        }
                    ),
                    "regular_line_calls_365d": "37",
                    "service_notes": "Teste: sincronização de ficha ativa.",
                }
                sync_record = self._step(
                    scenario,
                    "Atualizar ficha com escala ativa",
                    "Edição do catálogo",
                    "A ficha editada fica guardada e pronta para sincronizar a escala ativa.",
                    lambda: _upsert_vessel_catalog_record(sync_payload, updated_by=self.actor_username, validate=False),
                ) or {}
                synced_count = self._step(
                    scenario,
                    "Sincronizar escala ativa",
                    "Catálogo -> escala",
                    "A alteração do catálogo atualiza a ficha do navio na escala ativa correspondente.",
                    lambda: _sync_vessel_catalog_record_to_active_port_calls(
                        sync_record,
                        updated_by=self.actor_username,
                    ),
                )
                refreshed = services.store.get_port_call(active_port_call["id"])
                self._check(
                    scenario,
                    "Escala sincronizada",
                    "Tipo e dimensões na escala ativa",
                    "A escala ativa passa a refletir o tipo e LOA editados no catálogo.",
                    bool(synced_count)
                    and refreshed.get("vessel_type") == "Roll-on/Roll-off"
                    and refreshed.get("vessel_loa_m") == "199.9",
                    f"{synced_count or 0} sincronizada(s) · {refreshed.get('vessel_type', '--')} · LOA {refreshed.get('vessel_loa_m', '--')}",
                )

            activity = services.store.get_port_activity_snapshot(window_days=3650)
            vessels = _build_vessel_catalog_options(activity)
            filtered = _filter_vessel_catalog_options(
                vessels,
                q=catalog_only_payload["vessel_call_sign"],
                vessel_type="Roll-on/Roll-off",
            )
            self._check(
                scenario,
                "Filtros de catálogo",
                "Busca e tipo de navio",
                "A exportação/consulta filtrada devolve apenas navios compatíveis com os filtros.",
                bool(filtered)
                and all(catalog_only_payload["vessel_call_sign"] in (item.get("vessel_call_sign") or "") for item in filtered),
                f"{len(filtered)} resultado(s) para {catalog_only_payload['vessel_call_sign']}.",
            )

            txt_body = self._step(
                scenario,
                "Exportar ficha TXT",
                "Ficha imprimível/exportável",
                "A ficha de navio gera texto com identificação e dados técnicos.",
                lambda: _vessel_catalog_txt(catalog_only),
            )
            self._check(
                scenario,
                "Conteúdo TXT",
                "Exportação individual",
                "O texto exportado inclui nome, IMO e tipo.",
                isinstance(txt_body, str)
                and catalog_only_payload["vessel_name"] in txt_body
                and catalog_only_payload["vessel_imo"] in txt_body
                and "Roll-on/Roll-off" in txt_body,
                (txt_body or "")[:220] if isinstance(txt_body, str) else "--",
            )

            removed = self._step(
                scenario,
                "Remover navio individual",
                "Apagar ficha frequente",
                "A remoção individual oculta a ficha sem apagar escalas.",
                lambda: _remove_vessel_catalog_record(catalog_only.get("key", ""), hide=True),
            ) or {}
            after_remove = _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
            self._check(
                scenario,
                "Ficha removida",
                "Catálogo visível",
                "O navio removido deixa de aparecer no catálogo.",
                removed.get("key") == catalog_only.get("key")
                and not any(item.get("key") == catalog_only.get("key") for item in after_remove),
                removed.get("vessel_name") or removed.get("key", "--"),
            )

            _upsert_vessel_catalog_record(catalog_only_payload, updated_by=self.actor_username)
            test_vessels_before_clear = [
                item
                for item in _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
                if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
            ]
            removed_keys = self._step(
                scenario,
                "Remover todos os navios TESTE QA",
                "Limpeza global controlada",
                "O teste exercita a remoção em lote apenas sobre fichas TESTE QA, sem ocultar navios reais do catálogo.",
                lambda: [
                    _remove_vessel_catalog_record(item.get("key", ""), hide=True).get("key")
                    for item in test_vessels_before_clear
                    if item.get("key")
                ],
            ) or []
            test_vessels_after_clear = [
                item
                for item in _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
                if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
            ]
            self._check(
                scenario,
                "Lote TESTE QA removido",
                "Remoção em lote sem impacto real",
                "As fichas de teste são ocultadas e navios reais não são tocados.",
                bool(removed_keys) and not test_vessels_after_clear,
                f"{len(removed_keys)} chave(s) TESTE QA removida(s).",
            )
        finally:
            try:
                self._restore_runtime_state(VESSEL_CATALOG_STATE_KEY, original_state)
                self._record_step(
                    scenario,
                    label="Repor catálogo",
                    element="Estado original do catálogo",
                    expected="O teste não deixa alterações definitivas no catálogo.",
                    observed="Snapshot inicial reposto.",
                    state="passed",
                )
            except Exception as exc:
                self._record_step(
                    scenario,
                    label="Repor catálogo",
                    element="Estado original do catálogo",
                    expected="O teste não deixa alterações definitivas no catálogo.",
                    observed=str(exc),
                    state="failed",
                )
        self._finish_scenario(scenario)

    def _slash_datetime_label(self, *, days: int, hour: int, minute: int = 0) -> str:
        return datetime.fromisoformat(self._future(days=days, hour=hour, minute=minute)).strftime("%d/%m/%Y, %H:%M")

    def _slash_context(self) -> dict:
        records = self._created_records()
        if not records:
            return {
                "ref": "PTSET26TEST0001",
                "vessel_name": f"{TEST_VESSEL_PREFIX} SLASH",
                "maneuver_id": "00000000",
                "maneuver_id_short": "00000000",
                "maneuver_type": "entry",
                "maneuver_type_label": "entrada",
                "imo": "9990001",
            }

        target = next(
            (
                record
                for record in records
                if record.get("status") in {"scheduled", "in_port"}
                and record.get("maneuver_history")
            ),
            records[0],
        )
        maneuvers = list(target.get("maneuver_history") or [])
        maneuver = next((item for item in maneuvers if item.get("state") == "pending"), None)
        maneuver = maneuver or next((item for item in maneuvers if item.get("id")), {})
        maneuver_type = (maneuver.get("type") or "entry").strip().lower()
        type_labels = {"entry": "entrada", "departure": "saída", "shift": "mudança"}
        return {
            "ref": target.get("reference_code") or target.get("id") or "PTSET26TEST0001",
            "vessel_name": target.get("vessel_name") or f"{TEST_VESSEL_PREFIX} SLASH",
            "maneuver_id": maneuver.get("id") or "00000000",
            "maneuver_id_short": str(maneuver.get("id") or "00000000")[:8],
            "maneuver_type": maneuver_type,
            "maneuver_type_label": type_labels.get(maneuver_type, "entrada"),
            "imo": target.get("vessel_imo") or "9990001",
        }

    def _slash_expected_intents(self, command: str) -> set[str]:
        if command == "help":
            return {"help"}
        if command in QUERY_SLASH_COMMANDS:
            return {"query"}
        if command == "validate_maneuver":
            return {"validate", "template"}
        if command == "event_report":
            return {"event_report"}
        return {"action", "template"}

    def _slash_sample_for_alias(self, alias: str, command: str, ctx: dict, index: int) -> str:
        head = f"/{alias}"
        if command == "help":
            return head
        if command == "local_warnings":
            return head
        if command == "wave":
            return head
        if command == "tides":
            return f"{head} hoje"
        if command == "weather":
            return f"{head} hoje"
        if command in {"planning", "planning_approved", "planning_pending"}:
            return head
        if command == "rule":
            return head
        if command in {"consult_scale", "consult_scale_cost"}:
            return f"{head} {ctx['ref']}"
        if command in {"consult_maneuver", "consult_maneuver_cost"}:
            return f"{head} {ctx['maneuver_id_short']}"
        if command == "consult_vessel":
            return f"{head} {ctx['vessel_name']}"
        if command == "validate_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "event_report":
            return f"{head} SEGURANCA. Cais 10. Teste operacional sem impacto."
        if command == "register_scale":
            return (
                f"{head}\n"
                f"Nome: {TEST_VESSEL_PREFIX} SLASH REGISTO {self.run_token} {index}\n"
                f"ETA de chegada: {self._slash_datetime_label(days=12, hour=10)}\n"
                "Cais previsto: ALSTOM\n"
                "Último porto: Lisboa\n"
                "Próximo destino: Faro\n"
                f"IMO: {self._imo(70 + index)}\n"
                f"Indicativo: TQASL{index:02d}\n"
                "Bandeira: Portugal\n"
                "Tipo de navio: Carga geral\n"
                "LOA: 118.0\n"
                "Boca: 19.2\n"
                "GT: 7200\n"
                "DWT: 9400\n"
                "Calado máximo: 7.2\n"
                "Bow thruster: yes\n"
                "Stern thruster: unknown\n"
                "Calado operacional: 6.4\n"
                "Rebocadores: 1\n"
                "Observações: teste de parser slash\n"
            )
        if command == "edit_scale":
            return (
                f"{head}\n"
                f"Ref: {ctx['ref']}\n"
                f"ETA: {self._slash_datetime_label(days=12, hour=11)}\n"
                "Motivo da alteração: Teste de comando slash\n"
            )
        if command == "delete_scale":
            return f"{head}\nRef: {ctx['ref']}\n"
        if command == "create_maneuver":
            return (
                f"{head}\n"
                f"Ref: {ctx['ref']}\n"
                "Tipo de manobra: saída\n"
                f"Hora prevista: {self._slash_datetime_label(days=12, hour=18)}\n"
                "Destino: Sines\n"
                "Calado: 6.5\n"
                "Rebocadores: 2\n"
                "Restrições: daylight\n"
                "Observações: teste de criação de manobra\n"
            )
        if command == "edit_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                f"Hora prevista: {self._slash_datetime_label(days=12, hour=19)}\n"
                "Motivo da alteração: Teste de edição slash\n"
            )
        if command == "delete_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "approve_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "create_report":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                f"Início da manobra: {self._slash_datetime_label(days=12, hour=9)}\n"
                f"Fim da manobra: {self._slash_datetime_label(days=12, hour=10)}\n"
                "Calado: 6.4\n"
                "Observações: teste de registo slash\n"
            )
        if command == "edit_report":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                f"Início da manobra: {self._slash_datetime_label(days=12, hour=9)}\n"
                f"Fim da manobra: {self._slash_datetime_label(days=12, hour=10)}\n"
                "Calado: 6.4\n"
                "Motivo da alteração: Teste de revisão de registo\n"
            )
        if command == "delete_report":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "abort_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                "Motivo: Teste de aborto slash\n"
            )
        return head

    def _slash_query_argument(self, parsed: dict) -> str:
        return " ".join(str((parsed or {}).get("argument") or "").split())

    def _scenario_slash_commands(self) -> None:
        scenario = self._new_scenario(
            "Comandos /",
            "Cobertura completa dos comandos slash",
            "Valida todos os aliases definidos no sistema, executa consultas seguras e captura erros por comando.",
        )
        ctx = self._slash_context()
        tested_aliases: list[str] = []
        for index, (alias, command) in enumerate(sorted(SLASH_COMMAND_ALIASES.items()), start=1):
            sample = self._slash_sample_for_alias(alias, command, ctx, index)
            parsed = self._step(
                scenario,
                f"Parser /{alias}",
                f"Alias de {command}",
                "O comando é reconhecido e encaminhado para a intenção correta.",
                lambda sample=sample: parse_slash_command(sample, "admin"),
            )
            tested_aliases.append(alias)
            expected_intents = self._slash_expected_intents(command)
            observed_intent = (parsed or {}).get("intent") if isinstance(parsed, dict) else ""
            observed_target = (
                (parsed or {}).get("command")
                or (parsed or {}).get("proposal", {}).get("action")
                or observed_intent
                if isinstance(parsed, dict)
                else ""
            )
            self._check(
                scenario,
                f"Cobertura /{alias}",
                "Resultado do parser",
                f"Intenção esperada: {', '.join(sorted(expected_intents))}.",
                isinstance(parsed, dict) and observed_intent in expected_intents,
                f"intent={observed_intent or '--'} · destino={observed_target or '--'}",
            )

            proposal = (parsed or {}).get("proposal") if isinstance(parsed, dict) else None
            if isinstance(proposal, dict) and proposal.get("action"):
                finalized = self._step(
                    scenario,
                    f"Resolver alvo /{alias}",
                    "Ref/ID/campos do comando",
                    "O comando resolve escala/manobra por Ref ou ID sem obrigar a repetir todos os campos.",
                    lambda proposal=proposal: finalize_operational_proposal(deepcopy(proposal)),
                )
                self._check(
                    scenario,
                    f"Diagnóstico /{alias}",
                    "Campos reconhecidos e em falta",
                    "O diagnóstico fica explícito e sem erro inesperado.",
                    isinstance(finalized, dict)
                    and finalized.get("intent") in {"action", "template", "unsupported"}
                    and "#ERR-9000" not in str(finalized),
                    (
                        f"intent={(finalized or {}).get('intent', '--') if isinstance(finalized, dict) else '--'} · "
                        f"ação={(finalized or {}).get('action', '--') if isinstance(finalized, dict) else '--'} · "
                        f"em falta={', '.join((finalized or {}).get('missing_fields') or []) if isinstance(finalized, dict) else '--'}"
                    ),
                )

            if isinstance(parsed, dict) and parsed.get("intent") == "query":
                payload = self._step(
                    scenario,
                    f"Executar /{alias}",
                    "Consulta slash",
                    "A consulta devolve uma resposta controlada, mesmo que a fonte externa esteja indisponível.",
                    lambda parsed=parsed: answer_slash_query(
                        parsed.get("command", ""),
                        self._slash_query_argument(parsed),
                        "admin",
                    ),
                )
                answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
                self._check(
                    scenario,
                    f"Resposta /{alias}",
                    "Resultado visível",
                    "A resposta contém texto e answer_origin.",
                    isinstance(payload, dict) and bool(answer.strip()) and bool(payload.get("answer_origin")),
                    f"{(payload or {}).get('answer_origin', '--') if isinstance(payload, dict) else '--'} · {answer[:180] or '--'}",
                )

            if isinstance(parsed, dict) and parsed.get("intent") == "validate":
                payload = self._step(
                    scenario,
                    f"Executar /{alias}",
                    "Validação determinística de manobra",
                    "A validação devolve resposta própria e não falha a página.",
                    lambda parsed=parsed: answer_slash_validation(parsed.get("target") or {}, "admin"),
                )
                answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
                self._check(
                    scenario,
                    f"Resposta /{alias}",
                    "Checklist de manobra",
                    "A resposta contém texto e origem slash_validation.",
                    isinstance(payload, dict)
                    and bool(answer.strip())
                    and payload.get("answer_origin") == "slash_validation",
                    answer[:220] or "--",
                )

        self._check(
            scenario,
            "Cobertura total de aliases",
            "Inventário dos comandos /",
            f"Todos os {len(SLASH_COMMAND_ALIASES)} aliases definidos são testados.",
            len(set(tested_aliases)) == len(SLASH_COMMAND_ALIASES),
            f"{len(set(tested_aliases))}/{len(SLASH_COMMAND_ALIASES)} aliases testados.",
        )

        help_text = build_slash_help("admin")
        missing_help = [
            alias
            for alias in (
                "help",
                "avisos-locais",
                "ondulacao",
                "mares",
                "meteorologia",
                "planeamento",
                "consultar-escala",
                "consultar-manobra",
                "consultar-navio",
                "validar-manobra",
                "registar-escala",
                "editar-escala",
                "apagar-escala",
                "criar-manobra",
                "editar-manobra",
                "apagar-manobra",
                "aprovar",
                "registar-manobra",
                "editar-registo-manobra",
                "apagar-registo-manobra",
                "abortar",
                "reportar-evento",
            )
            if f"/{alias}" not in help_text
        ]
        self._check(
            scenario,
            "Ajuda cobre comandos principais",
            "/help",
            "A ajuda admin lista os comandos slash principais.",
            not missing_help,
            "Em falta: " + ", ".join(missing_help) if missing_help else "Comandos principais presentes.",
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

    def _scenario_site_queries_and_archive(self) -> None:
        scenario = self._new_scenario(
            "Consultas site/live feed",
            "Ficha, escala, manobra e arquivo",
            "Valida respostas determinísticas e fontes do portal para ficha de navio, detalhes de escala/manobra, saídas recentes e arquivo.",
        )
        port_call = self._create_port_call(
            scenario,
            index=42,
            name="CONSULTAS SITE",
            eta=self._future(days=14, hour=8),
            berth="Tanquisado (lado jusante)",
            last_port="Sines",
            next_port="Lisboa",
            vessel_type="Graneis líquidos",
            constraints=["gas"],
        )
        if not port_call:
            self._finish_scenario(scenario)
            return

        port_call = self._step(
            scenario,
            "Preparar entrada concluída",
            "Dados base para consultas",
            "A escala fica com entrada aprovada, concluída e registada.",
            lambda: services.store.attach_entry_report(
                self._complete_entry(
                    self._approve_entry(port_call),
                    at=self._event_time(days=14, hour=9),
                )["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=14, hour=8, minute=10),
                maneuver_finished_at=self._event_time(days=14, hour=9),
                draft_m="7.1",
                notes="Teste: entrada para consultas do site.",
                maneuver_id=self._latest(port_call, "entry").get("id"),
            ),
        ) or port_call
        port_call = self._step(
            scenario,
            "Preparar saída arquivada",
            "Arquivo de manobras",
            "A escala fica com saída aprovada, concluída e registada para consulta histórica.",
            lambda: services.store.attach_departure_report(
                self._complete_departure(
                    self._approve_departure(
                        services.store.schedule_departure_plan(
                            port_call["id"],
                            planned_departure_at=self._future(days=15, hour=10),
                            updated_by=self.actor_username,
                            next_port="Lisboa",
                            constraints=["gas"],
                            departure_plan_note="Teste: saída para arquivo.",
                            draft_m="7.2",
                            tug_count="2",
                        )
                    ),
                    at=self._event_time(days=15, hour=10, minute=45),
                )["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=15, hour=10),
                maneuver_finished_at=self._event_time(days=15, hour=10, minute=45),
                draft_m="7.0",
                notes="Teste: saída arquivada para consultas.",
                maneuver_id=self._latest(port_call, "departure").get("id"),
            ),
        ) or port_call
        port_call = services.store.get_port_call(port_call["id"])
        departure = self._latest(port_call, "departure")
        departure_id = departure.get("id", "")
        departure_short = departure_id[:8].upper()
        reference = port_call.get("reference_code", "")
        vessel_name = port_call.get("vessel_name", "")

        vessel_answer = self._step(
            scenario,
            "Consultar ficha de navio",
            "Dados técnicos e operacionais",
            "A pergunta natural devolve ficha, escala, localização, manobras e agência.",
            lambda: answer_direct_operational_query(f"Dados navio {vessel_name} call sign {port_call.get('vessel_call_sign', '')}"),
        )
        vessel_text = (vessel_answer or {}).get("answer", "") if isinstance(vessel_answer, dict) else ""
        self._check(
            scenario,
            "Resposta ficha navio",
            "Ficha do navio no bot",
            "A resposta inclui identificação, ficha técnica, estado/localização e manobras.",
            vessel_name in vessel_text
            and reference in vessel_text
            and "GT" in vessel_text
            and "DWT" in vessel_text
            and "Manobras conhecidas" in vessel_text
            and departure_id in vessel_text,
            vessel_text[:300] or "--",
        )

        id_answer = self._step(
            scenario,
            "Consultar IDs",
            "Escala e manobra",
            "A pergunta por ID distingue referência da escala e ID real da manobra.",
            lambda: answer_direct_operational_query(
                f"Qual o id da escala do {vessel_name}? Depois diz também o id da manobra de saída."
            ),
        )
        id_text = (id_answer or {}).get("answer", "") if isinstance(id_answer, dict) else ""
        self._check(
            scenario,
            "Resposta IDs",
            "Tracking escala/manobra",
            "A resposta contém a referência da escala e o ID de manobra, não repete a Ref como se fosse manobra.",
            reference in id_text and departure_id in id_text and "ID da manobra" in id_text,
            id_text[:300] or "--",
        )

        approver_answer = self._step(
            scenario,
            "Consultar aprovação",
            "Quem aprovou e quem executou",
            "A pergunta sobre aprovação mostra validador, executante e ID da manobra.",
            lambda: answer_direct_operational_query(f"Quem aprovou a manobra do {vessel_name} para sair?"),
        )
        approver_text = (approver_answer or {}).get("answer", "") if isinstance(approver_answer, dict) else ""
        self._check(
            scenario,
            "Resposta aprovação",
            "Validador/piloto executante",
            "A resposta identifica aprovação, execução e manobra.",
            "aprovada por" in approver_text.casefold()
            and "execut" in approver_text.casefold()
            and departure_id in approver_text,
            approver_text[:300] or "--",
        )

        agent_answer = self._step(
            scenario,
            "Consultar agente/agência",
            "Agente de navegação",
            "Sempre que fala do agente deve incluir também a agência quando existe perfil.",
            lambda: answer_direct_operational_query(f"Qual era o agente do {vessel_name} na saída?"),
        )
        agent_text = (agent_answer or {}).get("answer", "") if isinstance(agent_answer, dict) else ""
        self._check(
            scenario,
            "Resposta agente",
            "Agente e agência",
            "A resposta menciona agente de navegação e agência/perfil.",
            "Agente de navegação" in agent_text
            and ("(" in agent_text or "agência" in agent_text.casefold() or "APSS" in agent_text),
            agent_text[:260] or "--",
        )

        recent_answer = self._step(
            scenario,
            "Consultar saídas recentes",
            "Partidas/arquivo público",
            "A pergunta natural sobre saídas recentes lista ATD, rota, manobra, agente, aprovação e execução.",
            lambda: answer_direct_operational_query("Alguma saída recente?"),
        )
        recent_text = (recent_answer or {}).get("answer", "") if isinstance(recent_answer, dict) else ""
        self._check(
            scenario,
            "Resposta saídas recentes",
            "Live feed de partidas",
            "A saída de teste aparece com piloto aprovador e executante.",
            vessel_name in recent_text
            and departure_short in recent_text.upper()
            and "aprovada por" in recent_text.casefold()
            and "executada por" in recent_text.casefold(),
            recent_text[:320] or "--",
        )

        scale_sources = self._step(
            scenario,
            "Fonte de escala",
            "Registo de escalas do portal",
            "As fontes do bot incluem a escala quando a pergunta aponta para a Ref.",
            lambda: build_operational_chat_sources(f"Dados da escala {reference} do navio {vessel_name}"),
        ) or []
        scale_text = "\n".join(str(source.get("snippet") or "") for source in scale_sources if isinstance(source, dict))
        self._check(
            scenario,
            "Escala nas fontes",
            "Contexto do site",
            "A fonte de escalas contém Ref, navio, estado, cais e agente.",
            reference in scale_text and vessel_name in scale_text and "Registo de escalas" in scale_text,
            scale_text[:320] or "--",
        )

        archive_sources = self._step(
            scenario,
            "Fonte de arquivo",
            "Arquivo de manobras",
            "As fontes do bot incluem manobras arquivadas com ID, rota e intervenientes.",
            lambda: build_operational_chat_sources(f"Arquivo de manobras do {vessel_name} saída {departure_short}"),
        ) or []
        archive_text = "\n".join(str(source.get("snippet") or "") for source in archive_sources if isinstance(source, dict))
        self._check(
            scenario,
            "Arquivo nas fontes",
            "Histórico operacional",
            "O arquivo contém a manobra concluída, rota, validador, executante e agente.",
            vessel_name in archive_text
            and departure_id in archive_text
            and "validado por" in archive_text.casefold()
            and "executado por" in archive_text.casefold(),
            archive_text[:360] or "--",
        )
        self._finish_scenario(scenario)

    def _scenario_live_environment_queries(self) -> None:
        scenario = self._new_scenario(
            "Live feed/snapshot",
            "Ambiente live e avisos",
            "Exercita perguntas live de luz do dia, lua, meteorologia e avisos locais com erro tratado quando uma fonte externa estiver indisponível.",
        )
        checks = [
            (
                "Luz do dia",
                "Qual o período luminoso para hoje?",
                ("Período luminoso", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Fase da lua",
                "Qual a fase da lua hoje?",
                ("Fase da lua", "Lua", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Meteorologia hoje",
                "Quais as previsões meteorológicas para hoje?",
                ("Previsão meteorológica", "Resumo das próximas horas", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Meteorologia próximos dias",
                "Meteo próximos dias",
                ("Previsão geral", "vento médio", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Avisos locais",
                "Que avisos locais estão em vigor?",
                ("Avisos locais", "aviso", "não estão configurados", "não consegui", "Sem avisos"),
            ),
            (
                "Aviso local individual",
                "Qual é o aviso local 91/26?",
                ("91/26", "Aviso", "Anav", "não estão configurados", "não consegui", "Sem avisos"),
            ),
        ]
        for label, question, expected_tokens in checks:
            payload = self._step(
                scenario,
                label,
                "Resposta live direta",
                "A consulta devolve resposta ou mensagem explícita de indisponibilidade, sem exceção.",
                lambda question=question: answer_direct_operational_query(question),
            )
            answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
            ok = isinstance(payload, dict) and bool(answer.strip()) and any(token in answer for token in expected_tokens)
            unavailable = any(
                marker in answer.casefold()
                for marker in (
                    "não está configurad",
                    "nao esta configurad",
                    "não estão configurad",
                    "nao estao configurad",
                    "não consegui",
                    "nao consegui",
                    "sem avisos",
                )
            )
            self._check_state(
                scenario,
                f"{label} tratada",
                "Erro trapping live",
                "A resposta é informativa; indisponibilidade externa conta como aviso, não como erro da página.",
                answer[:260] or "--",
                state="passed" if ok and not unavailable else "warning" if answer else "failed",
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
            self._scenario_vessel_catalog_management()
            self._scenario_slash_commands()
            self._scenario_site_queries_and_archive()
            self._scenario_live_feed_snapshot()
            self._scenario_live_environment_queries()
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
