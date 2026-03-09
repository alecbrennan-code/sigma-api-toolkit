from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SigmaConfig:
    base_url: str
    client_id: str
    client_secret: str
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "SigmaConfig":
        if env_file:
            _load_dotenv(Path(env_file))
        else:
            _load_dotenv(Path(".env"))

        base_url = os.getenv("SIGMA_API_URL", "").strip().rstrip("/")
        client_id = os.getenv("SIGMA_CLIENT_ID", "").strip()
        client_secret = os.getenv("SIGMA_CLIENT_SECRET", "").strip()

        missing = [
            name
            for name, value in (
                ("SIGMA_API_URL", base_url),
                ("SIGMA_CLIENT_ID", client_id),
                ("SIGMA_CLIENT_SECRET", client_secret),
            )
            if not value
        ]
        if missing:
            raise EnvironmentError(
                f"Missing required Sigma environment variables: {', '.join(missing)}"
            )

        return cls(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
        )


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(path)

