"""HTTP client for the Kinetic backend API."""

from __future__ import annotations

from typing import Any, Optional

import httpx

EXIT_AUTH = 3


class KineticClient:
    def __init__(self, base_url: str, password: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Kinetic-Password": self.password}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("headers", {})
        kwargs["headers"] = {**self._headers(), **kwargs["headers"]}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                return client.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            raise ConnectionError(f"Cannot reach backend at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise ConnectionError(f"Backend timed out ({self.base_url})") from exc

    def check_connection(self) -> tuple[bool, str, Optional[dict]]:
        """Returns (ok, message, readyz_payload).

        Ops connectivity only: backend reachable (/healthz) and API password valid.
        readyz_payload is fetched for status display; trading feed readiness is not required.
        """
        if not self.password:
            return False, "No password configured", None
        try:
            health = self._request("GET", "/healthz")
        except ConnectionError as exc:
            return False, str(exc), None
        if health.status_code != 200:
            return False, f"Backend returned HTTP {health.status_code}", None
        try:
            resp = self._request("GET", "/api/accounts")
        except ConnectionError as exc:
            return False, str(exc), None
        if resp.status_code == 401:
            return False, "Wrong password — check KINETIC_BACKEND_PASSWORD", None
        if resp.status_code != 200:
            return False, f"Backend returned HTTP {resp.status_code}", None
        readyz_data: Optional[dict] = None
        try:
            readyz = self._request("GET", "/readyz")
            if readyz.status_code == 200:
                readyz_data = readyz.json()
        except ConnectionError:
            pass
        return True, "connected", readyz_data

    def get_logs(self, date: str) -> list[dict]:
        resp = self._request("GET", "/api/logs", params={"date": date})
        if resp.status_code == 401:
            raise PermissionError("Unauthorized — wrong password")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def get_execution_status(self) -> dict[str, Any]:
        resp = self._request("GET", "/api/execution/status")
        if resp.status_code == 401:
            raise PermissionError("Unauthorized — wrong password")
        resp.raise_for_status()
        return resp.json()

    def get_accounts(self) -> list[dict]:
        resp = self._request("GET", "/api/accounts")
        if resp.status_code == 401:
            raise PermissionError("Unauthorized — wrong password")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def put_accounts_config(self, accounts: list[dict]) -> dict[str, Any]:
        resp = self._request(
            "PUT",
            "/api/accounts/config",
            json={"accounts": accounts},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 401:
            raise PermissionError("Unauthorized — wrong password")
        if resp.status_code == 400:
            detail = resp.json().get("detail", resp.text)
            raise ValueError(detail)
        resp.raise_for_status()
        return resp.json()
