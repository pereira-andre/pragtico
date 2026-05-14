#!/usr/bin/env python3
"""Slow PRAGtico chat bug hunter for the site conversation API.

The script sends one operational question per run by default. It keeps state on
disk so a 5-minute scheduler can call it repeatedly without repeating the same
case, and it writes detailed JSONL evidence for later debugging.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover - runtime dependency is in requirements.txt
    requests = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "bot_bug_hunter"
DEFAULT_STATE_FILE = DEFAULT_LOG_DIR / "state.json"
DEFAULT_SCENARIO_LIMIT = 360
DEFAULT_DAILY_TURN_LIMIT = 12
DEFAULT_MONTHLY_TURN_LIMIT = 250
DEFAULT_ESTIMATED_COST_PER_TURN = 0.003
DEFAULT_DAILY_COST_CAP = 0.06
DEFAULT_MONTHLY_COST_CAP = 1.50


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str
    question: str
    expected_substrings: tuple[str, ...] = ()
    forbidden_substrings: tuple[str, ...] = ()
    expected_answer_origin: str = ""
    risk: str = "Medio"
    source: str = ""
    strict: bool = True
    context: str = "auto"
    parent_id: str = ""
    history: tuple[dict[str, str], ...] = ()


@dataclass
class TransportResult:
    ok: bool
    answer: str = ""
    status_code: int = 0
    conversation_id: str = ""
    message_id: str = ""
    answer_origin: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_budget_periods() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load simple KEY=VALUE pairs without printing secrets."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_only.casefold()).strip()


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char))


def scenario_key(question: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_text(question)).strip()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _safe_case_id(prefix: str, value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_text(value)).strip("-")
    return f"{prefix}-{slug[:72] or 'case'}"


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _manual_scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="manual-roro-230-norte-forte",
            group="Rebocadores",
            risk="Critico",
            question="Quantos reboques para RORO de 230m a entrar com vento norte forte?",
            expected_substrings=("4 rebocadores grandes", "mais de 220 m", "vento Norte forte"),
            source="knowledge/tug_operational_guidance.json",
        ),
        Scenario(
            id="manual-lisnave-300-rebocadores",
            group="Lisnave",
            risk="Critico",
            question="Um navio na LISNAVE de 300 m manobra com quantos rebocadores normalmente?",
            expected_substrings=("6 rebocador", "Lisnave acima de 250 m"),
            source="knowledge/tug_operational_guidance.json",
        ),
        Scenario(
            id="manual-tanquisado-two-tugs",
            group="Tanquisado",
            risk="Critico",
            question="Entrada para Tanquisado com 2 rebocadores pode avancar?",
            expected_substrings=("Tanquisado", "minimo 3 rebocadores", "Rebocadores insuficientes"),
            source="knowledge/tug_operational_guidance.json",
        ),
        Scenario(
            id="manual-ecooil-two-tugs",
            group="Eco-Oil",
            risk="Critico",
            question="Entrada para Eco-Oil com 2 rebocadores pode avancar?",
            expected_substrings=("Eco-Oil", "minimo 3 rebocadores", "Rebocadores insuficientes"),
            source="knowledge/tug_operational_guidance.json",
        ),
        Scenario(
            id="manual-secil-e-1925",
            group="SECIL e reponto",
            risk="Critico",
            question="Marquei manobra de entrada para a Secil E as 1925. Esta correta a hora?",
            expected_substrings=("Secil E", "30-45 min", "reponto"),
            forbidden_substrings=("nao ha proibicao", "horario das 19:25 e, portanto, permitido"),
            source="IT-009_Secil.txt",
        ),
        Scenario(
            id="manual-alstom-barra-preia-mar",
            group="ALSTOM",
            risk="Critico",
            question="Quanto tempo da Barra para o Cais Alstom para apanhar o reponto de preia-mar?",
            expected_substrings=("Cais ALSTOM", "1 hora e 30 minutos antes da preia-mar", "reponto de preia-mar"),
            source="IT-038_Alstom.txt",
        ),
        Scenario(
            id="manual-fog-underway-colreg",
            group="COLREG/RIEAM",
            risk="Critico",
            question="Se um navio for apanhado no meio do nevoeiro a navegar, que procedimentos deve adoptar segundo a COLREG?",
            expected_substrings=("velocidade de seguranca", "sinais", "visibilidade reduzida"),
            source="RIEAM_COLREG_Regras_Estrada.txt",
        ),
        Scenario(
            id="manual-meteo-live",
            group="Meteorologia live",
            risk="Alto",
            question="Face a meteorologia atual em Setubal, um Ro-Ro de 200 m pode sair da Autoeuropa com 2 rebocadores?",
            expected_substrings=("Meteorologia", "Ro-Ro", "Autoeuropa", "rebocadores"),
            source="meteorologia live + tug guidance",
            strict=False,
        ),
        Scenario(
            id="manual-mares-hoje",
            group="Mares",
            risk="Alto",
            question="/mares hoje",
            expected_substrings=("mare",),
            source="resources/tides",
            strict=False,
            context="new",
        ),
        Scenario(
            id="manual-ondulacao",
            group="Ondulacao",
            risk="Medio",
            question="/ondulacao",
            expected_substrings=("ondulacao",),
            source="wave service",
            strict=False,
            context="new",
        ),
    ]


def _matrix_scenarios() -> list[Scenario]:
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from core.operational_test_suite import critical_bot_test_matrix
    except Exception:
        return []

    scenarios: list[Scenario] = []
    for item in critical_bot_test_matrix():
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        mode = str(item.get("mode") or "").casefold()
        if "manual" in mode and not item.get("expected_tokens"):
            strict = False
        else:
            strict = True
        scenarios.append(
            Scenario(
                id=f"matrix-{item.get('id') or _safe_case_id('case', question)}",
                group=str(item.get("group") or "Matriz critica"),
                question=question,
                expected_substrings=_as_tuple(item.get("expected_tokens")),
                forbidden_substrings=_as_tuple(item.get("forbidden_tokens")),
                expected_answer_origin=str(item.get("expected_origin") or ""),
                risk=str(item.get("risk") or "Medio"),
                source=str(item.get("source") or ""),
                strict=strict,
                context="continue" if item.get("history") else "auto",
                history=tuple(
                    {"role": str(entry.get("role") or ""), "content": str(entry.get("content") or "")}
                    for entry in (item.get("history") or [])
                    if isinstance(entry, dict) and str(entry.get("content") or "").strip()
                ),
            )
        )
    return scenarios


def _eval_file_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    eval_dir = PROJECT_ROOT / "knowledge" / "evals"
    for path in sorted(eval_dir.glob("*.json")):
        payload = load_json(path, [])
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            eval_type = str(item.get("eval_type") or "").strip()
            scenarios.append(
                Scenario(
                    id=_safe_case_id(path.stem, question),
                    group=f"Evals/{path.stem}",
                    question=question,
                    expected_substrings=_as_tuple(item.get("expected_substrings")),
                    expected_answer_origin=str(item.get("expected_answer_origin") or ""),
                    risk="Alto" if eval_type == "direct_operational" else "Medio",
                    source=str(item.get("document") or path.name),
                    strict=True,
                    context="auto",
                )
            )
    return scenarios


def _domain_variation_questions() -> list[Scenario]:
    berths = [
        ("LISNAVE", "Lisnave"),
        ("Tanquisado", "Tanquisado"),
        ("Eco-Oil", "Eco-Oil"),
        ("SECIL E", "Secil E"),
        ("SECIL W", "Secil W"),
        ("SAPEC Liquidos", "SAPEC Liquidos"),
        ("SAPEC Solidos", "SAPEC Solidos"),
        ("Teporset", "Teporset"),
        ("TMS1", "TMS1"),
        ("TMS2", "TMS2"),
        ("Autoeuropa", "Autoeuropa"),
        ("Alstom", "Alstom"),
    ]
    questions: list[Scenario] = []
    for slug, label in berths:
        questions.extend(
            [
                Scenario(
                    id=f"terminal-{slug.lower().replace(' ', '-')}-restricoes",
                    group="Terminais e cais",
                    question=f"Quais sao as principais restricoes operacionais do {label}?",
                    expected_substrings=(label.split()[0],),
                    risk="Alto",
                    source="knowledge/companions",
                    strict=False,
                    context="new",
                ),
                Scenario(
                    id=f"terminal-{slug.lower().replace(' ', '-')}-calado",
                    group="Terminais e cais",
                    question=f"Tenho um navio com 9,2 m de calado para {label}. Ha algum limite critico?",
                    expected_substrings=(label.split()[0], "calado"),
                    risk="Alto",
                    source="berth profiles + knowledge",
                    strict=False,
                ),
                Scenario(
                    id=f"terminal-{slug.lower().replace(' ', '-')}-reponto",
                    group="Repontos de mare",
                    question=f"Para manobrar no {label}, tenho de acertar com o reponto de mare?",
                    expected_substrings=(label.split()[0], "reponto"),
                    risk="Alto",
                    source="knowledge",
                    strict=False,
                ),
            ]
        )
    return questions


def _variant_scenarios(seeds: list[Scenario], target_total: int) -> list[Scenario]:
    prefixes = [
        "Pergunta rapida: ",
        "Confirma-me uma coisa: ",
        "Estou a planear agora. ",
        "Como piloto, ",
    ]
    replacements = [
        (r"\brebocadores\b", "reboques"),
        (r"\brebocador\b", "reboque"),
        (r"\bQuantos\b", "Qts"),
        (r"\bmetros\b", "m"),
        (r"\bSetubal\b", "porto de Setubal"),
        (r"\bpreia-mar\b", "PM"),
        (r"\bbaixa-mar\b", "BM"),
    ]
    variants: list[Scenario] = []
    for seed in seeds:
        if len(seeds) + len(variants) >= target_total:
            break
        candidates = [strip_accents(seed.question)]
        candidates.extend(prefix + seed.question[:1].lower() + seed.question[1:] for prefix in prefixes)
        for pattern, new in replacements:
            if re.search(pattern, seed.question, flags=re.IGNORECASE):
                candidates.append(re.sub(pattern, new, seed.question, flags=re.IGNORECASE))
        for index, question in enumerate(candidates, start=1):
            if question == seed.question or not question.strip():
                continue
            variants.append(
                Scenario(
                    id=f"{seed.id}-variant-{index}",
                    group=seed.group,
                    question=question,
                    expected_substrings=seed.expected_substrings,
                    forbidden_substrings=seed.forbidden_substrings,
                    expected_answer_origin=seed.expected_answer_origin,
                    risk=seed.risk,
                    source=seed.source,
                    strict=False,
                    context=seed.context,
                    parent_id=seed.id,
                    history=seed.history,
                )
            )
            if len(seeds) + len(variants) >= target_total:
                break
    return variants


def build_scenarios(limit: int = DEFAULT_SCENARIO_LIMIT) -> list[Scenario]:
    base = _manual_scenarios() + _matrix_scenarios() + _eval_file_scenarios() + _domain_variation_questions()
    seen: set[str] = set()
    unique: list[Scenario] = []
    for scenario in base:
        key = scenario_key(scenario.question)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(scenario)

    target = max(limit, len(unique))
    for scenario in _variant_scenarios(unique, target):
        key = scenario_key(scenario.question)
        if key in seen:
            continue
        seen.add(key)
        unique.append(scenario)
        if len(unique) >= limit:
            break
    return unique[:limit]


def _find_case_insensitive_tokens(answer: str, tokens: tuple[str, ...]) -> list[str]:
    normalized_answer = normalize_text(answer)
    missing: list[str] = []
    for token in tokens:
        if normalize_text(token) not in normalized_answer:
            missing.append(token)
    return missing


def _present_tokens(answer: str, tokens: tuple[str, ...]) -> list[str]:
    normalized_answer = normalize_text(answer)
    return [token for token in tokens if normalize_text(token) in normalized_answer]


def _entities_in_question(question: str) -> list[str]:
    candidates = [
        "Lisnave",
        "Tanquisado",
        "Eco-Oil",
        "Secil",
        "SAPEC",
        "Teporset",
        "TMS1",
        "TMS2",
        "Autoeuropa",
        "Alstom",
        "Praias do Sado",
        "Outao",
        "Joao Farto",
    ]
    normalized_question = normalize_text(question)
    return [item for item in candidates if normalize_text(item) in normalized_question]


def evaluate_answer(scenario: Scenario, result: TransportResult) -> dict[str, Any]:
    answer = result.answer or ""
    missing = _find_case_insensitive_tokens(answer, scenario.expected_substrings)
    forbidden = _present_tokens(answer, scenario.forbidden_substrings)
    warnings: list[str] = []

    if not result.ok:
        return {
            "verdict": "error",
            "missing_expected": list(missing),
            "forbidden_present": list(forbidden),
            "warnings": [result.error or "transport_error"],
            "manual_review": True,
        }

    if not answer.strip():
        warnings.append("empty_answer")

    normalized_question = normalize_text(scenario.question)
    normalized_answer = normalize_text(answer)

    explicit_live_context = any(
        token in normalized_question
        for token in ("meteorologia", "ondulacao", "mares", "hoje", "atual", "condicoes atuais", "condições atuais")
    )
    declared_wind_scenario = bool(
        "vento" in normalized_question
        and re.search(r"\b(n|s|e|w|norte|sul|este|leste|oeste|forte|fraco|kt|kts|nos|nós)\b", normalized_question)
        and not explicit_live_context
    )
    live_question = any(
        token in normalized_question
        for token in ("meteorologia", "vento", "ondulacao", "mares", "mare", "hoje", "atual", "condicoes")
    ) and not declared_wind_scenario
    if live_question:
        if not any(token in normalized_answer for token in ("atual", "hoje", "hora", "fonte", "meteorologia", "mare", "ondulacao", "disponivel")):
            warnings.append("live_context_not_explicit")

    if any(token in normalized_question for token in ("quantos reboc", "qts reboc", "quantos reboq", "qts reboq")):
        if not re.search(r"\b[1-6]\b|\bum\b|\bdois\b|\btres\b|\bquatro\b|\bcinco\b|\bseis\b", normalized_answer):
            warnings.append("tug_count_not_clear")

    if "reponto" in normalized_question:
        if not any(token in normalized_answer for token in ("reponto", "preia", "baixa", "mare", "corrente")):
            warnings.append("reponto_not_addressed")

    if any(token in normalized_question for token in ("colreg", "rieam", "nevoeiro", "visibilidade reduzida")):
        if not any(token in normalized_answer for token in ("velocidade de seguranca", "sinal", "visibilidade reduzida", "radar", "maquinas prontas")):
            warnings.append("colreg_procedure_thin")

    for entity in _entities_in_question(scenario.question):
        if normalize_text(entity) not in normalized_answer:
            warnings.append(f"entity_not_echoed:{entity}")

    if result.answer_origin and scenario.expected_answer_origin:
        if result.answer_origin != scenario.expected_answer_origin:
            warnings.append(f"answer_origin_mismatch:{result.answer_origin}")

    if forbidden:
        verdict = "fail"
    elif missing and scenario.strict:
        verdict = "fail"
    elif missing or warnings:
        verdict = "review"
    else:
        verdict = "pass"

    return {
        "verdict": verdict,
        "missing_expected": list(missing),
        "forbidden_present": list(forbidden),
        "warnings": warnings,
        "manual_review": verdict in {"review", "fail"},
    }


def extract_csrf_token(page_html: str) -> str:
    match = re.search(
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        page_html,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return html.unescape(match.group(1))


class SiteChatTransport:
    def __init__(self, *, base_url: str, email: str, password: str, timeout: int = 180) -> None:
        if requests is None:
            raise RuntimeError("Instala as dependencias do projeto: pip install -r requirements.txt")
        self.base_url = base_url.rstrip("/") + "/"
        self.email = email
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PRAGticoBugHunter/1.0"})
        self.logged_in = False
        self.csrf_token = ""

    def url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def login(self) -> None:
        if self.logged_in:
            return
        if not self.email or not self.password:
            raise RuntimeError("Define PRAGTICO_BUG_HUNTER_EMAIL/PASSWORD ou ADMIN_EMAIL/ADMIN_PASSWORD.")
        login_page = self.session.get(self.url("/login"), timeout=self.timeout)
        login_page.raise_for_status()
        csrf_token = extract_csrf_token(login_page.text)
        self.csrf_token = csrf_token
        response = self.session.post(
            self.url("/login"),
            data={
                "email": self.email,
                "password": self.password,
                "csrf_token": csrf_token,
            },
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        probe = self.session.get(
            self.url("/conversations"),
            headers={"Accept": "text/html"},
            timeout=self.timeout,
            allow_redirects=True,
        )
        if probe.status_code >= 400:
            raise RuntimeError(f"Login falhou ou perfil incompleto: HTTP {probe.status_code}.")
        if "/login" in probe.url:
            raise RuntimeError("Login falhou: continuo na pagina de login.")
        if "/profile" in probe.url:
            raise RuntimeError("Login feito, mas o perfil da conta QA esta incompleto.")
        self.logged_in = True

    def _json_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.csrf_token:
            headers["X-CSRFToken"] = self.csrf_token
        return headers

    def create_conversation(self) -> str:
        self.login()
        response = self.session.post(
            self.url("/api/conversations"),
            headers=self._json_headers(),
            json={},
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            self.logged_in = False
            self.login()
            response = self.session.post(
                self.url("/api/conversations"),
                headers=self._json_headers(),
                json={},
                timeout=self.timeout,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Falha ao criar conversa: HTTP {response.status_code}: {response.text[:240]}")
        payload = response.json()
        return str((payload.get("conversation") or {}).get("id") or payload.get("conversation_id") or "").strip()

    def ask(self, question: str, *, conversation_id: str = "") -> TransportResult:
        self.login()
        started = time.monotonic()
        try:
            response = self.session.post(
                self.url("/api/chat"),
                headers=self._json_headers(),
                json={"question": question, "conversation_id": conversation_id},
                timeout=self.timeout,
            )
            if response.status_code in {401, 403}:
                self.logged_in = False
                self.login()
                response = self.session.post(
                    self.url("/api/chat"),
                    headers=self._json_headers(),
                    json={"question": question, "conversation_id": conversation_id},
                    timeout=self.timeout,
                )
        except Exception as exc:
            return TransportResult(ok=False, error=str(exc))
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": response.text[:1000]}
        if response.status_code >= 400:
            return TransportResult(
                ok=False,
                status_code=response.status_code,
                error=str(payload.get("error") or response.text[:300]),
                raw={**payload, "latency_ms": latency_ms},
            )
        return TransportResult(
            ok=True,
            answer=str(payload.get("answer") or ""),
            status_code=response.status_code,
            conversation_id=str(payload.get("conversation_id") or (payload.get("conversation") or {}).get("id") or ""),
            message_id=str(payload.get("message_id") or ""),
            answer_origin=str(payload.get("answer_origin") or ""),
            sources=list(payload.get("sources") or []),
            raw={**payload, "latency_ms": latency_ms},
        )


def should_reset_context(state: dict[str, Any], scenario: Scenario, args: argparse.Namespace) -> tuple[bool, str]:
    policy = args.context_policy
    if policy == "always-new":
        return True, "policy_always_new"
    if policy == "never-new":
        return False, "policy_never_new"
    if scenario.history:
        return True, "scenario_history_replay"
    if scenario.context == "new":
        return True, "scenario_new"
    if scenario.context == "continue":
        return False, "scenario_continue"
    reset_every = int(args.reset_every or 0)
    turn_count = int(state.get("turn_count") or 0)
    if reset_every > 0 and turn_count > 0 and turn_count % reset_every == 0:
        return True, f"reset_every_{reset_every}"
    return False, "continue"


def _budget_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    budget = dict(state.get("budget") or {})
    current_day, current_month = current_budget_periods()
    if budget.get("day") != current_day:
        budget["day"] = current_day
        budget["turns_today"] = 0
        budget["estimated_spend_today"] = 0.0
    if budget.get("month") != current_month:
        budget["month"] = current_month
        budget["turns_this_month"] = 0
        budget["estimated_spend_this_month"] = 0.0
    return budget


def budget_decision(state: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str, dict[str, Any]]:
    budget = _budget_snapshot(state)
    if args.force:
        return True, "force", budget

    daily_turn_limit = max(int(args.daily_turn_limit or 0), 0)
    monthly_turn_limit = max(int(args.monthly_turn_limit or 0), 0)
    estimated_cost = max(float(args.estimated_cost_per_turn or 0), 0.0)
    daily_cost_cap = max(float(args.daily_cost_cap or 0), 0.0)
    monthly_cost_cap = max(float(args.monthly_cost_cap or 0), 0.0)

    if daily_turn_limit and int(budget.get("turns_today") or 0) >= daily_turn_limit:
        return False, f"daily_turn_limit_{daily_turn_limit}", budget
    if monthly_turn_limit and int(budget.get("turns_this_month") or 0) >= monthly_turn_limit:
        return False, f"monthly_turn_limit_{monthly_turn_limit}", budget
    if daily_cost_cap and float(budget.get("estimated_spend_today") or 0.0) + estimated_cost > daily_cost_cap:
        return False, f"daily_cost_cap_{daily_cost_cap:.4f}", budget
    if monthly_cost_cap and float(budget.get("estimated_spend_this_month") or 0.0) + estimated_cost > monthly_cost_cap:
        return False, f"monthly_cost_cap_{monthly_cost_cap:.4f}", budget
    return True, "allowed", budget


def record_budget_turn(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    budget = _budget_snapshot(state)
    estimated_cost = max(float(args.estimated_cost_per_turn or 0), 0.0)
    budget["turns_today"] = int(budget.get("turns_today") or 0) + 1
    budget["turns_this_month"] = int(budget.get("turns_this_month") or 0) + 1
    budget["estimated_spend_today"] = round(float(budget.get("estimated_spend_today") or 0.0) + estimated_cost, 6)
    budget["estimated_spend_this_month"] = round(
        float(budget.get("estimated_spend_this_month") or 0.0) + estimated_cost,
        6,
    )
    budget["estimated_cost_per_turn"] = estimated_cost
    state["budget"] = budget
    return budget


def next_scenario(state: dict[str, Any], scenarios: list[Scenario]) -> tuple[int, Scenario]:
    index = int(state.get("next_index") or 0) % len(scenarios)
    return index, scenarios[index]


def append_log(log_dir: Path, record: dict[str, Any]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = log_dir / f"bug_hunter_{day}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_recent_records(log_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("bug_hunter_*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(records) >= limit:
                return list(reversed(records))
    return list(reversed(records))


def write_summary(log_dir: Path, limit: int = 50) -> Path:
    records = load_recent_records(log_dir, limit=limit)
    total = len(records)
    counts: dict[str, int] = {}
    findings = []
    for record in records:
        verdict = str(((record.get("checks") or {}).get("verdict")) or "unknown")
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict in {"fail", "review", "error"}:
            findings.append(record)
    lines = [
        "# PRAGtico bug hunter - latest",
        "",
        f"Updated: {utc_now_iso()}",
        f"Window: last {total} turns",
        "",
        "## Counts",
        "",
    ]
    for key in ("pass", "review", "fail", "error", "unknown"):
        if counts.get(key):
            lines.append(f"- {key}: {counts[key]}")
    if not any(counts.values()):
        lines.append("- no records yet")
    lines.extend(["", "## Findings", ""])
    if not findings:
        lines.append("- No findings in the current window.")
    else:
        for record in findings[-20:]:
            checks = record.get("checks") or {}
            scenario = record.get("scenario") or {}
            details = []
            if checks.get("missing_expected"):
                details.append("missing=" + ", ".join(checks["missing_expected"][:4]))
            if checks.get("forbidden_present"):
                details.append("forbidden=" + ", ".join(checks["forbidden_present"][:4]))
            if checks.get("warnings"):
                details.append("warnings=" + ", ".join(checks["warnings"][:4]))
            suffix = f" ({'; '.join(details)})" if details else ""
            lines.append(
                f"- {checks.get('verdict', 'unknown').upper()} {scenario.get('id', '')}: "
                f"{scenario.get('question', '')[:140]}{suffix}"
            )
    path = log_dir / "latest.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def summarize_records(log_dir: Path) -> dict[str, Any]:
    counts = {"pass": 0, "review": 0, "fail": 0, "error": 0, "budget-skip": 0, "other": 0}
    total = 0
    latest_timestamp = ""
    for path in sorted(log_dir.glob("bug_hunter_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            verdict = str(((record.get("checks") or {}).get("verdict")) or "other")
            counts[verdict if verdict in counts else "other"] += 1
            total += 1
            latest_timestamp = str(record.get("timestamp") or latest_timestamp)
    return {
        "total": total,
        "counts": counts,
        "latest_timestamp": latest_timestamp,
    }


def completion_message(*, state: dict[str, Any], log_dir: Path) -> str:
    summary = summarize_records(log_dir)
    counts = summary["counts"]
    bugs = counts.get("fail", 0) + counts.get("error", 0)
    latest_summary = log_dir / "latest.md"
    return "\n".join(
        [
            "PRAGtico bug hunter terminou.",
            f"Perguntas executadas: {int(state.get('turn_count') or 0)}",
            f"Corretos: {counts.get('pass', 0)}",
            f"Duvidas/rever: {counts.get('review', 0)}",
            f"Falhas/bugs: {bugs}",
            f"Budget estimado: ${float((state.get('budget') or {}).get('estimated_spend_this_month') or 0):.2f}",
            f"Resumo local: {latest_summary}",
            f"Log local: {log_dir}",
        ]
    )


def maybe_notify_completion(args: argparse.Namespace) -> None:
    completion_turns = max(int(args.completion_turns or 0), 0)
    if completion_turns <= 0:
        return
    state_path = Path(args.state_file)
    state = load_json(state_path, {})
    if int(state.get("turn_count") or 0) < completion_turns:
        return
    if state.get("notified_at"):
        return

    target = str(args.notify_telegram_target or "").strip()
    if not target:
        return
    openclaw_bin = str(args.openclaw_bin or "").strip() or shutil.which("openclaw") or "openclaw"
    message = completion_message(state=state, log_dir=Path(args.log_dir))
    try:
        result = subprocess.run(
            [
                openclaw_bin,
                "message",
                "send",
                "--channel",
                "telegram",
                "--target",
                target,
                "--message",
                message,
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        state["notification_error"] = str(exc)
        save_json(state_path, state)
        return

    if result.returncode == 0:
        state["notified_at"] = utc_now_iso()
        state["notification_target"] = target
        state["notification_result"] = (result.stdout or "").strip()[:1000]
        save_json(state_path, state)
        plist = str(args.launch_agent_plist or "").strip()
        if plist:
            subprocess.run(["launchctl", "unload", plist], check=False, timeout=30)
    else:
        state["notification_error"] = ((result.stderr or result.stdout or "").strip())[:1000]
        save_json(state_path, state)


def build_record(
    *,
    scenario: Scenario,
    scenario_index: int,
    reset_context: bool,
    reset_reason: str,
    result: TransportResult,
    checks: dict[str, Any],
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": utc_now_iso(),
        "scenario_index": scenario_index,
        "scenario": asdict(scenario),
        "reset_context": reset_context,
        "reset_reason": reset_reason,
        "transport": {
            "name": "site",
            "ok": result.ok,
            "status_code": result.status_code,
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
            "answer_origin": result.answer_origin,
            "error": result.error,
            "latency_ms": (result.raw or {}).get("latency_ms"),
        },
        "answer": result.answer,
        "sources": result.sources,
        "checks": checks,
        "budget": budget or {},
    }


def run_one_turn(args: argparse.Namespace, scenarios: list[Scenario]) -> dict[str, Any]:
    state = load_json(Path(args.state_file), {})
    allowed_by_budget, budget_reason, budget = budget_decision(state, args)
    state["budget"] = budget
    scenario_index, scenario = next_scenario(state, scenarios)
    reset_context, reset_reason = should_reset_context(state, scenario, args)

    if not allowed_by_budget and not args.dry_run:
        result = TransportResult(
            ok=True,
            answer=f"BUDGET PAUSED - no request sent ({budget_reason}).",
            conversation_id=str(state.get("conversation_id") or ""),
        )
        checks = {
            "verdict": "budget-skip",
            "missing_expected": [],
            "forbidden_present": [],
            "warnings": [budget_reason],
            "manual_review": False,
        }
    elif args.dry_run:
        result = TransportResult(
            ok=True,
            answer="DRY RUN - no request sent.",
            conversation_id=str(state.get("conversation_id") or ""),
        )
        checks = {"verdict": "dry-run", "missing_expected": [], "forbidden_present": [], "warnings": [], "manual_review": False}
    else:
        transport = SiteChatTransport(
            base_url=args.base_url,
            email=args.email,
            password=args.password,
            timeout=args.timeout,
        )
        conversation_id = str(state.get("conversation_id") or "")
        if reset_context or not conversation_id:
            try:
                conversation_id = transport.create_conversation()
            except Exception as exc:
                result = TransportResult(ok=False, error=f"context_reset_failed: {exc}")
                checks = evaluate_answer(scenario, result)
            else:
                for history_item in scenario.history:
                    if str(history_item.get("role") or "").lower() != "user":
                        continue
                    history_question = str(history_item.get("content") or "").strip()
                    if not history_question or normalize_text(history_question) == normalize_text(scenario.question):
                        continue
                    transport.ask(history_question, conversation_id=conversation_id)
                result = transport.ask(scenario.question, conversation_id=conversation_id)
                checks = evaluate_answer(scenario, result)
        else:
            result = transport.ask(scenario.question, conversation_id=conversation_id)
            checks = evaluate_answer(scenario, result)
        budget = record_budget_turn(state, args)

    record = build_record(
        scenario=scenario,
        scenario_index=scenario_index,
        reset_context=reset_context,
        reset_reason=reset_reason,
        result=result,
        checks=checks,
        budget={**budget, "decision": budget_reason, "allowed": allowed_by_budget},
    )
    log_path = append_log(Path(args.log_dir), record)
    summary_path = write_summary(Path(args.log_dir), limit=args.summary_window)

    consumed_turn = checks.get("verdict") not in {"budget-skip"}
    next_index = (scenario_index + 1) % len(scenarios) if consumed_turn else scenario_index
    state.update(
        {
            "next_index": next_index,
            "turn_count": int(state.get("turn_count") or 0) + (1 if consumed_turn else 0),
            "scheduler_ticks": int(state.get("scheduler_ticks") or 0) + 1,
            "updated_at": utc_now_iso(),
            "conversation_id": result.conversation_id or state.get("conversation_id") or "",
            "last_scenario_id": scenario.id,
            "last_log_path": str(log_path),
            "latest_summary_path": str(summary_path),
        }
    )
    save_json(Path(args.state_file), state)
    return record


def env_default(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a slow PRAGtico site chat QA turn and log possible answer inconsistencies."
    )
    parser.add_argument("--base-url", default=env_default("PRAGTICO_SITE_URL", "PRAGTICO_BASE_URL", default="http://127.0.0.1:5000"))
    parser.add_argument("--email", default=env_default("PRAGTICO_BUG_HUNTER_EMAIL", "ADMIN_EMAIL"))
    parser.add_argument("--password", default=env_default("PRAGTICO_BUG_HUNTER_PASSWORD", "ADMIN_PASSWORD"))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--scenario-limit", type=int, default=DEFAULT_SCENARIO_LIMIT)
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--reset-every", type=int, default=12)
    parser.add_argument("--daily-turn-limit", type=int, default=int(env_default("PRAGTICO_BUG_HUNTER_DAILY_TURN_LIMIT", default=str(DEFAULT_DAILY_TURN_LIMIT))))
    parser.add_argument("--monthly-turn-limit", type=int, default=int(env_default("PRAGTICO_BUG_HUNTER_MONTHLY_TURN_LIMIT", default=str(DEFAULT_MONTHLY_TURN_LIMIT))))
    parser.add_argument("--estimated-cost-per-turn", type=float, default=float(env_default("PRAGTICO_BUG_HUNTER_ESTIMATED_COST_PER_TURN", default=str(DEFAULT_ESTIMATED_COST_PER_TURN))))
    parser.add_argument("--daily-cost-cap", type=float, default=float(env_default("PRAGTICO_BUG_HUNTER_DAILY_COST_CAP", default=str(DEFAULT_DAILY_COST_CAP))))
    parser.add_argument("--monthly-cost-cap", type=float, default=float(env_default("PRAGTICO_BUG_HUNTER_MONTHLY_COST_CAP", default=str(DEFAULT_MONTHLY_COST_CAP))))
    parser.add_argument("--summary-window", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--context-policy", choices=["auto", "always-new", "never-new"], default="auto")
    parser.add_argument("--once", action="store_true", help="Run exactly one turn. This is the scheduler-friendly mode.")
    parser.add_argument("--loop", action="store_true", help="Run continuously, sleeping --interval seconds between turns.")
    parser.add_argument("--max-turns", type=int, default=0)
    parser.add_argument("--random", action="store_true", help="Shuffle the scenario queue for ad hoc batch runs.")
    parser.add_argument("--seed", type=int, default=20260512)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Bypass local budget guardrails for a manual run.")
    parser.add_argument("--completion-turns", type=int, default=0)
    parser.add_argument("--notify-telegram-target", default=env_default("OPENCLAW_TELEGRAM_TARGET"))
    parser.add_argument("--openclaw-bin", default=env_default("OPENCLAW_BIN", default="/Users/andrepereira/.nvm/versions/node/v24.2.0/bin/openclaw"))
    parser.add_argument("--launch-agent-plist", default="")
    parser.add_argument("--no-dotenv", action="store_true")
    parser.add_argument("--show-answer", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    early_args = list(argv if argv is not None else sys.argv[1:])
    if "--no-dotenv" not in early_args:
        load_dotenv()
    args = parse_args(argv)

    scenarios = build_scenarios(limit=max(args.scenario_limit, 1))
    if not scenarios:
        print("No scenarios available.", file=sys.stderr)
        return 2
    if args.random:
        random.Random(args.seed).shuffle(scenarios)

    turns = 1
    if args.loop:
        turns = args.max_turns if args.max_turns > 0 else sys.maxsize
    elif args.max_turns > 0:
        turns = args.max_turns

    exit_code = 0
    for turn in range(turns):
        record = run_one_turn(args, scenarios)
        checks = record.get("checks") or {}
        scenario = record.get("scenario") or {}
        verdict = checks.get("verdict", "unknown")
        print(
            f"[{record.get('timestamp')}] {verdict.upper()} "
            f"{scenario.get('id')}: {scenario.get('question')}"
        )
        if args.show_answer:
            print(record.get("answer", "").strip())
        maybe_notify_completion(args)
        if verdict in {"fail", "error"}:
            exit_code = 1
        if turn + 1 >= turns:
            break
        if args.loop:
            time.sleep(max(args.interval, 1))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
