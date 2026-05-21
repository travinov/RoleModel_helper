# AGENTS Guide

## Repository Context

RoleModel Helper is a Python backend assistant for role/access lookup.

Core areas:
- `app/` - API, agent flow, services, repositories.
- `rolemodel_etl/` - workbook validation and ETL into PostgreSQL.
- `tests/` - unit/integration tests and dialogue benchmark tooling.
- `docs/` and `Doc/` - process docs, plans, source artifacts, and exports.

## SDD Rules (Spec-Driven Development)

1. Start every non-trivial feature, bug fix, behavior change, or refactor with a short written spec before implementation.
2. Keep each spec scoped to one user-visible behavior or one bounded technical change.
3. Define: goal, non-goals, inputs/outputs, constraints, and acceptance criteria.
4. Link implementation and tests back to explicit acceptance criteria.
5. Prefer updating an existing spec over creating overlapping specs.
6. If the change affects dialogue behavior, include at least one concrete user dialogue, benchmark fixture, or replay expectation.

Use `docs/specs/README.md` template for new specs.

## TDD Rules (Test-Driven Development)

1. Write or update the smallest test/check before production changes.
2. Run that exact test/check and confirm it fails for the expected reason.
3. Implement the minimal change to pass that check.
4. Run the same test/check again and confirm it passes.
5. Refactor only after passing checks, with behavior preserved.
6. Run only relevant tests for the changed scope first, then broader checks as needed.

Default full test command:

```bash
/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"
```

Relevant targeted commands:

```bash
/usr/bin/python3 -m unittest tests.test_parser_unit
/usr/bin/python3 -m unittest tests.test_parser_integration
/usr/bin/python3 -m unittest tests.test_agent_logic
/usr/bin/python3 -m unittest tests.test_dialogue_export
/usr/bin/python3 -m unittest tests.test_rag_pptx
```

Dialogue behavior can also be checked with:

```bash
/usr/bin/python3 tests/run_dialogue_benchmark.py
```

## Agent Routing

For AI-assisted work in this repository:

- Start unfamiliar repository inspection with `repo_scanner`.
- Route test scope analysis to `test_impact`.
- Route test writing and test execution to `test_runner`.
- Route standard implementation to `implementer`.
- Use `final_reviewer` only for ambiguous failures, architecture decisions, or final verification.

## Change Rules

1. Prefer small, reversible changes.
2. Avoid broad refactors unless explicitly requested.
3. Run only relevant checks.
4. Escalate uncertainty instead of guessing.
5. Summarize decisions briefly after each major step.
6. Do not modify production code or tests when a docs-only task is requested.
