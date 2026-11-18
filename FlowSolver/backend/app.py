# -*- coding: utf-8 -*-
"""FastAPI 应用：本机数值接口（只绑定 127.0.0.1）。"""

from __future__ import annotations

import threading
import traceback

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import service
from .schemas import (AnalyzeRequest, FvCurveRequest, IterateRequest,
                      RodCurveRequest)

_SOLVE_LOCK = threading.Lock()

app = FastAPI(
    title="FlowSolver API",
    version=service.BACKEND_VERSION,
    description="定常流道-压力场求解（截流面积 + 射流效应，v1）。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request, exc):
    detail = "请求格式错误: " + "; ".join(
        f"{'/'.join(map(str, e.get('loc', [])))}: {e.get('msg', '')}"
        for e in exc.errors()[:3])
    return JSONResponse(status_code=400, content={"detail": detail})


@app.get("/api/health")
def health():
    return dict(status="ok", version=service.BACKEND_VERSION,
                numpy=np.__version__, solvers=["flow1d"])


@app.get("/api/config")
def config():
    return service.config()


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    try:
        with _SOLVE_LOCK:
            return service.analyze(req)
    except service.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:                      # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500,
                            detail=f"求解失败: {exc}") from exc


@app.post("/api/iterate")
def iterate(req: IterateRequest):
    """Q→w→P→w′ 定点迭代（调用 DeflectionSolver 的分层挠度模型）."""
    try:
        with _SOLVE_LOCK:
            return service.iterate(req)
    except service.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:                      # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500,
                            detail=f"迭代求解失败: {exc}") from exc


@app.post("/api/fv-curve")
def fv_curve(req: FvCurveRequest):
    """闭环 Q–F 曲线：逐流量点迭代（热启动）。"""
    try:
        with _SOLVE_LOCK:
            return service.fv_curve(req)
    except service.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:                      # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500,
                            detail=f"Q–F 曲线求解失败: {exc}") from exc


@app.post("/api/rod-curve")
def rod_curve(req: RodCurveRequest):
    """活塞杆速度 → 阻尼力 F(v)（压缩/回弹分支，闭环迭代）."""
    try:
        with _SOLVE_LOCK:
            return service.rod_curve(req)
    except service.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:                      # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500,
                            detail=f"杆速–阻尼力求解失败: {exc}") from exc