"""Load backend URL and password from env, .env, or config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

TOOL_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = TOOL_ROOT / ".env"
CONFIG_FILE = TOOL_ROOT / "config.json"
DATA_DIR = Path.cwd() / "data"
LOGS_DIR = DATA_DIR / "logs"
CONFIG_ACCOUNTS_DIR = Path.cwd() / "config"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _load_config_json() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def get_backend_url() -> str:
    env_vals = _parse_env_file(ENV_FILE)
    cfg = _load_config_json()
    url = (
        os.environ.get("KINETIC_BACKEND_URL")
        or env_vals.get("KINETIC_BACKEND_URL")
        or cfg.get("backend_url")
        or "http://localhost:8000"
    )
    return url.rstrip("/")


def get_backend_password() -> str:
    env_vals = _parse_env_file(ENV_FILE)
    cfg = _load_config_json()
    return (
        os.environ.get("KINETIC_BACKEND_PASSWORD")
        or env_vals.get("KINETIC_BACKEND_PASSWORD")
        or cfg.get("password")
        or ""
    )


def backend_host_label(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname
    return url


def ensure_dirs() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)


def save_env_password(password: str) -> None:
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith("KINETIC_BACKEND_PASSWORD="):
            out.append(f"KINETIC_BACKEND_PASSWORD={password}")
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(f"KINETIC_BACKEND_PASSWORD={password}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def save_config(backend_url: Optional[str] = None, password: Optional[str] = None) -> None:
    cfg = _load_config_json()
    if backend_url is not None:
        cfg["backend_url"] = backend_url.rstrip("/")
    if password is not None:
        cfg["password"] = password
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
