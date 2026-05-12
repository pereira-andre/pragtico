#!/usr/bin/env python3
"""Standalone launchd runner for PRAGtico site QA.

This copy intentionally depends only on the Python standard library so it can
run from ~/Library/Application Support without importing the project checkout.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str
    question: str
    expected_substrings: tuple[str, ...]
    forbidden_substrings: tuple[str, ...]
    expected_answer_origin: str
    risk: str
    source: str
    strict: bool
    context: str
    parent_id: str


@dataclass
class SiteResult:
    ok: bool
    answer: str = ""
    status_code: int = 0
    conversation_id: str = ""
    message_id: str = ""
    answer_origin: str = ""
    sources: list[dict[str, Any]] | None = None
    error: str = ""
    latency_ms: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_periods() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_only.casefold()).strip()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def extract_csrf_token(page_html: str) -> str:
    match = re.search(
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        page_html,
        flags=re.IGNORECASE,
    )
    return html.unescape(match.group(1)) if match else ""


def as_scenario(raw: dict[str, Any]) -> Scenario:
    return Scenario(
        id=str(raw.get("id") or ""),
        group=str(raw.get("group") or ""),
        question=str(raw.get("question") or ""),
        expected_substrings=tuple(str(item) for item in raw.get("expected_substrings") or []),
        forbidden_substrings=tuple(str(item) for item in raw.get("forbidden_substrings") or []),
        expected_answer_origin=str(raw.get("expected_answer_origin") or ""),
        risk=str(raw.get("risk") or ""),
        source=str(raw.get("source") or ""),
        strict=bool(raw.get("strict", True)),
        context=str(raw.get("context") or "auto"),
        parent_id=str(raw.get("parent_id") or ""),
    )


def missing_tokens(answer: str, tokens: tuple[str, ...]) -> list[str]:
    normalized_answer = normalize_text(answer)
    return [token for token in tokens if normalize_text(token) not in normalized_answer]


def present_tokens(answer: str, tokens: tuple[str, ...]) -> list[str]:
    normalized_answer = normalize_text(answer)
    return [token for token in tokens if normalize_text(token) in normalized_answer]


def entities_in_question(question: str) -> list[str]:
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
    q = normalize_text(question)
    return [item for item in candidates if normalize_text(item) in q]


def evaluate_answer(scenario: Scenario, result: SiteResult) -> dict[str, Any]:
    answer = result.answer or ""
    missing = missing_tokens(answer, scenario.expected_substrings)
    forbidden = present_tokens(answer, scenario.forbidden_substrings)
    warnings: list[str] = []
    if not result.ok:
        return {
            "verdict": "error",
            "missing_expected": missing,
            "forbidden_present": forbidden,
            "warnings": [result.error or "transport_error"],
            "manual_review": True,
        }
    if not answer.strip():
        warnings.append("empty_answer")
    q = normalize_text(scenario.question)
    a = normalize_text(answer)
    if any(token in q for token in ("meteorologia", "vento", "ondulacao", "mares", "mare", "hoje", "atual", "condicoes")):
        if not any(token in a for token in ("atual", "hoje", "hora", "fonte", "meteorologia", "mare", "ondulacao", "disponivel")):
            warnings.append("live_context_not_explicit")
    if any(token in q for token in ("quantos reboc", "qts reboc", "quantos reboq", "qts reboq")):
        if not re.search(r"\b[1-6]\b|\bum\b|\bdois\b|\btres\b|\bquatro\b|\bcinco\b|\bseis\b", a):
            warnings.append("tug_count_not_clear")
    if "reponto" in q and not any(token in a for token in ("reponto", "preia", "baixa", "mare", "corrente")):
        warnings.append("reponto_not_addressed")
    if any(token in q for token in ("colreg", "rieam", "nevoeiro", "visibilidade reduzida")):
        if not any(token in a for token in ("velocidade de seguranca", "sinal", "visibilidade reduzida", "radar", "maquinas prontas")):
            warnings.append("colreg_procedure_thin")
    for entity in entities_in_question(scenario.question):
        if normalize_text(entity) not in a:
            warnings.append(f"entity_not_echoed:{entity}")
    if result.answer_origin and scenario.expected_answer_origin and result.answer_origin != scenario.expected_answer_origin:
        warnings.append(f"answer_origin_mismatch:{result.answer_origin}")
    if forbidden:
        verdict = "fail"
    elif missing and scenario.strict:
        verdict = "fail"
    elif missing or warnings or not scenario.expected_substrings:
        verdict = "review"
    else:
        verdict = "pass"
    return {
        "verdict": verdict,
        "missing_expected": missing,
        "forbidden_present": forbidden,
        "warnings": warnings,
        "manual_review": verdict in {"review", "fail"},
    }


class SiteClient:
    def __init__(self, base_url: str, email: str, password: str, timeout: int) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.email = email
        self.password = password
        self.timeout = timeout
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.csrf_token = ""

    def url(self, path: str) -> str:
        return urllib.parse.urljoin(self.base_url, path.lstrip("/"))

    def open(self, request: urllib.request.Request | str) -> tuple[int, str, str]:
        with self.opener.open(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.getcode() or 0), body, response.geturl()

    def login(self) -> None:
        _status, body, _url = self.open(self.url("/login"))
        csrf = extract_csrf_token(body)
        self.csrf_token = csrf
        form = urllib.parse.urlencode(
            {
                "email": self.email,
                "password": self.password,
                "csrf_token": csrf,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url("/login"),
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        _status, _body, final_url = self.open(request)
        if "/login" in final_url:
            raise RuntimeError("login_failed")
        if "/profile" in final_url:
            raise RuntimeError("profile_incomplete")

    def json_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PRAGticoBugHunter/1.0",
        }
        if self.csrf_token:
            headers["X-CSRFToken"] = self.csrf_token
        return headers

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers=self.json_headers(),
            method="POST",
        )
        started = datetime.now()
        try:
            status, body, _url = self.open(request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = int(exc.code or 0)
        latency_ms = int((datetime.now() - started).total_seconds() * 1000)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"raw_text": body[:1000]}
        data["_latency_ms"] = latency_ms
        return status, data

    def create_conversation(self) -> str:
        status, payload = self.post_json("/api/conversations", {})
        if status in {401, 403}:
            self.login()
            status, payload = self.post_json("/api/conversations", {})
        if status >= 400:
            raise RuntimeError(str(payload.get("error") or f"conversation_create_http_{status}"))
        return str((payload.get("conversation") or {}).get("id") or payload.get("conversation_id") or "").strip()

    def ask(self, question: str, conversation_id: str) -> SiteResult:
        try:
            self.login()
            status, payload = self.post_json("/api/chat", {"question": question, "conversation_id": conversation_id})
            if status in {401, 403}:
                self.login()
                status, payload = self.post_json("/api/chat", {"question": question, "conversation_id": conversation_id})
        except Exception as exc:
            return SiteResult(ok=False, error=str(exc))
        if status >= 400:
            return SiteResult(ok=False, status_code=status, error=str(payload.get("error") or payload))
        return SiteResult(
            ok=True,
            answer=str(payload.get("answer") or ""),
            status_code=status,
            conversation_id=str(payload.get("conversation_id") or (payload.get("conversation") or {}).get("id") or ""),
            message_id=str(payload.get("message_id") or ""),
            answer_origin=str(payload.get("answer_origin") or ""),
            sources=list(payload.get("sources") or []),
            latency_ms=int(payload.get("_latency_ms") or 0),
        )


def append_log(log_dir: Path, record: dict[str, Any]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"bug_hunter_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def summarize(log_dir: Path) -> dict[str, Any]:
    counts: dict[str, int] = {"pass": 0, "review": 0, "fail": 0, "error": 0}
    total = 0
    findings: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("bug_hunter_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            verdict = str((record.get("checks") or {}).get("verdict") or "error")
            counts[verdict] = counts.get(verdict, 0) + 1
            total += 1
            if verdict in {"review", "fail", "error"}:
                findings.append(record)
    lines = [
        "# PRAGtico bug hunter - latest",
        "",
        f"Updated: {utc_now_iso()}",
        f"Total: {total}",
        "",
        "## Counts",
        "",
    ]
    for key in ("pass", "review", "fail", "error"):
        lines.append(f"- {key}: {counts.get(key, 0)}")
    lines.extend(["", "## Findings", ""])
    if not findings:
        lines.append("- No findings.")
    for record in findings[-30:]:
        scenario = record.get("scenario") or {}
        checks = record.get("checks") or {}
        details = []
        if checks.get("missing_expected"):
            details.append("missing=" + ", ".join(checks["missing_expected"][:4]))
        if checks.get("forbidden_present"):
            details.append("forbidden=" + ", ".join(checks["forbidden_present"][:4]))
        if checks.get("warnings"):
            details.append("warnings=" + ", ".join(checks["warnings"][:4]))
        suffix = f" ({'; '.join(details)})" if details else ""
        lines.append(f"- {checks.get('verdict', 'unknown').upper()} {scenario.get('id', '')}: {scenario.get('question', '')[:130]}{suffix}")
    summary_path = log_dir / "latest.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"counts": counts, "total": total, "summary_path": str(summary_path)}


def normalize_budget(state: dict[str, Any]) -> dict[str, Any]:
    day, month = current_periods()
    budget = dict(state.get("budget") or {})
    if budget.get("day") != day:
        budget.update({"day": day, "turns_today": 0, "estimated_spend_today": 0.0})
    if budget.get("month") != month:
        budget.update({"month": month, "turns_this_month": 0, "estimated_spend_this_month": 0.0})
    return budget


def budget_allows(state: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    budget = normalize_budget(state)
    if int(budget.get("turns_this_month") or 0) >= int(config.get("monthly_turn_limit") or 100):
        return False, "monthly_turn_limit", budget
    if float(budget.get("estimated_spend_this_month") or 0.0) + float(config.get("estimated_cost_per_turn") or 0.01) > float(config.get("monthly_cost_cap") or 1.25):
        return False, "monthly_cost_cap", budget
    return True, "allowed", budget


def update_budget(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    budget = normalize_budget(state)
    cost = float(config.get("estimated_cost_per_turn") or 0.01)
    budget["turns_today"] = int(budget.get("turns_today") or 0) + 1
    budget["turns_this_month"] = int(budget.get("turns_this_month") or 0) + 1
    budget["estimated_spend_today"] = round(float(budget.get("estimated_spend_today") or 0.0) + cost, 6)
    budget["estimated_spend_this_month"] = round(float(budget.get("estimated_spend_this_month") or 0.0) + cost, 6)
    budget["estimated_cost_per_turn"] = cost
    state["budget"] = budget
    return budget


def should_reset(state: dict[str, Any], scenario: Scenario, config: dict[str, Any]) -> tuple[bool, str]:
    if scenario.context == "new":
        return True, "scenario_new"
    if scenario.context == "continue":
        return False, "scenario_continue"
    reset_every = int(config.get("reset_every") or 20)
    turn_count = int(state.get("turn_count") or 0)
    if reset_every > 0 and turn_count > 0 and turn_count % reset_every == 0:
        return True, f"reset_every_{reset_every}"
    return False, "continue"


def notify_if_done(state: dict[str, Any], config: dict[str, Any], log_dir: Path) -> None:
    if state.get("notified_at"):
        return
    target_total = int(config.get("target_total") or 100)
    if int(state.get("turn_count") or 0) < target_total:
        return
    summary = summarize(log_dir)
    counts = summary["counts"]
    bugs = counts.get("fail", 0) + counts.get("error", 0)
    message = "\n".join(
        [
            "PRAGtico bug hunter terminou.",
            f"Perguntas executadas: {state.get('turn_count')}",
            f"Corretos: {counts.get('pass', 0)}",
            f"Duvidas/rever: {counts.get('review', 0)}",
            f"Falhas/bugs: {bugs}",
            f"Budget estimado: ${float((state.get('budget') or {}).get('estimated_spend_this_month') or 0):.2f}",
            f"Resumo local: {summary['summary_path']}",
        ]
    )
    try:
        result = subprocess.run(
            [
                str(config.get("openclaw_bin") or "openclaw"),
                "message",
                "send",
                "--channel",
                "telegram",
                "--target",
                str(config.get("telegram_target") or ""),
                "--message",
                message,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            state["notified_at"] = utc_now_iso()
            state["notification_result"] = (result.stdout or "").strip()[:1000]
            plist = str(config.get("launch_agent_plist") or "").strip()
            if plist:
                subprocess.run(["launchctl", "unload", plist], timeout=30, check=False)
        else:
            state["notification_error"] = (result.stderr or result.stdout or "").strip()[:1000]
    except Exception as exc:
        state["notification_error"] = str(exc)


def run_once(config_path: Path) -> int:
    config = load_json(config_path, {})
    base_dir = config_path.parent
    state_path = base_dir / "state.json"
    log_dir = base_dir / "logs"
    scenarios = [as_scenario(item) for item in load_json(base_dir / "scenarios.json", []) if isinstance(item, dict)]
    if not scenarios:
        raise RuntimeError("No scenarios loaded.")
    state = load_json(state_path, {})
    if state.get("notified_at"):
        return 0
    if int(state.get("turn_count") or 0) >= int(config.get("target_total") or 100):
        notify_if_done(state, config, log_dir)
        save_json(state_path, state)
        return 0

    allowed, budget_reason, budget = budget_allows(state, config)
    state["budget"] = budget
    index = int(state.get("next_index") or 0) % len(scenarios)
    scenario = scenarios[index]
    reset_context, reset_reason = should_reset(state, scenario, config)
    if not allowed:
        result = SiteResult(ok=False, error=budget_reason)
        checks = {"verdict": "error", "missing_expected": [], "forbidden_present": [], "warnings": [budget_reason], "manual_review": True}
    else:
        client = SiteClient(
            str(config.get("base_url") or ""),
            str(config.get("email") or ""),
            str(config.get("password") or ""),
            int(config.get("timeout") or 180),
        )
        conversation_id = str(state.get("conversation_id") or "")
        if reset_context or not conversation_id:
            try:
                client.login()
                conversation_id = client.create_conversation()
            except Exception as exc:
                result = SiteResult(ok=False, error=f"context_reset_failed: {exc}")
                checks = evaluate_answer(scenario, result)
            else:
                result = client.ask(scenario.question, conversation_id)
                checks = evaluate_answer(scenario, result)
        else:
            result = client.ask(scenario.question, conversation_id)
            checks = evaluate_answer(scenario, result)
        update_budget(state, config)

    record = {
        "timestamp": utc_now_iso(),
        "scenario_index": index,
        "scenario": scenario.__dict__,
        "reset_context": reset_context,
        "reset_reason": reset_reason,
        "transport": {
            "name": "site-standalone",
            "ok": result.ok,
            "status_code": result.status_code,
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
            "answer_origin": result.answer_origin,
            "error": result.error,
            "latency_ms": result.latency_ms,
        },
        "answer": result.answer,
        "sources": result.sources or [],
        "checks": checks,
        "budget": {**state.get("budget", {}), "decision": budget_reason, "allowed": allowed},
    }
    append_log(log_dir, record)
    summary = summarize(log_dir)
    state.update(
        {
            "next_index": (index + 1) % len(scenarios),
            "turn_count": int(state.get("turn_count") or 0) + 1,
            "scheduler_ticks": int(state.get("scheduler_ticks") or 0) + 1,
            "conversation_id": result.conversation_id or state.get("conversation_id") or "",
            "last_scenario_id": scenario.id,
            "last_verdict": checks.get("verdict"),
            "updated_at": utc_now_iso(),
            "latest_summary_path": summary["summary_path"],
        }
    )
    notify_if_done(state, config, log_dir)
    save_json(state_path, state)
    print(f"{state['turn_count']}/100 {checks.get('verdict')} {scenario.id}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    return run_once(Path(args.config).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
