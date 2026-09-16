"""Copied into each image as /app/healthcheck.py; used by the Docker HEALTHCHECK."""

import os
import sys
import urllib.request

port = os.environ.get("SERVICE_PORT", "8000")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=3) as resp:  # noqa: S310
        sys.exit(0 if resp.status == 200 else 1)
except Exception:  # noqa: BLE001
    sys.exit(1)
