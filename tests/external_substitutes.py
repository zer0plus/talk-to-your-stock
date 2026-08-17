from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import Lock

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/control/alpha-vantage/{mode}")
def set_alpha_vantage_mode(mode: str) -> JSONResponse:
    if mode not in {"success", "failure"}:
        return JSONResponse(status_code=400, content={"error": "invalid mode"})
    global _alpha_vantage_mode
    with _state_lock:
        _alpha_vantage_mode = mode
    return JSONResponse(content={"mode": mode})


@app.get("/alpha-vantage")
def alpha_vantage(request: Request) -> JSONResponse:
    function = request.query_params.get("function", "")
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
    del path
    body = await request.json()
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
