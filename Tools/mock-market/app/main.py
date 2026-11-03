"""MarketUS Mock 行情服务。

提供与真实行情语义一致的 REST + WebSocket 假数据：
- 无需 API key，可离线运行；
- `seed + symbol` 确定价格，便于测试与截图复现；
- 通过 `/v1/_scenario` 注入故障（超时/429/500/断连/畸形 JSON）。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "NFLX", "AVGO", "JPM", "V", "SPY", "QQQ", "DIA", "IWM",
]

SCENARIOS = {
    "default", "bull", "bear", "volatile", "stale", "disconnect",
    "slow", "error500", "rate429", "malformed", "empty",
}

DEFAULT_SEED = int(os.getenv("MOCK_SEED", "42"))
DEFAULT_SCENARIO = os.getenv("MOCK_SCENARIO", "default")

app = FastAPI(title="MarketUS Mock Market", version="1.0.0")

_state: dict[str, str] = {"scenario": DEFAULT_SCENARIO}


class ScenarioRequest(BaseModel):
    scenario: str


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _seed_for(symbol: str) -> int:
    value = DEFAULT_SEED
    for byte in symbol.encode("utf-8"):
        value = (value * 131 + byte) % (2**63 - 1)
    return value


def _base_quote(symbol: str) -> dict[str, Any]:
    rng = random.Random(_seed_for(symbol))
    base = Decimal(rng.randint(20, 500))
    change = Decimal(rng.randint(-200, 200)) / 100
    scenario = _state["scenario"]

    if scenario == "bull":
        change = abs(change)
    elif scenario == "bear":
        change = -abs(change)
    elif scenario == "volatile":
        change = change * 3

    price = (base + change).quantize(Decimal("0.01"))
    percent = (change / base * 100).quantize(Decimal("0.01"))
    return {
        "symbol": symbol,
        "price": str(price),
        "change": str(change),
        "changePercent": str(percent),
        "bid": str((price - Decimal("0.01")).quantize(Decimal("0.01"))),
        "ask": str((price + Decimal("0.01")).quantize(Decimal("0.01"))),
        "volume": rng.randint(100_000, 5_000_000),
        "ts": _now_iso(),
        "session": "regular",
        "stale": scenario == "stale",
    }


async def _apply_http_faults() -> None:
    scenario = _state["scenario"]
    if scenario == "slow":
        await asyncio.sleep(2)
    elif scenario == "error500":
        raise HTTPException(status_code=500, detail="mock server error")
    elif scenario == "rate429":
        raise HTTPException(status_code=429, detail="rate limited", headers={"Retry-After": "1"})


def _parse_symbols(raw: str) -> list[str]:
    requested = [item.strip().upper() for item in raw.split(",") if item.strip()]
    unknown = [item for item in requested if item not in SYMBOLS]
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {', '.join(unknown)}")
    return requested


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "scenario": _state["scenario"], "seed": DEFAULT_SEED}


@app.get("/v1/symbols")
async def list_symbols() -> dict[str, Any]:
    return {"symbols": SYMBOLS}


@app.get("/v1/quotes")
async def quotes(symbols: str = "AAPL,MSFT") -> Any:
    await _apply_http_faults()
    if _state["scenario"] == "empty":
        return {"quotes": []}
    requested = _parse_symbols(symbols)
    if _state["scenario"] == "malformed":
        return PlainTextResponse('{"quotes": [', media_type="application/json")
    return {"quotes": [_base_quote(symbol) for symbol in requested]}


@app.get("/v1/candles")
async def candles(symbol: str = "AAPL", interval: str = "1m", limit: int = 30) -> Any:
    await _apply_http_faults()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol}")
    if limit < 1 or limit > 390:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 390")

    rng = random.Random(_seed_for(symbol + interval))
    price = Decimal(rng.randint(20, 500))
    bars = []
    for index in range(limit):
        delta = Decimal(rng.randint(-50, 50)) / 100
        open_price = price
        close_price = max(Decimal("1.00"), open_price + delta)
        high = max(open_price, close_price) + Decimal(rng.randint(0, 30)) / 100
        low = min(open_price, close_price) - Decimal(rng.randint(0, 30)) / 100
        bars.append(
            {
                "t": f"2026-11-03T14:{index % 60:02d}:00Z",
                "o": str(open_price.quantize(Decimal("0.01"))),
                "h": str(high.quantize(Decimal("0.01"))),
                "l": str(low.quantize(Decimal("0.01"))),
                "c": str(close_price.quantize(Decimal("0.01"))),
                "v": rng.randint(1_000, 500_000),
            }
        )
        price = close_price
    return {"symbol": symbol, "interval": interval, "bars": bars}


@app.get("/v1/news")
async def news(symbol: str = "AAPL") -> dict[str, Any]:
    await _apply_http_faults()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol}")
    return {
        "symbol": symbol,
        "items": [
            {
                "id": f"{symbol}-1",
                "title": f"{symbol} 发布季度业绩，营收超出预期",
                "source": "MockWire",
                "url": "https://example.invalid/news/1",
                "publishedAt": _now_iso(),
            },
            {
                "id": f"{symbol}-2",
                "title": f"分析师上调 {symbol} 目标价",
                "source": "MockWire",
                "url": "https://example.invalid/news/2",
                "publishedAt": _now_iso(),
            },
        ],
    }


@app.post("/v1/_scenario")
async def set_scenario(request: ScenarioRequest) -> dict[str, Any]:
    if request.scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"unknown scenario: {request.scenario}")
    _state["scenario"] = request.scenario
    return {"scenario": _state["scenario"]}


@app.websocket("/v1/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    scenario = _state["scenario"]
    subscribed: set[str] = set()

    async def reader() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "code": "bad_json"})
                continue
            kind = message.get("type")
            if kind == "subscribe":
                for symbol in message.get("symbols", []):
                    ticker = str(symbol).upper()
                    if ticker in SYMBOLS:
                        subscribed.add(ticker)
            elif kind == "unsubscribe":
                for symbol in message.get("symbols", []):
                    subscribed.discard(str(symbol).upper())

    async def ticker() -> None:
        if scenario == "disconnect":
            await asyncio.sleep(2)
            await websocket.close(code=1011, reason="mock disconnect")
            return

        while True:
            await asyncio.sleep(0.25)
            if scenario == "stale":
                await websocket.send_json({"type": "heartbeat", "ts": _now_iso()})
                continue
            if scenario == "volatile":
                await websocket.send_json({"type": "heartbeat", "ts": _now_iso()})
            for symbol in list(subscribed):
                quote = _base_quote(symbol)
                await websocket.send_json(
                    {
                        "type": "tick",
                        "symbol": symbol,
                        "price": quote["price"],
                        "ts": _now_iso(),
                        "session": "regular",
                        "stale": scenario == "stale",
                    }
                )

    reader_task = asyncio.create_task(reader())
    ticker_task = asyncio.create_task(ticker())
    try:
        await asyncio.wait(
            {reader_task, ticker_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        ticker_task.cancel()
