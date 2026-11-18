# ShimTuner 桌面版 · Deflection Studio

阀片堆叠挠度数值核验的图形化工作台。**数值后端 = Python (FastAPI)**，
**桌面前端 = Electron**，两者通过本机 HTTP + JSON 通信。

物理模型与 `deflection_checker.py`（Tk 版）完全一致：同一个
`shim_physics_core` / `shim_fem_plate` / `shim_nl_plate` / `shim_layered`
内核，同一套网格与求解参数，只是把绘图与交互搬到了 Electron。

```
┌───────────────────────────── Electron ──────────────────────────────┐
│ main.js          启动/守护 python -m backend, 申请随机空闲端口        │
│ preload.js       contextBridge: 后端地址 / 重启 / 文件对话框          │
│ renderer/        HTML + CSS + Canvas 图表 (无第三方前端依赖)          │
└───────────────▲──────────────────────────────────────────────────────┘
                │ fetch http://127.0.0.1:<port>/api/...
┌───────────────┴──────────────────────────────────────────────────────┐
│ backend/service.py   四种求解器 → 曲线 + 结构化报告 (JSON)            │
│ backend/app.py       FastAPI: /api/health /api/config /api/analyze    │
│ shim_*.py            既有物理内核 (未改动)                            │
└───────────────────────────────────────────────────────────────────────┘
```

## 快速启动

```bat
:: 在 DeflectionSolver 目录下双击, 或:
run-desktop.bat
```

脚本会复用仓库根目录的 `.venv`（没有则创建），安装
`requirements-ui.txt` 与 Electron，然后打开窗口。

手动启动：

```powershell
# 在 DeflectionSolver 目录
# 1) 安装后端依赖 (仓库根目录 .venv, 也可新建 DeflectionSolver/.venv)
..\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt

# 2) 安装前端依赖
cd desktop
npm install

# 3) 启动 (Electron 自动拉起 Python 后端)
npm start
```

单独调试后端：

```powershell
cd DeflectionSolver
..\.venv\Scripts\python.exe -m backend --port 8760
# 浏览器打开 API 文档: http://127.0.0.1:8760/docs
```

前端也支持浏览器预览（不经过 Electron）：

```
desktop/renderer/index.html?backend=http://127.0.0.1:8760
```

## 界面功能

* **阀片堆叠编辑器**：逐行编辑内径/外径/厚度，支持上移、下移、删除、
  4 组预设；第 1 行标记为「顶」（受油压片）。
* **材料与载荷**：E [GPa]、ν、p_max [MPa]。
* **四种求解器**：1D 环元 / 2D Mindlin / 2D 大挠度 vK / 2D 分层 μ=0。
  切换时自动载入建议网格（大挠度 20×24，分层 24×24）。
* **双图联动**：外缘 h(p) 曲线 + 挠度剖面 w(r)，带图例显隐、悬停十字准星
  与逐序列读数；阀片外缘/固支内缘以彩色虚线标注。
* **数值核验报告**：KPI 卡片、各层 h 与 h/t（或 w/t）适用性进度条、
  警告标签、完整求解日志、假设与备注。
* **参数变化自动重算**（可关闭）、`Ctrl+Enter` 手动运行、
  计算中计时；连点/连续编辑时自动合并请求，不堆积后端任务。
* **导入 / 导出** JSON 堆叠文件，一键复制报告。

## 后端接口

`GET /api/health` → `{status, version, numpy, solvers}`

`GET /api/config` → 求解器元数据、预设、调色板、材料默认值、网格建议与限制。

`POST /api/analyze`

```json
{
  "layers": [
    {"d_in_mm": 6, "d_out_mm": 10, "t_mm": 0.1},
    {"d_in_mm": 6, "d_out_mm": 9,  "t_mm": 0.1},
    {"d_in_mm": 6, "d_out_mm": 8,  "t_mm": 0.1}
  ],
  "material": {"E_GPa": 200.0, "nu": 0.30},
  "p_max_MPa": 1.0,
  "solver": "2d",
  "fem": {"n_radial": 60, "n_theta": 0, "clamp_radius_mm": null},
  "layered_nonlinear": false,
  "curve_points": 101
}
```

返回（节选）：

