from __future__ import annotations

import asyncio
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.config import AppConfig
from app.services.gigachat import GigaChatClient
from rolemodel_etl.config import DBConfig


def _config(**overrides) -> AppConfig:
    values = {
        "db": DBConfig(host="localhost", port=5432, dbname="rolemodel", user="rolemodel", password="rolemodel"),
        "gigachat_cert_file": "/app/certs/gigachat/egress_sberca.crt",
        "gigachat_key_file": "/app/certs/gigachat/egress_sberca.key",
        "gigachat_base_url": "https://gigachat-ift.sberdevices.delta.sbrf.ru/v1",
        "gigachat_chat_model": "GigaChat-2-Max",
        "gigachat_verify_ssl": False,
        "gigachat_auth_key": None,
        "gigachat_client_id": None,
        "gigachat_client_secret": None,
    }
    values.update(overrides)
    return AppConfig(**values)


class DummyResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._payload


class GigaChatCertificateAuthTest(unittest.TestCase):
    def test_env_certificate_pair_enables_gigachat_without_basic_credentials(self) -> None:
        env = {
            "RM_GIGACHAT_CERT_FILE": "/deploy/certs/gigachat/egress_sberca.crt",
            "RM_GIGACHAT_KEY_FILE": "/deploy/certs/gigachat/egress_sberca.key",
        }
        with patch.dict(os.environ, env, clear=False):
            config = AppConfig.from_env()

        self.assertTrue(config.gigachat_enabled)
        self.assertEqual(config.gigachat_cert_file, "/deploy/certs/gigachat/egress_sberca.crt")
        self.assertEqual(config.gigachat_key_file, "/deploy/certs/gigachat/egress_sberca.key")
        self.assertEqual(config.gigachat_base_url, "https://gigachat-ift.sberdevices.delta.sbrf.ru/v1")
        self.assertEqual(config.gigachat_chat_model, "GigaChat-2-Max")
        self.assertFalse(config.gigachat_verify_ssl)

    def test_certificate_auth_uses_gigachat_sdk_settings(self) -> None:
        created_clients: list[dict] = []

        class FakeGigaChat:
            def __init__(self, **kwargs):
                created_clients.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def chat(self, prompt):
                self.prompt = prompt
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Ответ через сертификат"))]
                )

        client = GigaChatClient(_config())

        with patch.object(GigaChatClient, "_load_sdk_class", return_value=FakeGigaChat):
            result = client.chat_completion(
                [
                    {"role": "system", "content": "Отвечай кратко."},
                    {"role": "user", "content": "Привет"},
                ],
            )

        self.assertEqual(result, "Ответ через сертификат")
        self.assertEqual(created_clients[0]["cert_file"], "/app/certs/gigachat/egress_sberca.crt")
        self.assertEqual(created_clients[0]["key_file"], "/app/certs/gigachat/egress_sberca.key")
        self.assertEqual(created_clients[0]["base_url"], "https://gigachat-ift.sberdevices.delta.sbrf.ru/v1")
        self.assertEqual(created_clients[0]["model"], "GigaChat-2-Max")
        self.assertFalse(created_clients[0]["verify_ssl_certs"])

    def test_certificate_auth_creates_event_loop_for_worker_thread(self) -> None:
        class FakeGigaChat:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def chat(self, prompt):
                asyncio.get_event_loop()
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok from worker"))]
                )

        client = GigaChatClient(_config())
        result: list[str] = []
        errors: list[BaseException] = []

        def run_in_worker() -> None:
            try:
                with patch.object(GigaChatClient, "_load_sdk_class", return_value=FakeGigaChat):
                    result.append(client.chat_completion([{"role": "user", "content": "ping"}]))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run_in_worker)
        thread.start()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, ["ok from worker"])

    def test_access_token_without_certificates_keeps_legacy_requests_path(self) -> None:
        client = GigaChatClient(
            _config(
                gigachat_cert_file=None,
                gigachat_key_file=None,
                gigachat_access_token="managed-token",
            )
        )
        calls: list[dict] = []

        def fake_post(url, **kwargs):
            calls.append({"url": url, **kwargs})
            return DummyResponse(200, {"choices": [{"message": {"content": "ok"}}]})

        client._session.post = fake_post  # type: ignore[method-assign]

        result = client.chat_completion([{"role": "user", "content": "ping"}])

        self.assertEqual(result, "ok")
        self.assertIsNone(calls[0]["cert"])
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer managed-token")


if __name__ == "__main__":
    unittest.main()
