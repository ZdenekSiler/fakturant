from __future__ import annotations

import os
from pathlib import Path


def read_secret(name: str, env_fallback: str = "") -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(env_fallback or name.upper(), "")