```json
{
  "solver": "2d",
  "solver_label": "2D FEM (Mindlin)",
  "headline": "3 片 · 2D Mindlin · 顶片 h = 0.04235 mm @ 1 MPa",
  "elapsed_ms": 2161.2,
  "charts": {
    "hp": {
      "title": "…", "x_label": "压强 p [MPa]", "y_label": "外缘挠度 h [mm]",
      "series": [{"id": "l1-fem", "label": "第1片 Ø10 FEM-2D",
                  "color": "#38bdf8", "dash": "solid", "width": 2.2,
                  "marker": "none", "x": [0, 0.01, "…"], "y": [0, 0.0004, "…"]}],
      "markers": [{"axis": "x", "value": 5.0, "label": "第1片外缘 Ø10",
                   "color": "#38bdf8", "style": "dash"}]
    },
    "profile": {"…": "同上结构"}
  },
  "report": {
    "kpis": [{"label": "顶片外缘挠度", "value": "0.04235 mm", "hint": "@ 1 MPa"}],
    "warnings": ["…"], "notes": ["…"],
    "lines": ["[2D FEM Mindlin] 网格 …", "…"],
    "layers": [{"index": 1, "h_mm": 0.04235, "ratio": 0.424,
                "ratio_name": "h/t", "limit": 0.2, "warn": true,
                "detail": "vs 1D +1.96e-03", "color": "#38bdf8"}]
  },
  "meta": {"mesh": {"n_nodes": 7198, "n_elem": 7080, "n_theta": 118}}
}
```

* 数值统一为 **mm / MPa**；不可用值输出 `null`（前端绘图自动断线）。
* 输入/几何错误 → `400 {"detail": "中文原因"}`；求解器异常 → `500`。
* 后端只绑定 `127.0.0.1`；`/api/analyze` 内部串行化，避免并发把 CPU 打满。

## 与 Tk 版对照

| 模式 | 后端实现 | 与 Tk 版差异 |
| --- | --- | --- |
| 1D 环元 | `ShimStackCore` (400 单元) | 无 |
| 2D Mindlin | `PolarShimFEM` | 无 |
| 2D 大挠度 | `PolarShimNLPlate`，9 点续载 | 无 |
| 2D 分层 | `LayeredStack`（线性 / vK） | 无 |

报告文字、KPI 口径、适用性阈值（h/t ≤ 0.2、w/t ≤ 3）均沿用 Tk 版；
唯一的交互差异是切换求解器会载入建议网格。

## 性能参考（本机 3 片 10/9/8，nθ 自动）

| 模式 | 网格 | 耗时 |
| --- | --- | --- |
| 1D | 401 环元 | ≈ 0.3 s |
| 2D | 60 × 118 | ≈ 2 s |
| 大挠度 | 20 × 24 | ≈ 5–9 s |
| 分层线性 | 24 × 24（4 片） | ≈ 25 s |
| 分层大挠度 | 16 × 16（3 片） | ≈ 18 s |

分层/大挠度是逐点 Newton 求解，窗口会显示「计算中 x.x s」；期间可继续编辑，
新请求会在当前求解结束后合并执行。

## 验证

```powershell
# 在 DeflectionSolver 目录: 后端数值回归 (1D vs 解析、2D vs 1D、分层结构、输入校验)
..\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Electron + 渲染进程 + 后端 端到端冒烟 (无窗口, 自动退出)
cd desktop
npm run smoke
```

## 目录

```
backend/
  __init__.py
  schemas.py        Pydantic 请求模型
  service.py        求解器封装 + JSON 报告
  app.py            FastAPI 应用
  __main__.py       python -m backend 启动器
desktop/
  main.js           Electron 主进程 (Python 发现/启动/健康检查/文件对话框)
  preload.js        最小 contextBridge
  renderer/
    index.html
    styles.css
    chart.js        Canvas 折线图 (零依赖)
    app.js          界面状态与请求编排
tests/
  test_backend_service.py
requirements-ui.txt
run-desktop.bat
```

## 打包说明

开发模式直接 `npm start`。若需要分发安装包：

1. 用 PyInstaller 把后端打成单文件：`pyinstaller -F backend/__main__.py`
   （需把 `shim_*.py` 一起打包，入口使用绝对导入）。
2. 在 `electron-builder` 的 `extraResources` 中放入该可执行文件。
3. 用 `SHIMTUNER_PYTHON` 或修改 `main.js` 的 `pythonCandidates()` 指向
   随包 Python/可执行后端。

当前 `main.js` 的解析顺序：`SHIMTUNER_PYTHON` → 仓库 `.venv` → `py -3` → `python`；
启动前会用 `import numpy, scipy, fastapi, uvicorn` 预检。

## 常见问题

* **PowerShell 禁止运行 npm.ps1**：使用 `npm.cmd`，或运行
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。
* **未找到可用 Python**：确认已安装 Python 3.10+，或在 `DeflectionSolver\.venv`
  中 `pip install -r requirements-ui.txt`。
* **分层模式很慢**：降低径向单元/周向分段（如 16 × 16），或先用 1D/2D
  模式筛方案，再用分层复核。
* **曲线看起来“只有一条”**：分层模式下各层在接触区挠度相同，只有外伸段
  会分离；可在图例中逐条显隐确认。
