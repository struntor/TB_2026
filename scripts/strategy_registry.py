from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_DIR = PROJECT_ROOT / "docs" / "strategy_portfolio"
REGISTRY_PATH = PORTFOLIO_DIR / "registry.json"
PORTFOLIO_PATH = PORTFOLIO_DIR / "PORTFOLIO.md"
AUDITS_DIR = PORTFOLIO_DIR / "audits"
TICK = chr(96)

LIFECYCLE_STAGES = {
    "idea",
    "exploration",
    "statistical_evidence",
    "backtest_validation",
    "robustness_validation",
    "money_management",
    "bot_integration",
    "paper_trading",
    "production",
}
WORK_STATES = {
    "active",
    "frozen_candidate",
    "parked",
    "blocked",
    "monitoring",
    "rejected",
    "retired",
}
PRIORITIES = {"primary", "secondary", "quick_probe", "backlog"}
REQUIRED_STRATEGY_FIELDS = {
    "strategy_id",
    "parent_strategy_id",
    "display_name",
    "family",
    "lifecycle_stage",
    "work_state",
    "priority",
    "current_summary",
    "hypothesis",
    "research_root",
    "primary_source",
    "validation",
    "audit",
    "money_management",
    "ibkr_integration",
    "production_eligible",
    "next_action",
    "last_updated",
}
PRIORITY_ORDER = {"primary": 0, "quick_probe": 1, "secondary": 2, "backlog": 3}
AUDIT_EVIDENCE_LEVELS = {"documented_only", "artifact_verified", "reproduced"}
STAGE_ORDER = {
    "production": 0,
    "paper_trading": 1,
    "bot_integration": 2,
    "money_management": 3,
    "robustness_validation": 4,
    "backtest_validation": 5,
    "statistical_evidence": 6,
    "exploration": 7,
    "idea": 8,
}


def load_registry() -> dict[str, Any]:
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("registry root must be an object")
    return payload


