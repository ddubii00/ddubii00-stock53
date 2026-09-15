"""Portable FastAPI entrypoint for Oracle/Linux deployments."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # This entrypoint is only used for the portable Oracle/Linux deployment.
    # Keep an explicitly supplied mode, while making a plain module launch
    # select the Oracle API routes instead of the Vercel-safe default.
    os.environ.setdefault("APP_MODE", "oracle")
    host = os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    uvicorn.run("api.index:app", host=host, port=port)


if __name__ == "__main__":
    main()
