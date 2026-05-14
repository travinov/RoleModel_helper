from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from app.config import AppConfig


class GigaChatError(RuntimeError):
    """Raised when GigaChat API request failed."""


class GigaChatClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._token_lock = threading.Lock()
        self._cached_token: Optional[str] = None
        self._cached_expires_epoch: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.config.gigachat_enabled

    def _verify_arg(self) -> bool | str:
        if self.config.gigachat_ca_bundle:
            return self.config.gigachat_ca_bundle
        return self.config.gigachat_verify_ssl

    def _basic_authorization(self) -> Optional[str]:
        if self.config.gigachat_auth_key:
            return f"Basic {self.config.gigachat_auth_key}"
        if self.config.gigachat_client_id and self.config.gigachat_client_secret:
            token = base64.b64encode(
                f"{self.config.gigachat_client_id}:{self.config.gigachat_client_secret}".encode("utf-8")
            ).decode("ascii")
            return f"Basic {token}"
        return None

    @staticmethod
    def _parse_expiration(payload: dict[str, Any]) -> float:
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, (int, float)):
            # API can return milliseconds timestamp.
            return float(expires_at) / 1000.0 if float(expires_at) > 2_000_000_000 else float(expires_at)
        if isinstance(expires_at, str):
            try:
                return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return time.time() + 1700.0

    def _request_token(self) -> str:
        if self.config.gigachat_access_token:
            return self.config.gigachat_access_token
        auth_header = self._basic_authorization()
        if not auth_header:
            raise GigaChatError("GigaChat credentials are not configured")
        try:
            response = self._session.post(
                self.config.gigachat_auth_url,
                headers={
                    "Authorization": auth_header,
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": self.config.gigachat_scope},
                timeout=self.config.gigachat_timeout_sec,
                verify=self._verify_arg(),
            )
        except requests.RequestException as exc:
            raise GigaChatError(f"GigaChat token request failed: {exc}") from exc
        if response.status_code >= 400:
            raise GigaChatError(f"GigaChat token request failed: HTTP {response.status_code} {response.text[:400]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GigaChatError("GigaChat token response is not valid JSON") from exc
        access_token = payload.get("access_token")
        if not access_token:
            raise GigaChatError("GigaChat token response does not contain access_token")
        self._cached_token = str(access_token)
        self._cached_expires_epoch = max(time.time() + 60.0, self._parse_expiration(payload) - 60.0)
        return self._cached_token

    def _ensure_token(self) -> str:
        if self.config.gigachat_access_token:
            return self.config.gigachat_access_token
        now = time.time()
        if self._cached_token and now < self._cached_expires_epoch:
            return self._cached_token
        with self._token_lock:
            now = time.time()
            if self._cached_token and now < self._cached_expires_epoch:
                return self._cached_token
            return self._request_token()

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        if not self.enabled:
            raise GigaChatError("GigaChat is disabled in configuration")
        token = self._ensure_token()
        payload: dict[str, Any] = {
            "model": model or self.config.gigachat_chat_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response = None
        retryable_statuses = {429, 500, 502, 503, 504}
        for attempt_no in range(1, 4):
            try:
                response = self._session.post(
                    f"{self.config.gigachat_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.config.gigachat_timeout_sec,
                    verify=self._verify_arg(),
                )
            except requests.RequestException as exc:
                if attempt_no >= 3:
                    raise GigaChatError(f"GigaChat completion request failed: {exc}") from exc
                time.sleep(float(attempt_no))
                continue
            if response.status_code not in retryable_statuses or attempt_no >= 3:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else float(attempt_no * 2)
            except ValueError:
                delay = float(attempt_no * 2)
            time.sleep(min(max(delay, 1.0), 8.0))
        if response is None:
            raise GigaChatError("GigaChat completion request failed without response")
        if response.status_code >= 400:
            raise GigaChatError(f"GigaChat completion request failed: HTTP {response.status_code} {response.text[:400]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GigaChatError("GigaChat completion response is not valid JSON") from exc
        choices = body.get("choices") or []
        if not choices:
            raise GigaChatError("GigaChat completion response does not contain choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise GigaChatError("GigaChat completion response does not contain textual content")
        return content.strip()

    @staticmethod
    def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char not in "{[":
                continue
            try:
                parsed, _ = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        content = self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        return self._extract_json_object(content)

    def healthcheck(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled"}
        token = self._ensure_token()
        return {
            "enabled": True,
            "status": "ok",
            "token_preview": f"{token[:12]}..." if len(token) > 12 else token,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
