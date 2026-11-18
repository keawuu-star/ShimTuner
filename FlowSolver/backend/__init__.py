# -*- coding: utf-8 -*-
"""FlowSolver 数值后端 (FastAPI).

把 `channel_flow` 的定常流道-压力场求解封装为 JSON 接口，供
Tk GUI / Electron 桌面端 / 迭代器调用。契约与 DeflectionSolver 对齐：

    GET  /api/health    → {status, version, numpy}
    GET  /api/config    → 默认参数 / 预设 / 调色板 / 限制
    POST /api/analyze   → {headline, charts, report, meta}

只绑定 127.0.0.1；`/api/analyze` 内部串行化，避免并发打满 CPU。
"""

from .service import BACKEND_VERSION

__all__ = ["BACKEND_VERSION"]
