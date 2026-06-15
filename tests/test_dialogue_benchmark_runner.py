from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.run_dialogue_benchmark import DialogueBenchmarkRunner


def _runner(sessions: list[dict] | None = None, **kwargs) -> DialogueBenchmarkRunner:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        handle.write(
            json.dumps(
                {
                    "suite_name": "test_suite",
                    "scoring": {
                        "target_success_rate_percent": 95,
                        "critical_failures": ["gigachat_unavailable", "fallback_without_llm", "wrong_context_state"],
                    },
                    "sessions": sessions or [],
                },
                ensure_ascii=False,
            )
        )
        path = Path(handle.name)
    return DialogueBenchmarkRunner("http://127.0.0.1:0", path, **kwargs)


class DialogueBenchmarkRunnerExpectationTestCase(unittest.TestCase):
    def test_script_path_supports_db_evidence_import_from_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "fixture.json"
            report_path = Path(temp_dir) / "report.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "suite_name": "test_suite",
                        "scoring": {"target_success_rate_percent": 95},
                        "sessions": [],
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)

            result = subprocess.run(
                [
                    sys.executable,
                    "tests/run_dialogue_benchmark.py",
                    "--fixture",
                    str(fixture_path),
                    "--report",
                    str(report_path),
                    "--db-evidence",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_selects_first_sessions_by_limit(self) -> None:
        sessions = [{"name": f"session_{idx}", "turns": []} for idx in range(1, 6)]
        runner = _runner(sessions, session_limit=2)

        selected = runner._select_sessions()

        self.assertEqual([item["name"] for item in selected], ["session_1", "session_2"])
        self.assertEqual(runner.session_selection, {"mode": "first", "limit": 2})

    def test_selects_random_sessions_with_seed(self) -> None:
        sessions = [{"name": f"session_{idx}", "turns": []} for idx in range(1, 8)]
        first_runner = _runner(sessions, random_session_limit=3, random_seed=42)
        second_runner = _runner(sessions, random_session_limit=3, random_seed=42)

        first_selection = [item["name"] for item in first_runner._select_sessions()]
        second_selection = [item["name"] for item in second_runner._select_sessions()]

        self.assertEqual(len(first_selection), 3)
        self.assertEqual(first_selection, second_selection)
        self.assertTrue(set(first_selection).issubset({item["name"] for item in sessions}))
        self.assertEqual(first_runner.session_selection, {"mode": "random", "limit": 3, "seed": 42})

    def test_evaluates_generated_fixture_structural_fields(self) -> None:
        runner = _runner()
        result = runner._evaluate_expectation(
            response={
                "intent_type": "ROLE_DISCOVERY",
                "dialog_act": "ASK_HELP",
                "assistant_text": "Нашел несколько похожих должностей.",
                "pending_question": {
                    "kind": "candidate_selection",
                    "topic": "position",
                    "options": [{"id": "1"}],
                },
                "answer": {
                    "answer_type": "ROLE_DISCOVERY",
                    "systems": [{"system_id": 1}],
                    "default_accesses": [{"role": "reader"}],
                    "request_accesses": [{"role": "writer"}],
                    "justification": "Для работы",
                },
            },
            expect={
                "intent_type": "ROLE_DISCOVERY",
                "dialog_act": "ASK_HELP",
                "pending_kind": "candidate_selection",
                "pending_options_min": 1,
                "answer_type": "ROLE_DISCOVERY",
                "systems_min": 1,
                "default_accesses_min": 1,
                "request_accesses_min": 1,
                "justification_present": True,
            },
            user_text="Какие роли?",
            evidence=None,
        )

        self.assertEqual(result.failures, [])
        self.assertEqual(result.checks_passed, result.checks_total)

    def test_evaluates_gigachat_evidence_fields(self) -> None:
        runner = _runner()
        result = runner._evaluate_expectation(
            response={
                "intent_type": "UNKNOWN",
                "dialog_act": "LLM_UNAVAILABLE",
                "assistant_text": "GigaChat временно недоступен.",
                "pending_question": None,
                "answer": None,
            },
            expect={
                "requires_gigachat": True,
                "expected_tool_success": ["gigachat_interpret_turn"],
                "forbid_reasoning_trace": ["fallback_without_llm"],
            },
            user_text="Какие роли?",
            evidence={
                "new_tool_successes": {},
                "new_reasoning_traces": ["fallback_without_llm"],
                "new_tool_errors": [],
            },
        )

        self.assertIn("gigachat_unavailable", result.critical_hits)
        self.assertIn("fallback_without_llm", result.critical_hits)
        self.assertTrue(any("expected successful tool" in failure for failure in result.failures))


if __name__ == "__main__":
    unittest.main()
