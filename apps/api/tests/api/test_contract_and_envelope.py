"""响应信封、request_id 与 OpenAPI 契约锁定（spec §7 / §14.4）。"""

from __future__ import annotations

import json

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.api.app.core.middleware import LOCAL_WEB_ORIGINS, REQUEST_ID_HEADER
from apps.api.app.main import create_app
from apps.api.scripts.export_openapi import SNAPSHOT_PATH, dumps, generate_openapi


async def test_every_response_carries_request_id_header(client: AsyncClient) -> None:
    response = await client.get("/api/v1/watchlist")
    assert response.headers[REQUEST_ID_HEADER]


async def test_request_id_is_echoed_when_supplied(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/watchlist", headers={REQUEST_ID_HEADER: "caller-supplied-id"}
    )
    assert response.headers[REQUEST_ID_HEADER] == "caller-supplied-id"
    assert response.json()["request_id"] == "caller-supplied-id"


async def test_local_web_origin_can_read_api() -> None:
    """3000 端口的本地前端必须能跨 origin 调用 8000 端口的 API。"""
    app: FastAPI = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    origin = LOCAL_WEB_ORIGINS[0]
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/openapi.json", headers={"Origin": origin})
        preflight = await http.options(
            "/api/v1/watchlist",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Expose-Headers"] == REQUEST_ID_HEADER
    assert preflight.status_code == 200
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]


async def test_error_envelope_shape(client: AsyncClient) -> None:
    """错误响应恒为 {"error": {"code", "message", "request_id"}}。"""
    response = await client.get("/api/v1/stocks/999999/snapshot")

    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_unknown_route_uses_error_envelope() -> None:
    """连 404 路由不存在也走统一信封，不漏出 FastAPI 默认的 {"detail": ...}。"""
    app: FastAPI = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/api/v1/nope")

    assert response.status_code == 404
    assert "error" in response.json()
    assert "detail" not in response.json()


async def test_no_stack_trace_in_error_body(client: AsyncClient) -> None:
    """未捕获异常 ⇒ 500 但不泄漏堆栈/文件路径（spec §14.3）。"""
    response = await client.get("/api/v1/stocks/999999/snapshot")
    text = response.text
    assert "Traceback" not in text
    assert "/home/" not in text
    assert ".py" not in text


def test_openapi_snapshot_is_up_to_date() -> None:
    """CI 契约锁：未审查的契约变化必须失败（spec §7）。

    修改契约后请运行::

        python -m apps.api.scripts.export_openapi
    """
    assert SNAPSHOT_PATH.exists(), (
        f"契约快照缺失：{SNAPSHOT_PATH}。"
        f"请运行 `python -m apps.api.scripts.export_openapi` 生成。"
    )
    stored = SNAPSHOT_PATH.read_text(encoding="utf-8")
    current = dumps(generate_openapi())
    assert stored == current, (
        "OpenAPI 契约与仓库快照不一致。若这是有意的契约变更，"
        "请运行 `python -m apps.api.scripts.export_openapi` 并在 review 中审查 diff。"
    )


def test_openapi_defines_all_ten_required_dtos() -> None:
    """spec §7 明文要求这 10 个 DTO 必须出现在 OpenAPI 里。"""
    schema = generate_openapi()
    components = schema["components"]["schemas"]
    required = {
        "InstrumentDTO",
        "WatchlistItemDTO",
        "QuoteDTO",
        "DocumentDTO",
        "EvidenceDTO",
        "AnalysisDTO",
        "PredictionDTO",
        "ScorecardDTO",
        "AnalogDTO",
        "JobDTO",
    }
    missing = required - set(components)
    assert not missing, f"OpenAPI 缺少 DTO：{sorted(missing)}"


def test_openapi_declares_every_spec_route() -> None:
    schema = generate_openapi()
    paths = schema["paths"]
    expected = {
        ("/api/v1/universes/CSI300/instruments", "get"),
        ("/api/v1/instruments/search", "get"),
        ("/api/v1/watchlist", "get"),
        ("/api/v1/watchlist", "post"),
        ("/api/v1/watchlist/{symbol}", "delete"),
        ("/api/v1/watchlist/order", "patch"),
        ("/api/v1/research-pool", "get"),
        ("/api/v1/extra-watchlist", "post"),
        ("/api/v1/extra-watchlist/{symbol}", "delete"),
        ("/api/v1/quotes/latest", "get"),
        ("/api/v1/jobs/{job_id}", "get"),
        ("/api/v1/stocks/{symbol}/snapshot", "get"),
        ("/api/v1/stocks/{symbol}/quote-refresh", "post"),
        ("/api/v1/stocks/{symbol}/bars", "get"),
        ("/api/v1/stocks/{symbol}/documents", "get"),
        ("/api/v1/stocks/{symbol}/analyses", "get"),
        ("/api/v1/stocks/{symbol}/analyses/refresh", "post"),
        ("/api/v1/stocks/{symbol}/predictions/latest", "get"),
        ("/api/v1/stocks/{symbol}/predictions/history", "get"),
        ("/api/v1/models/{model_key}/scorecard", "get"),
        ("/api/v1/stocks/{symbol}/analogs", "get"),
    }
    missing = {(p, m) for p, m in expected if m not in paths.get(p, {})}
    assert not missing, f"OpenAPI 缺少路由：{sorted(missing)}"


def test_openapi_is_serializable() -> None:
    json.dumps(generate_openapi())
