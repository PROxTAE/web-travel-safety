"""Write the public OpenAPI snapshot to packages/contracts/openapi/public-api.yaml (CI checks it is in sync)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

os.environ.setdefault("APP_ENV", "test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import build, openapi_document  # noqa: E402
from app.settings import Settings  # noqa: E402

OUT = ROOT.parents[1] / "packages" / "contracts" / "openapi" / "public-api.yaml"


def render() -> str:
    doc = openapi_document(build(Settings(app_env="test")))
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is out of date; run scripts/export_openapi.py", file=sys.stderr)
            sys.exit(1)
        print("openapi snapshot in sync")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {OUT}")
