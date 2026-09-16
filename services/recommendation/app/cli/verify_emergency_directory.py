"""Verify the emergency directory: schema, dates, reachability of source pages, review status summary.

Exit code 0 when every VERIFIED record has a reachable source (HTTP < 400) and no review is overdue.
Records whose source blocks automated fetching are reported for manual confirmation (kept PENDING).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import httpx

from app.directory.resolver import Directory
from app.settings import get_settings


def main() -> None:
    s = get_settings()
    d = Directory.load(s.emergency_directory_path)
    today = datetime.now(UTC).date()
    report: dict[str, object] = {
        "directory_version": d.directory_version,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    rows = []
    failures = 0
    headers = {"User-Agent": "Mozilla/5.0 (smart-travel-assistant directory verifier)"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as c:
        for r in d.records:
            try:
                resp = c.get(str(r.source_url))
                reachable = resp.status_code < 400
                status_code: object = resp.status_code
            except Exception as exc:  # noqa: BLE001
                reachable, status_code = False, type(exc).__name__
            overdue = r.review_due_at < today
            row: dict[str, object] = {
                "country": r.country_code,
                "service": r.service_type,
                "phone": r.phone,
                "review_status": r.review_status,
                "source": str(r.source_url),
                "http": status_code,
                "reachable": reachable,
                "review_overdue": overdue,
                "checksum": r.checksum[:12],
            }
            if r.review_status == "VERIFIED" and (not reachable or overdue):
                failures += 1
                row["problem"] = "unreachable source" if not reachable else "review overdue"
            rows.append(row)
    report["records"] = rows
    report["verified"] = sum(1 for r in d.records if r.review_status == "VERIFIED")
    report["pending"] = sum(1 for r in d.records if r.review_status != "VERIFIED")
    report["failures"] = failures
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
