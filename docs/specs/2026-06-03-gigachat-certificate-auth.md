# GigaChat Certificate Authentication

## Context
- Problem: deployment should authenticate to GigaChat with client TLS certificates instead of Basic credentials.
- Why now: production deployment will place certificate files into the project directory.
- Related files/services: `app/config.py`, `app/services/gigachat.py`, `requirements.txt`, `README.md`, `.gitignore`.

## Goal
- Primary outcome: GigaChat is enabled when client certificate and private key files are configured, and all GigaChat HTTP calls use those certificates.

## Non-Goals
- Do not replace the existing GigaChat request/response parsing.
- Do not commit private certificates or keys.

## Change Category Checklist
- [ ] ETL/workbook parsing or validation behavior
- [ ] PostgreSQL schema/data contract/query behavior
- [ ] Chat API contract
- [ ] Agent state machine/slots/phase transitions
- [ ] Static instruction upload/answer behavior
- [ ] Dialogue benchmark/replay expectations
- [x] Operational checks

## Requirements
- Functional:
  - `RM_GIGACHAT_CERT_FILE` and `RM_GIGACHAT_KEY_FILE` enable GigaChat without `RM_GIGACHAT_AUTH_KEY`.
  - If files exist under `certs/gigachat/egress_sberca.crt` and `certs/gigachat/egress_sberca.key`, they are used by default.
  - Certificate-authenticated completion calls use the `gigachat.GigaChat` SDK with IFT base URL.
- Technical constraints:
  - Keep `RM_GIGACHAT_ACCESS_TOKEN` as an explicit bypass for externally managed tokens.
  - Do not require GigaChat certificates to be present in the repository.
  - Keep the existing `requests` fallback for explicit access-token or Basic-key flows.
- Operational constraints:
  - README must state the exact deploy folder for certificates.
  - Certificate files stay untracked by git.

## Inputs / Outputs
- Inputs: client certificate PEM, client private key PEM, optional CA bundle.
- Outputs: OAuth token and chat completions using the existing response shape.
- Side effects: none beyond network calls.

## Domain Invariants
- Preserved:
  - GigaChat remains optional.
  - Intent parsing and instruction answer flags still control whether GigaChat is used.
  - Health endpoint shape remains unchanged.
- Intentionally changed:
  - Certificate pair is now a first-class GigaChat authentication method.

## Acceptance Criteria
1. Config loaded from env enables GigaChat when certificate/key paths are set.
2. Certificate-authenticated chat uses `GigaChat(cert_file=..., key_file=..., base_url=..., verify_ssl_certs=..., model=...)`.
3. Default base URL is `https://gigachat-ift.sberdevices.delta.sbrf.ru/v1`, default model is `GigaChat-2-Max`, and default SSL verification is disabled for the IFT endpoint.
4. README documents `certs/gigachat/egress_sberca.crt`, `certs/gigachat/egress_sberca.key`, and optional `certs/gigachat/ca.pem`.
5. Certificate files under `certs/gigachat` are ignored by git, except the folder placeholder.

## Test Plan (TDD)
- RED command: `/usr/bin/python3 -m unittest tests.test_gigachat_certificate_auth`
- Expected RED failure: missing certificate config fields and request cert argument.
- GREEN command: `/usr/bin/python3 -m unittest tests.test_gigachat_certificate_auth tests.test_static_instruction`
- Regression checks: `/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"`

## Rollout / Verification
- Local verification steps: targeted tests, full tests.
- Runtime/monitoring checks: place certs in `certs/gigachat`, deploy, call `/api/v1/health`, and run a GigaChat-backed chat request.
- Rollback approach: redeploy previous ZIP or set `RM_GIGACHAT_USE_FOR_INTENT=false` and `RM_GIGACHAT_USE_FOR_INSTRUCTION_ANSWER=false`.

## Change Notes
- Key decisions: use mTLS directly through `requests` rather than adding the GigaChat SDK.
- Open questions / risks: encrypted private keys are not configured in this deployment path; use an unencrypted deployment key file unless a password is added later.
