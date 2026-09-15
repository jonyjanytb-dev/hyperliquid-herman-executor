from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlencode, urlparse

import requests


class OKXAPIError(RuntimeError):
    """An HTTP or API-level error returned by OKX."""


class OKXClient:
    """Small OKX REST v5 client with exact-path signing and bounded retries."""

    def __init__(
        self,
        base_url: str = "https://www.okx.com",
        api_key: str = "",
        secret_key: str = "",
        passphrase: str = "",
        demo: bool = False,
        timeout: float = 15.0,
        retry_attempts: int = 3,
        session=None,
        timestamp: Optional[Callable[[], str]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        parsed_url = urlparse(base_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in {"www.okx.com", "my.okx.com", "app.okx.com"}
            or parsed_url.port not in {None, 443}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("base_url must be an official OKX HTTPS origin")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.demo = demo
        self.timeout = timeout
        self.retry_attempts = max(1, retry_attempts)
        self.session = session or requests.Session()
        self._timestamp = timestamp or self._utc_timestamp
        self._sleep = sleep

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _query_path(path: str, params: Optional[dict]) -> str:
        if not params:
            return path
        clean = {key: value for key, value in params.items() if value is not None and value != ""}
        query = urlencode(clean)
        return f"{path}?{query}" if query else path

    def _private_headers(self, timestamp: str, method: str, request_path: str, body: str) -> dict:
        if not self.api_key or not self.secret_key or not self.passphrase:
            raise OKXAPIError("OKX private request requires API key, secret key and passphrase")
        prehash = f"{timestamp}{method}{request_path}{body}".encode()
        signature = base64.b64encode(
            hmac.new(self.secret_key.encode(), prehash, hashlib.sha256).digest()
        ).decode()
        headers = {
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }
        if self.demo:
            headers["x-simulated-trading"] = "1"
        return headers

    @staticmethod
    def _validate_payload(payload: dict) -> list:
        code = str(payload.get("code", ""))
        if code != "0":
            raise OKXAPIError(f"OKX API error {code}: {payload.get('msg', '')}")
        data = payload.get("data") or []
        for item in data:
            if isinstance(item, dict) and str(item.get("sCode", "0")) not in {"", "0"}:
                raise OKXAPIError(
                    f"OKX order error {item.get('sCode')}: {item.get('sMsg', '')}"
                )
        return data

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body=None,
        private: bool = False,
    ) -> list:
        method = method.upper()
        request_path = self._query_path(path, params)
        body_text = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        last_error = None

        for attempt in range(self.retry_attempts):
            timestamp = self._timestamp()
            headers = (
                self._private_headers(timestamp, method, request_path, body_text)
                if private
                else {"Content-Type": "application/json"}
            )
            try:
                response = self.session.request(
                    method=method,
                    url=self.base_url + request_path,
                    headers=headers,
                    data=body_text or None,
                    timeout=self.timeout,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise OKXAPIError(f"OKX HTTP {response.status_code}: {response.text}")
                if response.status_code >= 400:
                    raise OKXAPIError(f"OKX HTTP {response.status_code}: {response.text}")
                return self._validate_payload(response.json())
            except (requests.RequestException, ValueError, OKXAPIError) as exc:
                last_error = exc
                transient = isinstance(exc, requests.RequestException) or (
                    isinstance(exc, OKXAPIError)
                    and ("HTTP 429" in str(exc) or any(f"HTTP {code}" in str(exc) for code in range(500, 600)))
                )
                if not transient or attempt + 1 >= self.retry_attempts:
                    break
                self._sleep(0.5 * (2 ** attempt))

        if isinstance(last_error, OKXAPIError):
            raise last_error
        raise OKXAPIError(f"OKX request failed: {last_error}") from last_error

    def get_public(self, path: str, params: Optional[dict] = None) -> list:
        return self._request("GET", path, params=params, private=False)

    def get_private(self, path: str, params: Optional[dict] = None) -> list:
        return self._request("GET", path, params=params, private=True)

    def post_private(self, path: str, body) -> list:
        return self._request("POST", path, body=body, private=True)
