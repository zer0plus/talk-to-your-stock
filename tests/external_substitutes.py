from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "comps-service"
    / "tests"
    / "fixtures"
    / "alpha_vantage"
    / "usd_company_latest.json"
)
ALPHA_VANTAGE_FIXTURE = json.loads(FIXTURE_PATH.read_text())
PROVIDER_SECRET = "deterministic-provider-secret"

app = FastAPI()
_state_lock = Lock()
_alpha_vantage_mode = "success"
_alpha_vantage_requests = 0
_alpha_vantage_requests_by_function: dict[str, int] = {}
_alpha_vantage_release = Event()
_alpha_vantage_release.set()
_gemini_requests = 0
_gemini_release = Event()
_gemini_release.set()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/control/alpha-vantage/{mode}")
def set_alpha_vantage_mode(mode: str) -> JSONResponse:
    if mode not in {"success", "failure", "blocked"}:
        return JSONResponse(status_code=400, content={"error": "invalid mode"})
    global _alpha_vantage_mode, _alpha_vantage_requests
    with _state_lock:
        _alpha_vantage_mode = mode
        _alpha_vantage_requests = 0
        _alpha_vantage_requests_by_function.clear()
        if mode == "blocked":
            _alpha_vantage_release.clear()
        else:
            _alpha_vantage_release.set()
    return JSONResponse(content={"mode": mode})


@app.get("/control/alpha-vantage/requests")
def alpha_vantage_requests() -> JSONResponse:
    with _state_lock:
        request_count = _alpha_vantage_requests
        requests_by_function = dict(_alpha_vantage_requests_by_function)
    return JSONResponse(
        content={
            "requests": request_count,
            "requests_by_function": requests_by_function,
        }
    )


@app.post("/control/release-alpha-vantage")
def release_alpha_vantage() -> JSONResponse:
    _alpha_vantage_release.set()
    return JSONResponse(content={"released": True})


@app.post("/control/gemini/{mode}")
def set_gemini_mode(mode: str) -> JSONResponse:
    if mode not in {"success", "blocked"}:
        return JSONResponse(status_code=400, content={"error": "invalid mode"})
    global _gemini_requests
    with _state_lock:
        _gemini_requests = 0
        if mode == "blocked":
            _gemini_release.clear()
        else:
            _gemini_release.set()
    return JSONResponse(content={"mode": mode})


@app.get("/control/gemini/requests")
def gemini_requests() -> JSONResponse:
    with _state_lock:
        request_count = _gemini_requests
    return JSONResponse(content={"requests": request_count})


@app.post("/control/release-gemini")
def release_gemini() -> JSONResponse:
    _gemini_release.set()
    return JSONResponse(content={"released": True})


@app.get("/alpha-vantage")
def alpha_vantage(request: Request) -> JSONResponse:
    global _alpha_vantage_requests
    function = request.query_params.get("function", "")
    with _state_lock:
        _alpha_vantage_requests += 1
        _alpha_vantage_requests_by_function[function] = (
            _alpha_vantage_requests_by_function.get(function, 0) + 1
        )
    ticker = (
        request.query_params.get("symbol")
        or request.query_params.get("keywords")
        or ""
    ).upper()

    if function == "SYMBOL_SEARCH":
        return JSONResponse(
            content={
                "bestMatches": [
                    {
                        "1. symbol": ticker,
                        "2. name": f"{ticker} Incorporated",
                        "3. type": "Equity",
                        "8. currency": "USD",
                    }
                ]
            }
        )

    with _state_lock:
        mode = _alpha_vantage_mode
    if mode == "blocked" and function == "GLOBAL_QUOTE":
        _alpha_vantage_release.wait(timeout=15)
    if mode == "failure" and function == "GLOBAL_QUOTE":
        return JSONResponse(
            status_code=429,
            content={
                "Information": (
                    "Provider quota exhausted for key " + PROVIDER_SECRET
                )
            },
        )

    payload = deepcopy(ALPHA_VANTAGE_FIXTURE[function])
    _replace_symbol(payload, ticker)
    return JSONResponse(content=payload)


@app.post("/{path:path}")
async def gemini(path: str, request: Request) -> JSONResponse:
    global _gemini_requests
    del path
    body = await request.json()
    with _state_lock:
        _gemini_requests += 1
    await asyncio.to_thread(_gemini_release.wait, 15)
    declarations = body.get("tools", [{}])[0].get("functionDeclarations", [])
    if not any(
        declaration.get("name") == "generate_comps_table"
        for declaration in declarations
    ):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Comps Tool was not exposed."}},
        )
    return JSONResponse(
        content={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "generate_comps_table",
                                    "args": {
                                        "target_ticker": "AAPL",
                                        "peer_tickers": ["MSFT", "NVDA"],
                                    },
                                }
                            }
                        ],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
            "modelVersion": "deterministic-gemini",
        }
    )


def _replace_symbol(value: object, ticker: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"01. symbol", "Symbol", "symbol"}:
                value[key] = ticker
            elif key == "Name":
                value[key] = f"{ticker} Incorporated"
            else:
                _replace_symbol(child, ticker)
    elif isinstance(value, list):
        for child in value:
            _replace_symbol(child, ticker)
