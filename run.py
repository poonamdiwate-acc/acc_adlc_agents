"""ADLC agent service launcher.

Loads ``.env``, validates required environment, and starts uvicorn on
``api.server:app``. Per ``ADLC_Tech_Stack_Config.json#runtime`` the server
is uvicorn + FastAPI in async mode. No orchestration — GenWiz drives the
endpoints over HTTP.

Exit codes:
    0  clean shutdown
    2  environment problem (missing required env var)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


_REQUIRED_ENV_VARS = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ADLC_API_KEY",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="adlc",
        description="Run the ADLC standalone agent service (uvicorn).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Path to a .env file (default: ./.env).",
    )
    return parser.parse_args()


def _load_env(env_path: Optional[str]) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        if env_path:
            print(
                f"WARNING: python-dotenv not installed; cannot load {env_path}",
                file=sys.stderr,
            )
        return
    if env_path:
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)


def _require_env() -> None:
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        print(
            f"ERROR: required environment variables not set: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(2)


def main() -> None:
    args = _parse_args()
    _load_env(args.env)
    _require_env()

    import uvicorn

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=(os.environ.get("LOG_LEVEL") or "info").lower(),
    )


if __name__ == "__main__":
    main()
