"""CLI-backed Longbridge helpers when the SDK OAuth cache is missing.

The official `longbridge` CLI (v0.27+) stores a binary session at
``~/.longbridge/openapi/cli-auth``. DSA's SDK path expects a JSON token
cache under ``tokens/<client_id>``. Until those formats match, quote /
static / kline / calc-index go through the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


def cli_bin() -> Optional[str]:
    found = shutil.which("longbridge")
    if found:
        return found
    fallback = Path("/usr/local/bin/longbridge")
    return str(fallback) if fallback.exists() else None


def cli_logged_in() -> bool:
    auth = Path.home() / ".longbridge/openapi/cli-auth"
    return bool(cli_bin() and auth.exists() and auth.stat().st_size > 0)


def run_json(args: List[str], timeout: int = 45) -> Any:
    binary = cli_bin()
    if not binary:
        raise RuntimeError("longbridge CLI not found")
    cmd = [binary, *args]
    if "--format" not in cmd:
        cmd.extend(["--format", "json"])
    env = os.environ.copy()
    path = env.get("PATH", "/usr/bin")
    if "/usr/local/bin" not in path.split(":"):
        env["PATH"] = "/usr/local/bin:" + path
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:400]
        raise RuntimeError(f"longbridge {' '.join(args)} failed: {err}")
    text = (proc.stdout or "").strip()
    if not text:
        return None
    return json.loads(text)


def first_row(payload: Any) -> Optional[dict]:
    if isinstance(payload, list) and payload:
        row = payload[0]
        return row if isinstance(row, dict) else None
    if isinstance(payload, dict):
        return payload
    return None
