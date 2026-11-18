# -*- coding: utf-8 -*-
"""FastAPI 应用: 本地数值接口 (仅绑定 127.0.0.1)."""

from __future__ import annotations

import threading
import traceback

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import service
from .schemas import AnalyzeRequest

_SOLVE_LOCK = threading.Lock()

app = FastAPI(
    title="ShimTuner Deflection API",
    version=service.BACKEND_VERSION,
    description="阀片堆叠挠度/压强数值核验 (1D / 2D / 大挠度 / 分层)。",
)

# 渲染进程既可能以 file:// (origin=null) 加载, 也可能用 http://localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc or '请求'}: {err.get('msg', '格式错误')}")
    return "; ".join(parts) or "请求格式错误"


@app.exception_handler(RequestValidationError)
async def _on_validation_error(_request, exc: RequestValidationError):
    return JSONResponse(status_code=400,
                        content={"detail": _validation_message(exc)})


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": service.BACKEND_VERSION,
        "numpy": np.__version__,
        "solvers": [s["id"] for s in service.SOLVERS],
    }


@app.get("/api/config")
def config() -> dict:
    return service.get_config()


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    try:
        # 求解为重 CPU 操作: 串行化, 避免并发请求把机器拖慢
        with _SOLVE_LOCK:
            return service.analyze(req.model_dump())
    except service.AnalysisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:                     # 求解器内部错误
        traceback.print_exc()
        raise HTTPException(status_code=500,
                            detail=f"求解失败: {exc}") from None


@app.get("/")
def root() -> dict:
    return {"service": "ShimTuner Deflection API",
            "version": service.BACKEND_VERSION,
            "docs": "/docs", "health": "/api/health"}
