import base64
import hashlib
import hmac

import pytest

from app.okx_client import OKXClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = str(payload)

    def json(self):
        return self._payload


class RecordingSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_private_get_signs_the_exact_path_and_sets_demo_header():
    session = RecordingSession([FakeResponse({"code": "0", "msg": "", "data": [{"posMode": "net_mode"}]})])
    timestamp = "2026-09-14T12:00:00.000Z"
    client = OKXClient(
        base_url="https://www.okx.com",
        api_key="key",
        secret_key="secret",
        passphrase="pass",
        demo=True,
        session=session,
        timestamp=lambda: timestamp,
    )

    data = client.get_private("/api/v5/account/positions", {"instId": "BTC-USDT-SWAP"})

    request_path = "/api/v5/account/positions?instId=BTC-USDT-SWAP"
    expected = base64.b64encode(
        hmac.new(b"secret", f"{timestamp}GET{request_path}".encode(), hashlib.sha256).digest()
    ).decode()
    call = session.calls[0]
    assert data == [{"posMode": "net_mode"}]
    assert call["url"] == "https://www.okx.com" + request_path
    assert call["headers"]["OK-ACCESS-SIGN"] == expected
    assert call["headers"]["x-simulated-trading"] == "1"
    assert call["timeout"] == 15.0


def test_public_get_retries_transient_server_failure():
    session = RecordingSession([
        FakeResponse({"code": "500", "msg": "temporary", "data": []}, status_code=500),
        FakeResponse({"code": "0", "msg": "", "data": [{"instId": "BTC-USDT-SWAP"}]}),
    ])
    sleeps = []
    client = OKXClient(session=session, sleep=sleeps.append, retry_attempts=2)

    data = client.get_public("/api/v5/public/instruments", {"instType": "SWAP"})

    assert data == [{"instId": "BTC-USDT-SWAP"}]
    assert len(session.calls) == 2
    assert sleeps == [0.5]


def test_client_rejects_non_okx_origin_before_credentials_can_be_sent():
    with pytest.raises(ValueError, match="official OKX HTTPS origin"):
        OKXClient(
            base_url="https://www.okx.com.attacker.example",
            api_key="key",
            secret_key="secret",
            passphrase="pass",
        )