def validate_registry(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    strategies = payload.get("strategies")
    if not isinstance(strategies, list):
        return errors + ["strategies must be a list"]

    ids: list[str] = []
    for index, strategy in enumerate(strategies):
        label = f"strategies[{index}]"
        if not isinstance(strategy, dict):
            errors.append(f"{label} must be an object")
            continue

        missing = sorted(REQUIRED_STRATEGY_FIELDS - strategy.keys())
        if missing:
            errors.append(f"{label} missing fields: {', '.join(missing)}")

        strategy_id = strategy.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id:
            errors.append(f"{label}.strategy_id must be a non-empty string")
        else:
            ids.append(strategy_id)
            label = strategy_id

        if strategy.get("lifecycle_stage") not in LIFECYCLE_STAGES:
            errors.append(f"{label}: invalid lifecycle_stage")
        if strategy.get("work_state") not in WORK_STATES:
            errors.append(f"{label}: invalid work_state")
        if strategy.get("priority") not in PRIORITIES:
            errors.append(f"{label}: invalid priority")
        if not isinstance(strategy.get("production_eligible"), bool):
            errors.append(f"{label}: production_eligible must be boolean")

        for field in ("current_summary", "hypothesis", "next_action", "last_updated"):
            value = strategy.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}: {field} must be a non-empty string")

        try:
            date.fromisoformat(str(strategy.get("last_updated")))
        except ValueError:
            errors.append(f"{label}: last_updated must use YYYY-MM-DD")

        integration = strategy.get("ibkr_integration")
        if not isinstance(integration, dict):
            errors.append(f"{label}: ibkr_integration must be an object")
            integration = {}
        for flag in ("dry_run_verified", "paper_verified"):
            if not isinstance(integration.get(flag), bool):
                errors.append(f"{label}: ibkr_integration.{flag} must be boolean")

        audit = strategy.get("audit")
        if not isinstance(audit, dict):
            errors.append(f"{label}: audit must be an object")
            audit = {}
        if audit.get("status") != "completed":
            errors.append(f"{label}: audit.status must be completed")
        if audit.get("evidence_level") not in AUDIT_EVIDENCE_LEVELS:
            errors.append(f"{label}: invalid audit.evidence_level")
        expected_report = str(AUDITS_DIR / f"{strategy_id}.md")
        if audit.get("report") != expected_report:
            errors.append(f"{label}: audit.report must be {expected_report}")
        try:
            date.fromisoformat(str(audit.get("last_audited")))
        except ValueError:
            errors.append(f"{label}: audit.last_audited must use YYYY-MM-DD")

        if strategy.get("production_eligible"):
            if strategy.get("lifecycle_stage") != "production":
                errors.append(f"{label}: production_eligible requires lifecycle_stage=production")
            if not integration.get("paper_verified"):
                errors.append(f"{label}: production_eligible requires paper_verified=true")
            if not integration.get("production_runner"):
                errors.append(f"{label}: production_eligible requires production_runner")
            if not integration.get("order_gate"):
                errors.append(f"{label}: production_eligible requires order_gate")

        if strategy.get("work_state") in {"rejected", "retired"} and strategy.get("production_eligible"):
            errors.append(f"{label}: rejected/retired strategy cannot be production eligible")

    duplicates = sorted(strategy_id for strategy_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate strategy_id values: {', '.join(duplicates)}")

    known_ids = set(ids)
    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        parent_id = strategy.get("parent_strategy_id")
        if parent_id is not None and parent_id not in known_ids:
            errors.append(f"{strategy.get('strategy_id')}: unknown parent_strategy_id {parent_id}")

    return errors


def markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def render_portfolio(payload: dict[str, Any]) -> str:
    strategies = sorted(
        payload["strategies"],
        key=lambda item: (
            PRIORITY_ORDER[item["priority"]],
            STAGE_ORDER[item["lifecycle_stage"]],
            item["display_name"].lower(),
        ),
    )

    lines = [
        "# Strategie-Portfolio",
        "",
        f"Stand: {payload['updated_at']}",
        "",
        f"> Diese Uebersicht wird aus {TICK}registry.json{TICK} erzeugt. Sie ist beschreibend und aktiviert keine Strategie.",
        "",
        "| Prioritaet | Strategie | Reifegrad | Arbeit | Evidenz | IBKR | Produktion | Naechster Schritt |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for strategy in strategies:
        integration = strategy["ibkr_integration"]
        lines.append(
            "| {priority} | {name} | {tick}{stage}{tick} | {tick}{state}{tick} | {tick}{evidence}{tick} | "
            "{tick}{integration}{tick} | {production} | {next_action} |".format(
                priority=markdown_cell(strategy["priority"]),
                name=markdown_cell(strategy["display_name"]),
                tick=TICK,
                stage=markdown_cell(strategy["lifecycle_stage"]),
                state=markdown_cell(strategy["work_state"]),
                evidence=markdown_cell(strategy["audit"]["evidence_level"]),
                integration=markdown_cell(integration["status"]),
                production="ja" if strategy["production_eligible"] else "nein",
                next_action=markdown_cell(strategy["next_action"]),
            )
        )

    lines.extend(["", "## Detailstatus", ""])
    for strategy in strategies:
        integration = strategy["ibkr_integration"]
        lines.extend(
            [
                f"### {strategy['display_name']}",
                "",
                f"- ID: {TICK}{strategy['strategy_id']}{TICK}",
                f"- Familie: {TICK}{strategy['family']}{TICK}",
                f"- Status: {TICK}{strategy['lifecycle_stage']}{TICK} / "
                f"{TICK}{strategy['work_state']}{TICK} / {TICK}{strategy['priority']}{TICK}",
                f"- Kurzstand: {strategy['current_summary']}",
                f"- IBKR: {TICK}{integration['status']}{TICK}; "
                f"Dry-Run: {TICK}{integration['dry_run_verified']}{TICK}; "
                f"Paper: {TICK}{integration['paper_verified']}{TICK}",
                f"- Produktionsfreigabe: {TICK}{strategy['production_eligible']}{TICK}",
                f"- Audit-Evidenz: {TICK}{strategy['audit']['evidence_level']}{TICK}",
                f"- Pruefbericht: {TICK}{strategy['audit']['report']}{TICK}",
                f"- Hauptquelle: {TICK}{strategy['primary_source']}{TICK}",
                f"- Naechster Schritt: {strategy['next_action']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def command_validate() -> int:
    payload = load_registry()
    errors = validate_registry(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Registry valid: {len(payload['strategies'])} strategies")
    return 0


def command_render() -> int:
    payload = load_registry()
    errors = validate_registry(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    PORTFOLIO_PATH.write_text(render_portfolio(payload), encoding="utf-8")
    print(f"Wrote {PORTFOLIO_PATH}")
    return 0


def command_audit_check() -> int:
    payload = load_registry()
    errors = validate_registry(payload)
    required_sections = [f"## {number}." for number in range(1, 11)]
    for strategy in payload.get("strategies", []):
        strategy_id = strategy.get("strategy_id", "")
        report_path = AUDITS_DIR / f"{strategy_id}.md"
        if not report_path.exists():
            errors.append(f"{strategy_id}: missing audit report {report_path}")
            continue
        text = report_path.read_text(encoding="utf-8")
        if f"Strategy ID: {TICK}{strategy_id}{TICK}" not in text:
            errors.append(f"{strategy_id}: report does not contain the exact strategy ID")
        for section in required_sections:
            if section not in text:
                errors.append(f"{strategy_id}: report missing section {section}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Audit coverage valid: {len(payload['strategies'])} reports")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or render the descriptive strategy registry.")
    parser.add_argument("command", choices=("validate", "render", "audit-check"))
    args = parser.parse_args()
    if args.command == "validate":
        return command_validate()
    if args.command == "render":
        return command_render()
    return command_audit_check()


if __name__ == "__main__":
    raise SystemExit(main())
