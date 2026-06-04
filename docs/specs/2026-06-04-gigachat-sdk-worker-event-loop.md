# GigaChat SDK Worker Event Loop

## Context
- Problem: after certificate files were installed on the app server, GigaChat SDK calls still failed from FastAPI/AnyIO worker threads.
- Observed error: `There is no current event loop in thread 'AnyIO worker thread'.`
- Related files/services: `app/services/gigachat.py`, `tests/test_gigachat_certificate_auth.py`.

## Goal
- Certificate-based GigaChat calls work from request worker threads that do not already have an asyncio event loop.

## Non-Goals
- Do not change GigaChat endpoint, model, certificate paths, or fallback behavior.
- Do not replace the SDK integration with a separate HTTP implementation.

## Requirements
- Before calling the GigaChat SDK certificate path, ensure the current thread has an asyncio event loop.
- Preserve legacy access-token/basic-auth request path.
- Preserve existing error wrapping in `GigaChatError`.

## Acceptance Criteria
1. Certificate SDK calls succeed in a plain worker thread with no pre-existing event loop.
2. Existing certificate auth settings still pass through to the SDK.
3. Existing full unit test suite passes.

## Test Plan
- Targeted: `/usr/bin/python3 -m unittest tests.test_gigachat_certificate_auth`
- Regression: `/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"`
