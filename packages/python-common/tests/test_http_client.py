import httpx
import pytest
import respx

from sta_common.errors import AppError, ErrorCode
from sta_common.http import ResilientClient


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_429_then_succeeds():
    route = respx.get("https://dep.test/x").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"ok": 1})]
    )
    c = ResilientClient(service_name="s", dependency="dep", base_url="https://dep.test", max_retries=2)
    assert await c.get_json("/x") == {"ok": 1}
    assert route.call_count == 2
    await c.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_no_retry_for_non_idempotent_post():
    route = respx.post("https://dep.test/x").mock(
        return_value=httpx.Response(503, json={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "down"}})
    )
    c = ResilientClient(service_name="s", dependency="dep", base_url="https://dep.test", max_retries=3)
    with pytest.raises(AppError) as ei:
        await c.post_json("/x", json={})
    assert ei.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert route.call_count == 1
    await c.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_post_with_idempotency_key_retries():
    route = respx.post("https://dep.test/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(201, json={"id": 1})]
    )
    c = ResilientClient(service_name="s", dependency="dep", base_url="https://dep.test", max_retries=2)
    out = await c.post_json("/x", json={}, headers={"Idempotency-Key": "k1"})
    assert out == {"id": 1}
    assert route.call_count == 2
    await c.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_timeout_maps_to_dependency_timeout():
    respx.get("https://dep.test/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    c = ResilientClient(service_name="s", dependency="dep", base_url="https://dep.test", max_retries=0)
    with pytest.raises(AppError) as ei:
        await c.get_json("/slow")
    assert ei.value.code == ErrorCode.DEPENDENCY_TIMEOUT
    await c.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_propagates_context_headers():
    route = respx.get("https://dep.test/h").mock(return_value=httpx.Response(200, json={}))
    c = ResilientClient(service_name="s", dependency="dep", base_url="https://dep.test")
    await c.get_json("/h")
    sent = route.calls[0].request.headers
    assert sent["X-Request-ID"] and sent["X-Correlation-ID"] and sent["X-Contract-Version"] == "1.0.0"
    await c.aclose()
