"""Contract tests: validation rules, generated artefacts in sync, examples validate."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jsonschema import Draft202012Validator
from sta_contracts import EXPORTED_MODELS
from sta_contracts.enums import ActionCode, RiskLevel, TravelMode
from sta_contracts.geo import BBox, Point
from sta_contracts.models import DecisionValidation, LocationRef, TravelRequest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(UTC)


def _loc(**kw):
    base = dict(
        place_id="1609350",
        display_name="Bangkok, Thailand",
        coordinates=Point(coordinates=[100.5018, 13.7563]),
        country_code="TH",
        timezone="Asia/Bangkok",
        provider="open_meteo",
        confirmed_by_user=True,
    )
    base.update(kw)
    return LocationRef(**base)


def test_coordinate_order_is_lon_lat():
    with pytest.raises(ValidationError):
        Point(coordinates=[13.7563, 200.0])  # lat/lon swapped past range
    with pytest.raises(ValidationError):
        Point(coordinates=[100.0, 95.0])


def test_country_code_uppercase_iso():
    with pytest.raises(ValidationError):
        _loc(country_code="th")


def test_travel_request_rules():
    req = TravelRequest(
        request_id=uuid4(),
        trip_id=uuid4(),
        origin=_loc(),
        destination=_loc(
            place_id="1153671", display_name="Chiang Mai", coordinates=Point(coordinates=[98.9853, 18.7883])
        ),
        departure_time=NOW + timedelta(days=1),
        travel_modes=[TravelMode.TRAIN],
        timezone="Asia/Bangkok",
        question="Is this route\x00 safe?",
    )
    assert req.question == "Is this route safe?"
    with pytest.raises(ValidationError):
        TravelRequest(
            request_id=uuid4(),
            trip_id=uuid4(),
            origin=_loc(),
            destination=_loc(),
            departure_time=NOW + timedelta(days=1),
            return_time=NOW,
            travel_modes=[TravelMode.CAR],
            timezone="Asia/Bangkok",
        )
    with pytest.raises(ValidationError):
        TravelRequest(
            request_id=uuid4(),
            trip_id=uuid4(),
            origin=_loc(),
            destination=_loc(),
            departure_time=datetime(2030, 1, 1),  # naive
            travel_modes=[TravelMode.CAR],
            timezone="Asia/Bangkok",
        )
    with pytest.raises(ValidationError):
        TravelRequest(
            request_id=uuid4(),
            trip_id=uuid4(),
            origin=_loc(),
            destination=_loc(),
            departure_time=NOW,
            travel_modes=[],
            timezone="Asia/Bangkok",
        )


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        LocationRef(**{**_loc().model_dump(), "unexpected": 1})


def test_enums_are_uppercase_strings():
    assert ActionCode.AVOID.value == "AVOID"
    assert RiskLevel.UNKNOWN.value == "UNKNOWN"


def test_bbox_parse():
    b = BBox.parse("100.0,13.0,101.0,14.0")
    assert b.as_list() == [100.0, 13.0, 101.0, 14.0]
    with pytest.raises(ValueError):
        BBox.parse("1,2,3")


def test_decision_validation_wire_name_is_schema():
    v = DecisionValidation(schema=False)
    assert v.model_dump() == {
        "schema": False,
        "citations": True,
        "locked_action": True,
        "numbers": True,
        "banned_phrases": True,
        "used_fallback": False,
        "fallback_reason": None,
    }
    assert DecisionValidation.model_validate({"schema": True}).schema_valid is True


def test_generated_artifacts_in_sync(tmp_path):
    """Running the generator must not change the committed output."""
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate.py")], check=True, capture_output=True)
    # compare working tree against the index: staged regenerated output counts as in sync
    out = subprocess.run(
        ["git", "diff", "--stat", "--", "jsonschema", "generated"], cwd=ROOT, capture_output=True, text=True
    )
    assert out.stdout.strip() == "", f"generated files out of date:\n{out.stdout}"


def test_every_exported_model_has_schema_file():
    index = json.loads((ROOT / "jsonschema" / "common" / "index.json").read_text(encoding="utf-8"))
    for m in EXPORTED_MODELS:
        assert m.__name__ in index
        Draft202012Validator.check_schema(json.loads((ROOT / "jsonschema" / "common" / index[m.__name__]).read_text()))


@pytest.mark.parametrize("example", sorted((ROOT / "examples" / "real-sanitized").glob("*.json")))
def test_examples_validate(example: Path):
    payload = json.loads(example.read_text(encoding="utf-8"))
    meta = payload["_fixture"]
    for key in ("source", "captured_at", "schema_version", "redaction"):
        assert meta.get(key), f"{example.name} missing fixture metadata {key}"
    model = next(m for m in EXPORTED_MODELS if m.__name__ == meta["model"])
    model.model_validate(payload["data"])
    schema = json.loads((ROOT / "jsonschema" / "common" / "common.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator({**schema, "$ref": f"#/$defs/{meta['model']}"})
    errors = list(validator.iter_errors(json.loads(model.model_validate(payload["data"]).model_dump_json())))
    assert not errors, [e.message for e in errors]
