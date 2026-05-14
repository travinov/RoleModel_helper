from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = "http://127.0.0.1:8011"
DEFAULT_FIXTURE = Path("tests/fixtures/dialogue_benchmark_5_sessions.json")
DEFAULT_REPORT = Path("tests/fixtures/dialogue_benchmark_report.latest.json")


@dataclass
class ExpectationResult:
    checks_total: int
    checks_passed: int
    failures: list[str]
    critical_hits: list[str]


def _normalize(text: Any) -> str:
    return str(text or "").strip().lower()


def _contains_any(haystack: str, needles: list[str]) -> bool:
    hay = _normalize(haystack)
    return any(_normalize(needle) in hay for needle in needles)


def _contains_all(haystack: str, needles: list[str]) -> bool:
    hay = _normalize(haystack)
    return all(_normalize(needle) in hay for needle in needles)


def _get_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _matches_expected(actual: Any, expected: Any) -> bool:
    if expected == "__NULL__":
        return actual is None
    if expected == "__NOT_NULL__":
        return actual is not None
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


class DialogueBenchmarkRunner:
    def __init__(self, base_url: str, fixture_path: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.fixture_path = fixture_path
        self.fixture = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.target_rate = float(self.fixture.get("scoring", {}).get("target_success_rate_percent", 95))
        self.required_consecutive = int(self.fixture.get("scoring", {}).get("consecutive_sessions_required", 5))
        self.critical_rules = list(self.fixture.get("scoring", {}).get("critical_failures", []))

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        last_error: Exception | None = None
        for attempt_no in range(1, 5):
            try:
                with request.urlopen(req, timeout=90) as response:
                    return json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    raise
            except error.URLError as exc:
                last_error = exc
            if attempt_no < 4:
                time.sleep(float(attempt_no * 2))
        if last_error:
            raise last_error
        raise RuntimeError("request failed without an exception")

    def _evaluate_expectation(
        self,
        response: dict[str, Any],
        expect: dict[str, Any],
        user_text: str,
    ) -> ExpectationResult:
        failures: list[str] = []
        critical_hits: list[str] = []
        checks_total = 0
        checks_passed = 0

        assistant_text = str(response.get("assistant_text") or "")
        pending_question = response.get("pending_question") or {}
        answer = response.get("answer") or {}
        citations_count = len(answer.get("citations") or [])
        pending_topic = pending_question.get("topic")

        if "conversation_phase" in expect:
            checks_total += 1
            if response.get("conversation_phase") == expect["conversation_phase"]:
                checks_passed += 1
            else:
                failures.append(
                    f"conversation_phase expected '{expect['conversation_phase']}', got '{response.get('conversation_phase')}'"
                )
                if "missing_required_slot_sequence" in self.critical_rules:
                    critical_hits.append("missing_required_slot_sequence")

        if "pending_topic" in expect:
            checks_total += 1
            if pending_topic == expect["pending_topic"]:
                checks_passed += 1
            else:
                failures.append(f"pending_topic expected '{expect['pending_topic']}', got '{pending_topic}'")
                if "missing_required_slot_sequence" in self.critical_rules:
                    critical_hits.append("missing_required_slot_sequence")

        if "answer_type" in expect:
            checks_total += 1
            actual_answer_type = answer.get("answer_type")
            if actual_answer_type == expect["answer_type"]:
                checks_passed += 1
            else:
                failures.append(f"answer_type expected '{expect['answer_type']}', got '{actual_answer_type}'")

        if "instruction_mode_any_of" in expect:
            checks_total += 1
            actual_mode = response.get("instruction_mode")
            valid_modes = set(expect["instruction_mode_any_of"])
            if actual_mode in valid_modes:
                checks_passed += 1
            else:
                failures.append(f"instruction_mode expected any of {sorted(valid_modes)}, got '{actual_mode}'")

        if "citations_min" in expect:
            checks_total += 1
            if citations_count >= int(expect["citations_min"]):
                checks_passed += 1
            else:
                failures.append(f"citations_count expected >= {expect['citations_min']}, got {citations_count}")
                if "uncited_instruction_answer" in self.critical_rules and answer.get("answer_type") == "INSTRUCTION_LOOKUP":
                    critical_hits.append("uncited_instruction_answer")

        if "contains_any" in expect:
            checks_total += 1
            if _contains_any(assistant_text, list(expect["contains_any"])):
                checks_passed += 1
            else:
                failures.append(f"assistant_text missing any of {expect['contains_any']}")

        if "contains_all" in expect:
            checks_total += 1
            if _contains_all(assistant_text, list(expect["contains_all"])):
                checks_passed += 1
            else:
                failures.append(f"assistant_text missing all of {expect['contains_all']}")

        if "forbid_contains" in expect:
            checks_total += 1
            forbidden = [token for token in expect["forbid_contains"] if _normalize(token) in _normalize(assistant_text)]
            if not forbidden:
                checks_passed += 1
            else:
                failures.append(f"assistant_text contains forbidden tokens: {forbidden}")
                if "loop_unrecognized_confirmation" in self.critical_rules:
                    critical_hits.append("loop_unrecognized_confirmation")

        for path, expected_value in (expect.get("state_expect") or {}).items():
            checks_total += 1
            actual_value = _get_path(response, path)
            if _matches_expected(actual_value, expected_value):
                checks_passed += 1
            else:
                failures.append(f"state_expect {path} expected {expected_value!r}, got {actual_value!r}")
                if "wrong_context_state" in self.critical_rules:
                    critical_hits.append("wrong_context_state")

        for path, forbidden_value in (expect.get("state_forbid") or {}).items():
            checks_total += 1
            actual_value = _get_path(response, path)
            if not _matches_expected(actual_value, forbidden_value):
                checks_passed += 1
            else:
                failures.append(f"state_forbid {path} unexpectedly matched {forbidden_value!r}")
                if "wrong_context_state" in self.critical_rules:
                    critical_hits.append("wrong_context_state")

        if "loop_unrecognized_confirmation" in self.critical_rules:
            checks_total += 1
            loop_hit = "не удалось распознать подтверждение" in _normalize(assistant_text)
            if loop_hit:
                failures.append("assistant_text hit loop phrase 'не удалось распознать подтверждение'")
                critical_hits.append("loop_unrecognized_confirmation")
            else:
                checks_passed += 1

        return ExpectationResult(
            checks_total=checks_total,
            checks_passed=checks_passed,
            failures=failures,
            critical_hits=sorted(set(critical_hits)),
        )

    def run(self) -> dict[str, Any]:
        suite_results: list[dict[str, Any]] = []
        total_checks = 0
        total_passed = 0
        critical_summary: dict[str, int] = {}

        for session_case in self.fixture["sessions"]:
            session_resp = self._request_json("POST", "/api/v1/chat/sessions")
            session_id = session_resp["session_id"]
            turns_report: list[dict[str, Any]] = []
            session_checks = 0
            session_passed = 0
            session_critical: list[str] = []

            for idx, turn in enumerate(session_case["turns"], start=1):
                user_text = turn["user"]
                expect = turn["expect"]
                try:
                    response = self._request_json(
                        "POST",
                        f"/api/v1/chat/sessions/{session_id}/messages",
                        {"text": user_text},
                    )
                except error.HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="ignore")
                    response = {
                        "assistant_text": f"HTTP {exc.code}: {body}",
                        "conversation_phase": None,
                        "pending_question": None,
                        "answer": None,
                    }
                except Exception as exc:
                    response = {
                        "assistant_text": f"ERROR: {exc}",
                        "conversation_phase": None,
                        "pending_question": None,
                        "answer": None,
                    }

                eval_result = self._evaluate_expectation(response, expect, user_text)
                session_checks += eval_result.checks_total
                session_passed += eval_result.checks_passed
                session_critical.extend(eval_result.critical_hits)
                for hit in eval_result.critical_hits:
                    critical_summary[hit] = critical_summary.get(hit, 0) + 1

                turns_report.append(
                    {
                        "step": idx,
                        "user": user_text,
                        "assistant_text": response.get("assistant_text"),
                        "conversation_phase": response.get("conversation_phase"),
                        "pending_topic": (response.get("pending_question") or {}).get("topic"),
                        "answer_type": (response.get("answer") or {}).get("answer_type"),
                        "instruction_mode": response.get("instruction_mode"),
                        "citations_count": len(((response.get("answer") or {}).get("citations") or [])),
                        "checks_total": eval_result.checks_total,
                        "checks_passed": eval_result.checks_passed,
                        "turn_success_rate_percent": round(
                            (eval_result.checks_passed / eval_result.checks_total * 100.0) if eval_result.checks_total else 100.0,
                            2,
                        ),
                        "failures": eval_result.failures,
                        "critical_hits": eval_result.critical_hits,
                    }
                )

            total_checks += session_checks
            total_passed += session_passed
            session_rate = (session_passed / session_checks * 100.0) if session_checks else 100.0
            suite_results.append(
                {
                    "name": session_case["name"],
                    "session_id": session_id,
                    "checks_total": session_checks,
                    "checks_passed": session_passed,
                    "actual_success_rate_percent": round(session_rate, 2),
                    "target_success_rate_percent": self.target_rate,
                    "meets_target": session_rate >= self.target_rate and not session_critical,
                    "critical_hits": sorted(set(session_critical)),
                    "turns": turns_report,
                }
            )

        overall_rate = (total_passed / total_checks * 100.0) if total_checks else 100.0
        meets_per_session = [bool(item["meets_target"]) for item in suite_results]
        max_consecutive = 0
        current = 0
        for ok in meets_per_session:
            if ok:
                current += 1
                if current > max_consecutive:
                    max_consecutive = current
            else:
                current = 0

        return {
            "suite_name": self.fixture["suite_name"],
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": self.base_url,
            "fixture_path": str(self.fixture_path),
            "target_success_rate_percent": self.target_rate,
            "actual_success_rate_percent": round(overall_rate, 2),
            "checks_total": total_checks,
            "checks_passed": total_passed,
            "required_consecutive_sessions": self.required_consecutive,
            "actual_max_consecutive_sessions": max_consecutive,
            "meets_global_target": overall_rate >= self.target_rate,
            "meets_consecutive_target": max_consecutive >= self.required_consecutive,
            "critical_failures_summary": critical_summary,
            "sessions": suite_results,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run API dialogue benchmark suite and print target vs actual score.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Backend base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument(
        "--fixture",
        default=str(DEFAULT_FIXTURE),
        help=f"Path to benchmark fixture JSON (default: {DEFAULT_FIXTURE})",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help=f"Path to output report JSON (default: {DEFAULT_REPORT})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = DialogueBenchmarkRunner(args.base_url, Path(args.fixture))
    report = runner.run()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "suite_name": report["suite_name"],
                "target_success_rate_percent": report["target_success_rate_percent"],
                "actual_success_rate_percent": report["actual_success_rate_percent"],
                "required_consecutive_sessions": report["required_consecutive_sessions"],
                "actual_max_consecutive_sessions": report["actual_max_consecutive_sessions"],
                "meets_global_target": report["meets_global_target"],
                "meets_consecutive_target": report["meets_consecutive_target"],
                "report_path": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
