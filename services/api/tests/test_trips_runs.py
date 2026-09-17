"""Trips CRUD/ETag/ownership, assessments (idempotency, state sync, cancel, needs-input/resume), SSE, apply-route."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
import respx

from tests.conftest import NOW, ROUTE_ID, Client, auth, trip_body


@pytest.mark.asyncio
@respx.mock
async def test_trip_crud_etag_and_ownership(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        trip, tag = await c.create_trip()
        assert tag == 'W/"1"' and trip["revision"] == 1 and trip["status"] == "ACTIVE"
        # validation: unconfirmed, past departure, bad timezone, null island
        assert (await h.post("/api/v1/trips", json=trip_body(confirmed=False), headers=auth())).status_code == 422
        past = trip_body(departure_time=(NOW - timedelta(days=1)).isoformat())
        r = await h.post("/api/v1/trips", json=past, headers=auth())
        assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["code"] == "IN_PAST"
        assert (await h.post("/api/v1/trips", json=trip_body(timezone="Nope/Zone"), headers=auth())).status_code == 422
        bad = trip_body()
        bad["origin"]["coordinates"]["coordinates"] = [0, 0]
        assert (await h.post("/api/v1/trips", json=bad, headers=auth())).status_code == 422
        bad["origin"]["coordinates"]["coordinates"] = [13.75, 100.5]  # lat/lon swapped => out of range lat
        assert (await h.post("/api/v1/trips", json=bad, headers=auth())).status_code == 422
        # ownership: other user gets 404, never 403 (no existence leak)
        tid = trip["id"]
        assert (await h.get(f"/api/v1/trips/{tid}", headers=auth("user-2"))).status_code == 404
        assert (
            await h.patch(
                f"/api/v1/trips/{tid}", json={"status": "ARCHIVED"}, headers={**auth("user-2"), "If-Match": tag}
            )
        ).status_code == 404
        # PATCH requires If-Match and the current revision
        assert (await h.patch(f"/api/v1/trips/{tid}", json={"status": "ARCHIVED"}, headers=auth())).status_code == 412
        r = await h.patch(f"/api/v1/trips/{tid}", json={"status": "ARCHIVED"}, headers={**auth(), "If-Match": 'W/"7"'})
        assert r.status_code == 412 and r.json()["error"]["code"] == "PRECONDITION_FAILED"
        r = await h.patch(
            f"/api/v1/trips/{tid}",
            json={"status": "ARCHIVED", "risk_acknowledged": True},
            headers={**auth(), "If-Match": tag},
        )
        assert r.status_code == 200 and r.headers["ETag"] == 'W/"2"' and r.json()["data"]["risk_acknowledged_at"]
        # stale revision after update
        assert (
            await h.patch(f"/api/v1/trips/{tid}", json={"status": "ACTIVE"}, headers={**auth(), "If-Match": tag})
        ).status_code == 412
        # list only own trips
        await c.create_trip("user-2")
        mine = (await h.get("/api/v1/trips", headers=auth())).json()["data"]
        assert [t["id"] for t in mine] == [tid]
        # soft delete reports status; trip disappears from reads
        r = await h.delete(f"/api/v1/trips/{tid}", headers=auth())
        assert r.status_code == 200 and r.json()["data"]["deletion_status"] == "SOFT_DELETED"
        assert (await h.get(f"/api/v1/trips/{tid}", headers=auth())).status_code == 404
        assert (await h.post(f"/api/v1/trips/{tid}/assessments", headers=auth())).status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_assessment_idempotency_state_sync_and_recommendation(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        trip, _ = await c.create_trip()
        tid = trip["id"]
        body = {"question": "Is my route safe tomorrow?"}
        hdr = {**auth(), "Idempotency-Key": "k-1"}
        r1 = await h.post(f"/api/v1/trips/{tid}/assessments", json=body, headers=hdr)
        assert r1.status_code == 202, r1.text
        ref = r1.json()["data"]
        assert ref["status"] == "QUEUED" and ref["events_url"] == f"/api/v1/runs/{ref['request_id']}/events"
        # same key + same payload => same run, agent called once
        r2 = await h.post(f"/api/v1/trips/{tid}/assessments", json=body, headers=hdr)
        assert r2.status_code == 202 and r2.json()["data"]["request_id"] == ref["request_id"]
        assert stubs.calls["agent.start"] == 1
        # same key + different payload => 409 IDEMPOTENCY_CONFLICT
        r3 = await h.post(f"/api/v1/trips/{tid}/assessments", json={"question": "different"}, headers=hdr)
        assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        # the agent received a pseudonymous scope, never the subject
        sent = stubs.runs[ref["request_id"]]
        assert sent["user_scope_hash"] and "user-1" not in sent["user_scope_hash"]
        # poll until terminal: request row mirrors agent state through the state machine
        st = await c.wait_run(ref["request_id"])
        assert (
            st["status"] == "COMPLETED"
            and st["recommendation_id"]
            and st["result_url"].endswith(st["recommendation_id"])
        )
        assert "risk-knowledge: model unavailable (rule baseline)" in st["degraded_services"]
        # other user cannot see the run
        assert (await h.get(f"/api/v1/runs/{ref['request_id']}", headers=auth("user-2"))).status_code == 404
        # trip carries latest ids; assessments list shows the run
        t = (await h.get(f"/api/v1/trips/{tid}", headers=auth())).json()["data"]
        assert t["latest_request_id"] == ref["request_id"] and t["latest_recommendation_id"] == st["recommendation_id"]
        runs = (await h.get(f"/api/v1/trips/{tid}/assessments", headers=auth())).json()["data"]
        assert runs[0]["status"] == "COMPLETED"
        # recommendation is validated against the contract and owner-scoped
        rec = await h.get(f"/api/v1/recommendations/{st['recommendation_id']}", headers=auth())
        assert rec.status_code == 200 and rec.json()["data"]["action_code"] == "NORMAL"
        assert rec.headers["Cache-Control"] == "private, no-store"
        assert (
            await h.get(f"/api/v1/recommendations/{st['recommendation_id']}", headers=auth("user-2"))
        ).status_code == 404
        # conversation was created with user + assistant messages
        convs = (await h.get("/api/v1/conversations", headers=auth())).json()["data"]
        assert len(convs) == 1 and convs[0]["trip_id"] == tid
        msgs = (await h.get(f"/api/v1/conversations/{convs[0]['id']}/messages", headers=auth())).json()["data"]
        assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["recommendation_id"] == st[
            "recommendation_id"
        ]


@pytest.mark.asyncio
@respx.mock
async def test_agent_down_gives_stable_error_and_failed_row(settings, stubs):
    stubs.fail["agent.start"] = httpx.Response(
        503, json={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "down"}}
    )
    async with Client(settings, stubs) as c:
        trip, _ = await c.create_trip()
        r = await c.http.post(f"/api/v1/trips/{trip['id']}/assessments", headers=auth())
        assert r.status_code == 503 and r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert r.json()["error"]["retryable"] is True and "Traceback" not in r.text
        runs = (await c.http.get(f"/api/v1/trips/{trip['id']}/assessments", headers=auth())).json()["data"]
        assert runs[0]["status"] == "FAILED"


@pytest.mark.asyncio
@respx.mock
async def test_malformed_agent_state_is_rejected(settings, stubs):
    stubs.fail["agent.state"] = httpx.Response(200, json={"data": {"status": "WHATEVER"}, "meta": {}})
    async with Client(settings, stubs) as c:
        trip, _ = await c.create_trip()
        ref = (await c.http.post(f"/api/v1/trips/{trip['id']}/assessments", headers=auth())).json()["data"]
        r = await c.http.get(f"/api/v1/runs/{ref['request_id']}", headers=auth())
        assert r.status_code == 502 and r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


@pytest.mark.asyncio
@respx.mock
async def test_sse_stream_reconnect_and_terminal_close(settings, stubs):
    async with Client(settings, stubs) as c:
        trip, _ = await c.create_trip()
        ref = (await c.http.post(f"/api/v1/trips/{trip['id']}/assessments", headers=auth())).json()["data"]
        rid = ref["request_id"]
        assert (await c.http.get(f"/api/v1/runs/{rid}/events", headers=auth("user-2"))).status_code == 404
        async with c.http.stream("GET", f"/api/v1/runs/{rid}/events", headers=auth()) as resp:
            assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"].startswith("no-cache") and resp.headers["x-accel-buffering"] == "no"
            text = "".join([chunk async for chunk in resp.aiter_text()])
        frames = [f for f in text.split("\n\n") if f.strip()]
        types = [line.split(": ", 1)[1] for f in frames for line in f.splitlines() if line.startswith("event: ")]
        assert types[0] == "run.accepted" and types[-1] == "run.completed"
        ids = [int(line.split(": ", 1)[1]) for f in frames for line in f.splitlines() if line.startswith("id: ")]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
        payloads = [json.loads(line[6:]) for f in frames for line in f.splitlines() if line.startswith("data: ")]
        completed = [p for p in payloads if p.get("event_type") == "run.completed"][0]
        assert completed["result_url"] == f"/api/v1/recommendations/{completed['recommendation_id']}"
        assert "coordinates" not in text and "100.5018" not in text
        # reconnect with Last-Event-ID: only newer events, then closes
        async with c.http.stream("GET", f"/api/v1/runs/{rid}/events", headers={**auth(), "Last-Event-ID": "2"}) as resp:
            text2 = "".join([chunk async for chunk in resp.aiter_text()])
        ids2 = [int(line.split(": ", 1)[1]) for line in text2.splitlines() if line.startswith("id: ")]
        assert ids2 == [3]


@pytest.mark.asyncio
@respx.mock
async def test_sse_heartbeat_and_connection_limit(settings, stubs):
    stubs.fail["agent.state"] = None

    async def slow_events(request):  # never terminal
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"data": [], "meta": {}})

    s = settings.model_copy(update={"max_sse_connections_per_user": 1, "sse_max_duration_seconds": 0.6})
    async with Client(s, stubs) as c:
        respx.get(url__regex=r"http://agent\.test/internal/v1/runs/[^/]+/events").mock(side_effect=slow_events)
        trip, _ = await c.create_trip()
        ref = (await c.http.post(f"/api/v1/trips/{trip['id']}/assessments", headers=auth())).json()["data"]
        rid = ref["request_id"]

        async def read():
            async with c.http.stream("GET", f"/api/v1/runs/{rid}/events", headers=auth()) as resp:
                return resp.status_code, "".join([chunk async for chunk in resp.aiter_text()])

        first = asyncio.create_task(read())
        await asyncio.sleep(0.1)
        r2 = await c.http.get(f"/api/v1/runs/{rid}/events", headers=auth())
        assert r2.status_code == 429 and r2.headers.get("Retry-After") == "5"
        status, text = await first
        assert status == 200 and "event: heartbeat" in text and "DEPENDENCY_TIMEOUT" in text
        # slot released after the stream ends
        r3 = await c.http.get(f"/api/v1/runs/{rid}", headers=auth())
        assert r3.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_cancel_and_state_machine(settings, stubs):
    async with Client(settings, stubs) as c:
        trip, _ = await c.create_trip()
        ref = (await c.http.post(f"/api/v1/trips/{trip['id']}/assessments", headers=auth())).json()["data"]
        rid = ref["request_id"]
        r = await c.http.post(f"/api/v1/runs/{rid}/cancel", json={"reason": "user_cancelled"}, headers=auth())
        assert r.status_code == 200 and r.json()["data"]["status"] == "CANCELLED"
        assert stubs.calls["agent.cancel"] == 1
        # terminal: later polls do not resurrect it and do not call the agent again
        calls = stubs.calls.get("agent.state", 0)
        st = (await c.http.get(f"/api/v1/runs/{rid}", headers=auth())).json()["data"]
        assert st["status"] == "CANCELLED" and stubs.calls.get("agent.state", 0) == calls
        assert (await c.http.post(f"/api/v1/runs/{rid}/cancel", headers=auth("user-2"))).status_code == 404
    from sta_common.errors import AppError

    from app.domain import rules

    rules.assert_transition("QUEUED", "RUNNING")
    with pytest.raises(AppError):
        rules.assert_transition("COMPLETED", "RUNNING")
    with pytest.raises(AppError):
        rules.assert_transition("NEEDS_INPUT", "RUNNING")


@pytest.mark.asyncio
@respx.mock
async def test_needs_input_then_resume(settings, stubs):
    stubs.agent_needs_input = True
    async with Client(settings, stubs) as c:
        h = c.http
        trip, tag = await c.create_trip()
        tid = trip["id"]
        ref = (await h.post(f"/api/v1/trips/{tid}/assessments", headers=auth())).json()["data"]
        st = await c.wait_run(ref["request_id"])
        assert st["status"] == "NEEDS_INPUT" and st["missing_fields"] == ["origin.confirmed_by_user"]
        # resume only from NEEDS_INPUT; new run linked to the old one
        stubs.agent_needs_input = False
        r = await h.post(f"/api/v1/runs/{ref['request_id']}/resume", headers=auth())
        assert r.status_code == 202 and r.json()["data"]["request_id"] != ref["request_id"]
        st2 = await c.wait_run(r.json()["data"]["request_id"])
        assert st2["status"] == "COMPLETED"
        assert (await h.post(f"/api/v1/runs/{ref['request_id']}/resume", headers=auth("user-2"))).status_code == 404
        r = await h.post(f"/api/v1/runs/{r.json()['data']['request_id']}/resume", headers=auth())
        assert r.status_code == 409


@pytest.mark.asyncio
@respx.mock
async def test_apply_route_creates_revision_and_reassessment(settings, stubs):
    async with Client(settings, stubs) as c:
        h = c.http
        trip, tag = await c.create_trip()
        tid = trip["id"]
        ref = (await h.post(f"/api/v1/trips/{tid}/assessments", headers=auth())).json()["data"]
        st = await c.wait_run(ref["request_id"])
        rec_id = st["recommendation_id"]
        body = {"route_id": str(ROUTE_ID), "recommendation_id": rec_id}
        assert (await h.post(f"/api/v1/trips/{tid}/apply-route", json=body, headers=auth())).status_code == 412
        bad = {**body, "route_id": "00000000-0000-0000-0000-000000000001"}
        r = await h.post(f"/api/v1/trips/{tid}/apply-route", json=bad, headers={**auth(), "If-Match": tag})
        assert r.status_code == 422
        r = await h.post(f"/api/v1/trips/{tid}/apply-route", json=body, headers={**auth(), "If-Match": tag})
        assert r.status_code == 202, r.text
        data = r.json()["data"]
        assert data["trip"]["revision"] == 2 and data["trip"]["selected_route_id"] == str(ROUTE_ID)
        assert data["run"]["request_id"] != ref["request_id"] and r.headers["ETag"] == 'W/"2"'
        # AVOID recommendation needs explicit acknowledgement
        stubs.recs[rec_id]["body"]["action_code"] = "AVOID"
        r = await h.post(f"/api/v1/trips/{tid}/apply-route", json=body, headers={**auth(), "If-Match": 'W/"2"'})
        assert r.status_code == 422 and r.json()["error"]["code"] == "POLICY_VALIDATION_FAILED"
        r = await h.post(
            f"/api/v1/trips/{tid}/apply-route",
            json={**body, "acknowledge_risk": True},
            headers={**auth(), "If-Match": 'W/"2"'},
        )
        assert r.status_code == 202 and r.json()["data"]["trip"]["risk_acknowledged_at"]
