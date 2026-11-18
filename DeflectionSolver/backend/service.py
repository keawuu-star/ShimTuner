# -*- coding: utf-8 -*-
"""数值后端服务
================
把 ``deflection_checker.py`` 中的四种求解器 (1D 环元 / 2D Mindlin /
2D 大挠度 vK / 2D 分层接触) 封装成纯函数, 输出可直接 JSON 序列化的
曲线 + 结构化报告, 供 Electron 前端绘图。

同一份物理内核, 不做任何模型改动; 数值与 Tk 版逐项一致。
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Any, Mapping, Sequence

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from shim_physics_core import (ShimLayer, ShimStackCore,
                               analytic_edge_deflection, analytic_phi,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)
from shim_fem_plate import PolarShimFEM, auto_n_theta
from shim_nl_plate import PolarShimNLPlate
from shim_layered import LayeredStack

BACKEND_VERSION = "1.0.0"

N_ELEM_BASE = 400          # 1D 环元径向单元数 (与 Tk 版一致)
NL_MAX_NTHETA = 48         # 大挠度/分层自动 nθ 上限
RATIO_LINEAR = 0.2         # 线性小挠度适用 h/t 上限
RATIO_NL = 3.0             # von Kármán 适用 w/t 上限

LAYER_COLORS = ["#38bdf8", "#fbbf24", "#34d399", "#f472b6",
                "#a78bfa", "#fb7185", "#22d3ee", "#eab308",
                "#4ade80", "#f97316", "#818cf8", "#facc15"]
REF_COLOR = "#94a3b8"
ANALYTIC_COLOR = "#cbd5e1"
CLAMP_COLOR = "#f87171"

SOLVERS = (
    {"id": "1d", "label": "1D 环元", "badge": "v0",
     "description": "轴对称环元 (w, θ), 阶梯刚度 ΣD; 秒级, 适合快速核验。",
     "recommended_fem": None},
    {"id": "2d", "label": "2D FEM", "badge": "Mindlin",
     "description": "极坐标 8 节点 Mindlin 板, 含横向剪切; h(p) 线性放大。",
     "recommended_fem": {"n_radial": 60, "n_theta": 0}},
    {"id": "nl", "label": "2D 大挠度", "badge": "vK",
     "description": "von Kármán 膜-弯耦合, 逐点 Newton 续载; 含膜拉伸刚化。",
     "recommended_fem": {"n_radial": 20, "n_theta": 24}},
    {"id": "layered", "label": "2D 分层", "badge": "μ=0",
     "description": "逐层中面 + 法向罚接触, 允许层间分离; 较慢。",
     "recommended_fem": {"n_radial": 24, "n_theta": 24}},
)

PRESETS = (
    {"id": "single", "label": "单片 Ø10",
     "layers": [{"d_in_mm": 6, "d_out_mm": 10, "t_mm": 0.1}]},
    {"id": "taper", "label": "锥形 10/9/8",
     "layers": [{"d_in_mm": 6, "d_out_mm": 10, "t_mm": 0.1},
                {"d_in_mm": 6, "d_out_mm": 9, "t_mm": 0.1},
                {"d_in_mm": 6, "d_out_mm": 8, "t_mm": 0.1}]},
    {"id": "equal3", "label": "等径 ×3",
     "layers": [{"d_in_mm": 6, "d_out_mm": 10, "t_mm": 0.1}] * 3},
    {"id": "taper4", "label": "锥形 22/20/18/16",
     "layers": [{"d_in_mm": 8, "d_out_mm": 22, "t_mm": 0.2},
                {"d_in_mm": 8, "d_out_mm": 20, "t_mm": 0.2},
                {"d_in_mm": 8, "d_out_mm": 18, "t_mm": 0.2},
                {"d_in_mm": 8, "d_out_mm": 16, "t_mm": 0.2}]},
)


class AnalysisError(ValueError):
    """输入/几何不合法 -> HTTP 400."""


# ============================================================
# 序列化辅助
# ============================================================
def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _fmt(value: float | None, digits: int = 5) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _series(series_id: str, label: str, x, y, color: str, *,
            dash: str = "solid", width: float = 2.0,
            marker: str = "none") -> dict:
    return {
        "id": series_id,
        "label": label,
        "color": color,
        "dash": dash,
        "width": float(width),
        "marker": marker,
        "x": [_num(v) for v in np.asarray(x, dtype=float).ravel()],
        "y": [_num(v) for v in np.asarray(y, dtype=float).ravel()],
    }


def _marker(value: float, label: str, color: str,
            style: str = "dash") -> dict:
    return {"axis": "x", "value": _num(value), "label": label,
            "color": color, "style": style}


def _chart(chart_id: str, title: str, x_label: str, y_label: str,
           series: Sequence[dict], markers: Sequence[dict] = ()) -> dict:
    return {"id": chart_id, "title": title, "x_label": x_label,
            "y_label": y_label, "series": list(series),
            "markers": list(markers)}


def _kpi(label: str, value: str, hint: str = "") -> dict:
    return {"label": label, "value": value, "hint": hint}


def _layer_color(i: int) -> str:
    return LAYER_COLORS[i % len(LAYER_COLORS)]


def _edge_markers(layers: Sequence[ShimLayer], clamp_mm: float) -> list[dict]:
    out = [_marker(l.b_m * 1e3, f"第{i + 1}片外缘 Ø{l.d_out_mm:g}",
                   _layer_color(i)) for i, l in enumerate(layers)]
    out.append(_marker(clamp_mm, "固支内缘", CLAMP_COLOR, style="dash"))
    return out


def _layer_series(i: int, layer: ShimLayer, x, y, *, dash: str = "solid",
                  width: float = 2.0, marker: str = "none",
                  suffix: str = "", series_id: str | None = None,
                  label_prefix: str | None = None) -> dict:
    label = label_prefix if label_prefix is not None else f"第{i + 1}片"
    return _series(series_id or f"l{i + 1}",
                   f"{label} Ø{layer.d_out_mm:g}{suffix}",
                   x, y, _layer_color(i), dash=dash, width=width,
                   marker=marker)


def _analytic_series(layers: Sequence[ShimLayer], p_max: float, E: float,
                     nu: float) -> tuple[dict, float]:
    top = layers[0]
    h = analytic_edge_deflection(top.d_in_mm, top.d_out_mm, top.t_mm,
                                 p_max, E, nu)
    ser = _series("analytic",
                  f"单片参照(解析) Ø{top.d_out_mm:g}/Ø{top.d_in_mm:g}",
                  [0.0, p_max / 1e6], [0.0, h * 1e3],
                  ANALYTIC_COLOR, dash="dot", width=1.5)
    return ser, h


def _layer_rows(layers: Sequence[ShimLayer], tips_m: Sequence[float], *,
                ratio_name: str, limit: float,
                extra: Sequence[str] | None = None) -> tuple[list[dict], float]:
    rows: list[dict] = []
    ratio_max = 0.0
    for i, (layer, tip) in enumerate(zip(layers, tips_m)):
        h_mm = abs(float(tip)) * 1e3
        ratio = abs(float(tip)) / (layer.t_mm * 1e-3)
        ratio_max = max(ratio_max, ratio)
        rows.append({
            "index": i + 1,
            "color": _layer_color(i),
            "d_in_mm": layer.d_in_mm,
            "d_out_mm": layer.d_out_mm,
            "t_mm": layer.t_mm,
            "h_mm": h_mm,
            "ratio": ratio,
            "ratio_name": ratio_name,
            "limit": limit,
            "warn": ratio > limit,
            "detail": extra[i] if extra and i < len(extra) else "",
        })
    return rows, ratio_max


def _report(layers: Sequence[ShimLayer], rows: list[dict],
            warnings: Sequence[str], lines: Sequence[str],
            kpis: Sequence[dict], notes: Sequence[str]) -> dict:
    return {"kpis": list(kpis), "lines": list(lines),
            "warnings": list(warnings), "notes": list(notes),
            "layers": rows,
            "layer_count": len(layers)}


def get_config() -> dict:
    return {
        "version": BACKEND_VERSION,
        "solvers": list(SOLVERS),
        "presets": list(PRESETS),
        "palette": LAYER_COLORS,
        "material_defaults": {"E_GPa": STEEL_7C27MO2_E / 1e9,
                              "nu": STEEL_7C27MO2_NU},
        "defaults": {"p_max_MPa": 1.0, "solver": "1d",
                     "fem": {"n_radial": 60, "n_theta": 0,
                             "clamp_radius_mm": None},
                     "layered_nonlinear": False},
        "limits": {"layers_max": 12, "p_max_MPa": 200.0,
                   "n_radial": [4, 200], "n_theta": [0, 256],
                   "ratio_linear": RATIO_LINEAR, "ratio_nl": RATIO_NL,
                   "curve_points": [11, 401]},
        "assumptions": [
            "阀片自上而下排列, 第 1 片顶面受均布油压, 其余层经接触传载。",
            "内孔被压紧螺栓夹持 (w=0, θ=0), 外缘自由。",
            "1D/2D 模式假设层间全程粘合 (ΣD), 分层模式允许分离。",
        ],
    }


# ============================================================
# 输入解析
# ============================================================
def _float(value: Any, name: str, default: float | None = None) -> float:
    if value is None or value == "":
        if default is None:
            raise AnalysisError(f"缺少参数: {name}")
        return float(default)
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise AnalysisError(f"{name} 必须是数字") from None
    if not math.isfinite(out):
        raise AnalysisError(f"{name} 必须是有限数值")
    return out


def _parse_request(req: Mapping[str, Any]) -> dict:
    raw_layers = req.get("layers") or []
    if not isinstance(raw_layers, (list, tuple)) or not raw_layers:
        raise AnalysisError("请至少添加一片阀片")
    if len(raw_layers) > 12:
        raise AnalysisError("阀片数量上限为 12 片")

    layers: list[ShimLayer] = []
    for i, raw in enumerate(raw_layers, 1):
        if not isinstance(raw, Mapping):
            raise AnalysisError(f"第 {i} 片参数格式错误")
        din = _float(raw.get("d_in_mm"), f"第 {i} 片内径")
        dout = _float(raw.get("d_out_mm"), f"第 {i} 片外径")
        t = _float(raw.get("t_mm"), f"第 {i} 片厚度")
        if din <= 0.0 or dout <= 0.0 or t <= 0.0:
            raise AnalysisError(f"第 {i} 片: 内径/外径/厚度必须 > 0 mm")
        if dout <= din:
            raise AnalysisError(
                f"第 {i} 片: 外径 ({dout:g} mm) 必须大于内径 ({din:g} mm)")
        layers.append(ShimLayer(din, dout, t))

    material = req.get("material") or {}
    if not isinstance(material, Mapping):
        raise AnalysisError("material 参数格式错误")
    e_gpa = _float(material.get("E_GPa"), "弹性模量 E", STEEL_7C27MO2_E / 1e9)
    if e_gpa <= 0.0:
        raise AnalysisError("弹性模量 E 必须 > 0 GPa")
    nu = _float(material.get("nu"), "泊松比 ν", STEEL_7C27MO2_NU)
    if not 0.0 <= nu < 0.5:
        raise AnalysisError("泊松比 ν 必须满足 0 ≤ ν < 0.5")

    p_mpa = _float(req.get("p_max_MPa"), "p_max", 1.0)
    if p_mpa <= 0.0:
        raise AnalysisError("p_max 必须 > 0 MPa")

    solver = str(req.get("solver") or "1d").lower()
    if solver not in {s["id"] for s in SOLVERS}:
        raise AnalysisError(f"未知求解器: {solver}")

    fem = req.get("fem") or {}
    if not isinstance(fem, Mapping):
        raise AnalysisError("fem 参数格式错误")
    n_radial = int(round(_float(fem.get("n_radial"), "径向单元数", 60)))
    n_theta = int(round(_float(fem.get("n_theta"), "周向分段数", 0)))
    if not 4 <= n_radial <= 200:
        raise AnalysisError("径向单元数应在 4…200 之间")
    if n_theta != 0 and not 3 <= n_theta <= 256:
        raise AnalysisError("周向分段数应为 0 (自动) 或 3…256")

    clamp_raw = fem.get("clamp_radius_mm")
    clamp_mm: float | None = None
    if clamp_raw not in (None, ""):
        clamp_mm = _float(clamp_raw, "夹持半径")
        if clamp_mm <= 0.0:
            raise AnalysisError("夹持半径必须 > 0 mm")
        min_rin = min(l.d_in_mm for l in layers) / 2.0
        min_rout = min(l.d_out_mm for l in layers) / 2.0
        if clamp_mm < min_rin - 1e-9:
            raise AnalysisError(
                f"夹持半径 {clamp_mm:g} mm 小于最小内半径 {min_rin:g} mm, "
                f"会形成无支撑悬环")
        if clamp_mm >= min_rout - 1e-9:
            raise AnalysisError(
                f"夹持半径 {clamp_mm:g} mm 必须小于最小外半径 {min_rout:g} mm")

    n_points = int(round(_float(req.get("curve_points"), "曲线点数", 101)))
    if not 11 <= n_points <= 401:
        raise AnalysisError("曲线点数应在 11…401 之间")

    return {
        "layers": layers,
        "E": e_gpa * 1e9,
        "nu": nu,
        "p_max": p_mpa * 1e6,
        "solver": solver,
        "n_radial": n_radial,
        "n_theta": n_theta,
        "clamp_mm": clamp_mm,
        "layered_nonlinear": bool(req.get("layered_nonlinear", False)),
        "n_points": n_points,
    }


def _auto_n_theta(layers: Sequence[ShimLayer], n_radial: int,
                  clamp_mm: float | None) -> int:
    clamp = clamp_mm if clamp_mm is not None else min(l.d_in_mm for l in layers) / 2.0
    b_max = max(l.b_m for l in layers)
    return int(min(auto_n_theta(b_max, (b_max - clamp * 1e-3) / n_radial),
                   NL_MAX_NTHETA))


# ============================================================
# 1D 环元
# ============================================================
def _run_1d(layers, E, nu, p_max, clamp_mm, n_points, _cfg) -> dict:
    core = ShimStackCore(layers, E=E, nu=nu, n_elem_base=N_ELEM_BASE,
                         clamp_radius_mm=clamp_mm)
    p_mpa = p_max / 1e6
    ps = np.linspace(0.0, p_max, n_points)
    tips = core.edge_deflection_curve(ps)                 # (nl, np)
    top = layers[0]
    an_ser, h_an = _analytic_series(layers, p_max, E, nu)

    hp_series = [an_ser]
    for i, (tip, layer) in enumerate(zip(tips, layers)):
        hp_series.append(_layer_series(i, layer, ps / 1e6, tip * 1e3))

    r_prof, w_prof = core.profile_at(p_max)
    profile = _chart(
        "profile", f"挠度剖面 w(r) @ p = {p_mpa:g} MPa · 1D 环元",
        "半径 r [mm]", "挠度 w [mm]",
        [_series("profile", "w(r) · 1D 环元", r_prof * 1e3,
                 w_prof * 1e3, "#e2e8f0", width=2.0)],
        _edge_markers(layers, core.clamp_radius_m * 1e3))

    rows, ratio_max = _layer_rows(layers, tips[:, -1],
                                  ratio_name="h/t", limit=RATIO_LINEAR)
    h_top = float(tips[0, -1])
    gain = h_an / h_top if h_top > 0 else None
    warnings = []
    if ratio_max > RATIO_LINEAR:
        warnings.append(f"最大 h/t = {ratio_max:.2f} > {RATIO_LINEAR:g}: "
                        f"超出线性小挠度适用区, 曲线仅供趋势参考")

    lines = [
        f"FEM 网格: {core.n_nodes} 节点, {core.n_nodes - 1} 单元, "
        f"固支半径 {core.clamp_radius_m * 1e3:.3f} mm",
        f"顶片单片解析 (md): φ = "
        f"{analytic_phi(top.d_in_mm, top.d_out_mm, nu):.6g}, "
        f"h = {h_an * 1e3:.5f} mm @ {p_mpa:g} MPa",
        f"堆叠顶片 FEM: h = {h_top * 1e3:.5f} mm"
        + (f"  (FEM/解析 = {h_top / h_an:.6f})" if len(layers) == 1 else ""),
        f"堆叠刚度提升 (相对单片): "
        + (f"{gain:.3f} ×" if gain else "—"),
    ]
    for i, (layer, tip) in enumerate(zip(layers, tips[:, -1])):
        ht = abs(float(tip)) / (layer.t_mm * 1e-3)
        mark = "  ← 超出 h<0.2t!" if ht > RATIO_LINEAR else ""
        lines.append(f"  第{i + 1}片 Ø{layer.d_out_mm:g}: "
                     f"h = {float(tip) * 1e3:.5f} mm, "
                     f"h/t = {ht:.3f}{mark}")
    if ratio_max > RATIO_LINEAR:
        lines.append(f"[警告] 最大 h/t = {ratio_max:.2f} > 0.2: "
                     f"超出线性小挠度适用区, 曲线仅供趋势参考")

    report = _report(
        layers, rows, warnings, lines,
        kpis=[
            _kpi("顶片外缘挠度", f"{h_top * 1e3:.5f} mm", f"@ {p_mpa:g} MPa"),
            _kpi("刚度提升", f"{gain:.2f} ×" if gain else "—",
                 "相对单片解析"),
            _kpi("最大 h/t", f"{ratio_max:.3f}",
                 f"线性适用 ≤ {RATIO_LINEAR:g}"),
            _kpi("网格规模", f"{core.n_nodes} 节点",
                 f"{core.n_nodes - 1} 环元"),
        ],
        notes=["小挠度线性: 适用 h ≪ t; 超出时结果仅供趋势参考。",
               "单片 FEM/解析 验证见 shim_physics_core.py 自检 "
               "(相对误差 < 1e-5)。"])

    return {
        "solver": "1d", "solver_label": "1D 环元",
        "headline": f"{len(layers)} 片 · 1D 环元 · "
                    f"顶片 h = {h_top * 1e3:.5f} mm @ {p_mpa:g} MPa",
        "charts": {
            "hp": _chart("hp", "阀片外缘 挠度-压强 h(p) · 1D 环元",
                         "压强 p [MPa]", "外缘挠度 h [mm]", hp_series),
            "profile": profile,
        },
        "report": report,
        "meta": {"mesh": {"n_nodes": core.n_nodes,
                          "n_elem": core.n_nodes - 1,
                          "n_theta": 1,
                          "clamp_radius_mm": core.clamp_radius_m * 1e3},
                 "timings": {}},
    }


# ============================================================
# 2D Mindlin
# ============================================================
def _run_2d(layers, E, nu, p_max, clamp_mm, n_points, cfg) -> dict:
    fem = PolarShimFEM(layers, E=E, nu=nu,
                       n_elem_base_radial=cfg["n_radial"],
                       n_theta=cfg["n_theta"], clamp_radius_mm=clamp_mm)
    core = ShimStackCore(layers, E=E, nu=nu, n_elem_base=N_ELEM_BASE,
                         clamp_radius_mm=clamp_mm)

    p_mpa = p_max / 1e6
    ps = np.linspace(0.0, p_max, n_points)
    tips_f = fem.tip_deflections_unit()[:, None] * ps[None, :]   # (nl, np)
    tips_1 = core.edge_deflection_curve(ps)
    top = layers[0]
    an_ser, h_an = _analytic_series(layers, p_max, E, nu)

    hp_series = [an_ser]
    for i, layer in enumerate(layers):
        hp_series.append(_layer_series(
            i, layer, ps / 1e6, tips_f[i] * 1e3, width=2.2,
            suffix=" FEM-2D", series_id=f"l{i + 1}-fem"))
        hp_series.append(_layer_series(
            i, layer, ps / 1e6, tips_1[i] * 1e3, dash="dash", width=1.1,
            suffix=" 1D", series_id=f"l{i + 1}-1d"))

    r_f, wf_u = fem.profile_unit_w()
    r_1, w1 = core.profile_at(p_max)
    profile = _chart(
        "profile",
        f"挠度剖面 w(r) @ p = {p_mpa:g} MPa · 环向平均 (FEM 实线 / 1D 虚线)",
        "半径 r [mm]", "挠度 w [mm]",
        [_series("profile-fem", "w(r) FEM-2D", r_f * 1e3,
                 wf_u * p_max * 1e3, "#e2e8f0", width=2.0),
         _series("profile-1d", "w(r) 1D v0", r_1 * 1e3,
                 w1 * 1e3, REF_COLOR, dash="dash", width=1.2)],
        _edge_markers(layers, fem.mesh["clamp_radius_mm"]))

    rel_1d = [(tips_f[i, -1] - tips_1[i, -1]) / tips_1[i, -1]
              for i in range(len(layers))]
    rows, ratio_max = _layer_rows(
        layers, tips_f[:, -1], ratio_name="h/t", limit=RATIO_LINEAR,
        extra=[f"vs 1D {r:+.2e}" for r in rel_1d])
    warnings = []
    if ratio_max > RATIO_LINEAR:
        warnings.append(f"最大 h/t = {ratio_max:.2f} > {RATIO_LINEAR:g}: "
                        f"超出线性小挠度适用区, 曲线仅供趋势参考")

    area = np.pi * (top.b_m ** 2 - top.a_m ** 2)
    rel_en = abs(fem.E_int - fem.E_ext) / max(abs(fem.E_ext), 1e-300)
    rel_face = abs(fem.area_face - area) / max(area, 1e-300)
    t_wall = fem.t_assemble + fem.t_solve

    lines = [
        f"[2D FEM Mindlin] 网格 {fem.mesh['n_cell_r']} 径向带 × "
        f"nθ={fem.mesh['n_theta']} → {fem.mesh['n_elem']} 单元, "
        f"{fem.n_nodes} 节点, 自由 DOF {int(fem.free.sum())}",
        f"组装 {fem.t_assemble * 1e3:.0f} ms + 求解 "
        f"{fem.t_solve * 1e3:.0f} ms (共 {t_wall * 1e3:.0f} ms; "
        f"单位压强解, h(p) 线性放大)",
        f"单片解析参照 (md): φ = "
        f"{analytic_phi(top.d_in_mm, top.d_out_mm, nu):.6g}, "
        f"h = {h_an * 1e3:.5f} mm @ {p_mpa:g} MPa",
        f"顶片外缘 @ {p_mpa:g} MPa: FEM-2D h = {tips_f[0, -1] * 1e3:.5f} mm "
        f"| 1D-v0 h = {tips_1[0, -1] * 1e3:.5f} mm | 相对差 "
        f"{rel_1d[0]:+.2e} (FEM 含 Mindlin 剪切, 略软)",
        f"守恒: 环向均匀性 {fem.ring_uniformity():.1e} | "
        f"能量 {rel_en:.1e} | 总压力 {rel_face:.1e}",
    ]
    for i, layer in enumerate(layers):
        ht = abs(tips_f[i, -1]) / (layer.t_mm * 1e-3)
        mark = "  ← 超出 h<0.2t!" if ht > RATIO_LINEAR else ""
        lines.append(f"  第{i + 1}片 Ø{layer.d_out_mm:g}: "
                     f"h = {tips_f[i, -1] * 1e3:.5f} mm, h/t = {ht:.3f}, "
                     f"vs-1D {rel_1d[i]:+.2e}{mark}")
    if ratio_max > RATIO_LINEAR:
        lines.append(f"[警告] 最大 h/t = {ratio_max:.2f} > 0.2: "
                     f"超出线性小挠度适用区, 曲线仅供趋势参考")

    report = _report(
        layers, rows, warnings, lines,
        kpis=[
            _kpi("顶片外缘挠度", f"{tips_f[0, -1] * 1e3:.5f} mm",
                 f"FEM-2D @ {p_mpa:g} MPa"),
            _kpi("相对 1D 偏差", f"{rel_1d[0]:+.2%}", "Mindlin 剪切修正"),
            _kpi("最大 h/t", f"{ratio_max:.3f}",
                 f"线性适用 ≤ {RATIO_LINEAR:g}"),
            _kpi("耗时", f"{t_wall * 1e3:.0f} ms",
                 f"{int(fem.free.sum())} 自由 DOF"),
        ],
        notes=["Mindlin 剪切修正 (~1.2(t/(b−a))²) 使 FEM-2D 略软于 "
               "1D/Kirchhoff。",
               "大挠度非线性见「2D 大挠度 (vK)」模式。"])

    return {
        "solver": "2d", "solver_label": "2D FEM (Mindlin)",
        "headline": f"{len(layers)} 片 · 2D Mindlin · "
                    f"顶片 h = {tips_f[0, -1] * 1e3:.5f} mm @ {p_mpa:g} MPa",
        "charts": {
            "hp": _chart("hp", "阀片外缘 h(p): 2D FEM 实线 vs 1D v0 虚线",
                         "压强 p [MPa]", "外缘挠度 h [mm]", hp_series),
            "profile": profile,
        },
        "report": report,
        "meta": {"mesh": {"n_nodes": fem.n_nodes,
                          "n_elem": fem.mesh["n_elem"],
                          "n_cell_r": fem.mesh["n_cell_r"],
                          "n_theta": fem.mesh["n_theta"],
                          "dof": int(fem.free.sum()),
                          "clamp_radius_mm": fem.mesh["clamp_radius_mm"]},
                 "timings": {"assemble_ms": fem.t_assemble * 1e3,
                             "solve_ms": fem.t_solve * 1e3}},
    }


# ============================================================
# 2D 大挠度 (von Kármán)
# ============================================================
def _run_nl(layers, E, nu, p_max, clamp_mm, _n_points, cfg) -> dict:
    nt = cfg["n_theta"] or _auto_n_theta(layers, cfg["n_radial"], clamp_mm)
    nl = PolarShimNLPlate(layers, E=E, nu=nu,
                          n_elem_base_radial=cfg["n_radial"], n_theta=nt,
                          nl=True, clamp_radius_mm=clamp_mm)
    p_mpa = p_max / 1e6
    ps = np.linspace(0.0, p_max, 9)
    tips_nl = nl.solve_curve(ps[1:], n_steps=4, tol=1e-6)   # (nl, 8)

    lin = PolarShimFEM(layers, E=E, nu=nu,
                       n_elem_base_radial=cfg["n_radial"], n_theta=nl.n_theta,
                       clamp_radius_mm=clamp_mm)
    tips_lin = lin.tip_deflections_unit()[:, None] * ps[None, :]
    an_ser, h_an = _analytic_series(layers, p_max, E, nu)

    hp_series = [an_ser]
    for i, layer in enumerate(layers):
        hp_series.append(_layer_series(
            i, layer, ps[1:] / 1e6, tips_nl[i] * 1e3, width=2.2,
            marker="circle", suffix=" 大挠度", series_id=f"l{i + 1}-nl"))
        hp_series.append(_layer_series(
            i, layer, ps / 1e6, tips_lin[i] * 1e3, dash="dash",
            width=1.1, suffix=" 线性", series_id=f"l{i + 1}-lin"))

    r_nl, w_nl = nl.profile()
    r_l, w_l = lin.profile_unit_w()
    profile = _chart(
        "profile",
        f"挠度剖面 w(r) @ p = {p_mpa:g} MPa · 大挠度实线 vs 线性虚线",
        "半径 r [mm]", "挠度 w [mm]",
        [_series("profile-nl", "w(r) 大挠度 vK", r_nl * 1e3,
                 w_nl * 1e3, "#e2e8f0", width=2.0),
         _series("profile-lin", "w(r) 线性", r_l * 1e3,
                 w_l * p_max * 1e3, REF_COLOR, dash="dash", width=1.2)],
        _edge_markers(layers, nl.mesh["clamp_radius_mm"]))

    stiff = [((tips_lin[i, -1] / tips_nl[i, -1])
              if tips_nl[i, -1] != 0 else None)
             for i in range(len(layers))]
    rows, ratio_max = _layer_rows(
        layers, tips_nl[:, -1], ratio_name="w/t", limit=RATIO_NL,
        extra=[f"刚化 {s:.3f}×" if s else "刚化 —" for s in stiff])
    warnings = []
    if ratio_max > RATIO_NL:
        warnings.append(f"w/t > {RATIO_NL:g}: 超出 von Kármán 中等转动域, "
                        f"结果仅供趋势参考")

    lines = [
        f"[2D 大挠度 von Kármán] 网格 {nl.mesh['n_cell_r']} 径向带 × "
        f"nθ={nl.n_theta} → {nl.mesh['n_elem']} 单元, DOF 5/节点 × "
        f"{nl.n_nodes} = {nl.n_dof} (自由 {int(nl.free.sum())})",
        f"Newton 累计 {nl.curve_info['n_newton']} 次 | 载荷步 "
        f"{nl.curve_info['n_steps']} ({nl.curve_info['n_points']} 个曲线点, "
        f"逐点热启动续载) | 曲线耗时 {nl.curve_info['t_total']:.2f} s",
        f"层合刚度: A=ΣEt/(1−ν²) 膜 + D=ΣEt³/12(1−ν²) 弯 + "
        f"k·G·Σt 剪 (B=0: 层间无摩擦可滑移)",
        f"单片解析参照 @ {p_mpa:g} MPa: h = {h_an * 1e3:.5f} mm",
    ]
    for i, layer in enumerate(layers):
        wnl = abs(float(tips_nl[i, -1]))
        ht = wnl / (layer.t_mm * 1e-3)
        kfac = tips_lin[i, -1] / tips_nl[i, -1] if tips_nl[i, -1] != 0 else None
        mark = "  ← 超出 w/t≤3 建议域!" if ht > RATIO_NL else ""
        lines.append(f"  第{i + 1}片 Ø{layer.d_out_mm:g}: "
                     f"h_NL = {wnl * 1e3:.5f} mm (线性 "
                     f"{abs(tips_lin[i, -1]) * 1e3:.5f}) | w/t = {ht:.3f} | "
                     + (f"刚化 {kfac:.3f}×" if kfac else "刚化 —")
                     + mark)
    lines.append(f"环向均匀性 {nl.ring_uniformity():.1e} | "
                 f"von Kármán 适用 w/t ≲ 2~3 (斜率 ≲ 10°); "
                 f"超出需全 Green-Lagrange")
    if ratio_max > RATIO_NL:
        lines.append("[警告] w/t > 3: 超出 von Kármán 中等转动域, "
                     "结果仅供趋势参考")

    report = _report(
        layers, rows, warnings, lines,
        kpis=[
            _kpi("顶片外缘挠度", f"{abs(tips_nl[0, -1]) * 1e3:.5f} mm",
                 f"大挠度 @ {p_mpa:g} MPa"),
            _kpi("刚化因子", f"{stiff[0]:.2f} ×" if stiff[0] else "—",
                 "线性解 / 非线性解"),
            _kpi("最大 w/t", f"{ratio_max:.3f}",
                 f"vK 适用 ≤ {RATIO_NL:g}"),
            _kpi("Newton 迭代", f"{nl.curve_info['n_newton']} 次",
                 f"{nl.curve_info['n_points']} 个曲线点"),
        ],
        notes=["von Kármán 板含膜拉伸刚化; 载荷步进 + 纯 Newton 续载。",
               "w/t > 3 时超出中等转动假设, 需全 Green-Lagrange 模型。"])

    return {
        "solver": "nl", "solver_label": "2D 大挠度 (vK)",
        "headline": f"{len(layers)} 片 · von Kármán · "
                    f"顶片 h = {abs(tips_nl[0, -1]) * 1e3:.5f} mm "
                    f"@ {p_mpa:g} MPa",
        "charts": {
            "hp": _chart("hp", "阀片外缘 h(p): 大挠度 vK 实线 vs 线性虚线",
                         "压强 p [MPa]", "外缘挠度 h [mm]", hp_series),
            "profile": profile,
        },
        "report": report,
        "meta": {"mesh": {"n_nodes": nl.n_nodes,
                          "n_elem": nl.mesh["n_elem"],
                          "n_cell_r": nl.mesh["n_cell_r"],
                          "n_theta": nl.n_theta,
                          "dof": int(nl.free.sum()),
                          "clamp_radius_mm": nl.mesh["clamp_radius_mm"]},
                 "timings": {"curve_s": nl.curve_info["t_total"]}},
    }


# ============================================================
# 2D 分层接触 (μ=0)
# ============================================================
def _run_layered(layers, E, nu, p_max, clamp_mm, _n_points, cfg) -> dict:
    nt = cfg["n_theta"] or _auto_n_theta(layers, cfg["n_radial"], clamp_mm)
    nl_flag = cfg["layered_nonlinear"]
    lr = LayeredStack(layers, E=E, nu=nu,
                      n_elem_base_radial=cfg["n_radial"], n_theta=nt,
                      clamp_radius_mm=clamp_mm, nl=nl_flag)
    if nl_flag:
        ps = np.linspace(0.0, p_max, 7)[1:]
        tips_l = lr.curve(ps, n_steps=4)
        tip_last = tips_l[:, -1]
    else:
        lr.solve(p_max)
        tip_last = lr.tip_deflections()
        ps = np.linspace(0.0, p_max, 25)[1:]
        tips_l = tip_last[:, None] * (ps / p_max)[None, :]

    ref = PolarShimFEM(layers, E=E, nu=nu,
                       n_elem_base_radial=cfg["n_radial"],
                       n_theta=lr.n_theta, clamp_radius_mm=clamp_mm)
    tips_eq_unit = ref.tip_deflections_unit()
    if nl_flag:
        ref_nl = PolarShimNLPlate(layers, E=E, nu=nu,
                                  n_elem_base_radial=cfg["n_radial"],
                                  n_theta=lr.n_theta, nl=True,
                                  clamp_radius_mm=clamp_mm)
        ref_nl.solve(p_max, n_steps=4)
        tips_eq = ref_nl.tip_deflections()
        eq_tag = "等效板 vK"
    else:
        tips_eq = tips_eq_unit * p_max
        eq_tag = "等效板 (ΣD)"

    p_mpa = p_max / 1e6
    hp_series = []
    for i, layer in enumerate(layers):
        hp_series.append(_layer_series(
            i, layer, ps / 1e6, tips_l[i] * 1e3, width=2.2,
            marker="circle" if nl_flag else "none", suffix=" 分层",
            series_id=f"l{i + 1}"))
        hp_series.append(_layer_series(
            i, layer, ps / 1e6, tips_eq_unit[i] * ps * 1e3, dash="dash",
            width=1.1, suffix=" 等效板", series_id=f"l{i + 1}-eq"))

    prof_series = []
    for i, layer in enumerate(layers):
        r_i, w_i = lr.profile(i)
        prof_series.append(_layer_series(
            i, layer, r_i * 1e3, w_i * 1e3, width=2.0,
            label_prefix=f"第{i + 1}片", series_id=f"l{i + 1}-prof"))
    r_eq, w_eq = ref.profile_unit_w()
    prof_series.append(_series("profile-eq", "等效板 ΣD", r_eq * 1e3,
                               w_eq * p_max * 1e3, REF_COLOR,
                               dash="dash", width=1.2))
    profile = _chart(
        "profile", f"分层挠度剖面 w(r) @ p = {p_mpa:g} MPa (每层一条)",
        "半径 r [mm]", "挠度 w [mm]", prof_series,
        [_marker(lr.clamp_m * 1e3, "固支内缘", CLAMP_COLOR, style="dash")])

    rows, ratio_max = _layer_rows(
        layers, tip_last, ratio_name="w/t", limit=RATIO_NL)
    warnings = []
    if ratio_max > RATIO_NL:
        warnings.append(f"最大 w/t = {ratio_max:.2f} > {RATIO_NL:g}: "
                        f"超出建议适用域")

    info = lr.info
    delta = (float(tip_last[0]) - float(tips_eq[0])) / float(tips_eq[0]) \
        if tips_eq[0] != 0 else None
    mode_tag = "von Kármán" if nl_flag else "线性"
    lines = [
        f"[2D 分层 μ=0] {len(layers)} 层 × 网格 {lr.mesh['n_cell_r']} 径向带 "
        f"× nθ={lr.n_theta}; 自由 DOF {int(lr.free.sum())} [{mode_tag}]",
        f"接触对 {info['n_pairs']} (激活 {info['n_active']}, "
        f"分离 {info['n_pairs'] - info['n_active']}) | "
        f"最大穿透 {info['penetration_max'] * 1e6:.3f} µm",
        f"{eq_tag} 顶片 @ {p_mpa:g} MPa = {float(tips_eq[0]) * 1e3:.5f} mm | "
        f"分层偏差 Δ = "
        + (f"{delta * 100:+.2f}%" if delta is not None else "—"),
        f"@ {p_mpa:g} MPa 各层外缘:",
    ]
    for i, layer in enumerate(layers):
        ht = abs(float(tip_last[i])) / (layer.t_mm * 1e-3)
        mark = "  ← 超出 w/t≤3!" if ht > RATIO_NL else ""
        lines.append(f"  第{i + 1}片 Ø{layer.d_out_mm:g}: "
                     f"{float(tip_last[i]) * 1e3:.5f} mm | "
                     f"w/t = {ht:.3f}{mark}")

    report = _report(
        layers, rows, warnings, lines,
        kpis=[
            _kpi("顶片外缘挠度", f"{abs(float(tip_last[0])) * 1e3:.5f} mm",
                 f"分层 @ {p_mpa:g} MPa"),
            _kpi("分层 vs 等效板",
                 f"{delta:+.2%}" if delta is not None else "—",
                 "等效板强制等挠度"),
            _kpi("接触激活", f"{info['n_active']} / {info['n_pairs']}",
                 f"最大穿透 {info['penetration_max'] * 1e6:.2f} µm"),
            _kpi("最大 w/t", f"{ratio_max:.3f}",
                 f"建议 ≤ {RATIO_NL:g}"),
        ],
        notes=["等效板强制各层等挠度/等曲率, 通常略硬; "
               "分层允许各层独立曲率并允许外伸段分离。",
               "层间无摩擦 μ=0 (项目决策)。"])

    return {
        "solver": "layered", "solver_label": "2D 分层 (μ=0)",
        "headline": f"{len(layers)} 片 · 分层接触 · "
                    f"顶片 h = {abs(float(tip_last[0])) * 1e3:.5f} mm "
                    f"@ {p_mpa:g} MPa",
        "charts": {
            "hp": _chart("hp", "阀片外缘 h(p): 分层实线 vs 等效板虚线 "
                        f"[{mode_tag}]", "压强 p [MPa]",
                        "外缘挠度 h [mm]", hp_series),
            "profile": profile,
        },
        "report": report,
        "meta": {"mesh": {"n_nodes": lr.n_nodes,
                          "n_elem": lr.mesh["n_elem"],
                          "n_cell_r": lr.mesh["n_cell_r"],
                          "n_theta": lr.n_theta,
                          "dof": int(lr.free.sum()),
                          "clamp_radius_mm": lr.clamp_m * 1e3},
                 "timings": {}},
    }


_RUNNERS = {"1d": _run_1d, "2d": _run_2d,
            "nl": _run_nl, "layered": _run_layered}


# ============================================================
# 入口
# ============================================================
def analyze(req: Mapping[str, Any]) -> dict:
    """执行一次完整分析, 返回 JSON 友好结果。输入错误抛 AnalysisError."""
    cfg = _parse_request(req)
    t0 = time.perf_counter()
    result = _RUNNERS[cfg["solver"]](
        cfg["layers"], cfg["E"], cfg["nu"], cfg["p_max"],
        cfg["clamp_mm"], cfg["n_points"], cfg)
    elapsed_ms = (time.perf_counter() - t0) * 1e3

    solver_meta = next(s for s in SOLVERS if s["id"] == cfg["solver"])
    result.update({
        "version": BACKEND_VERSION,
        "p_max_MPa": cfg["p_max"] / 1e6,
        "elapsed_ms": round(elapsed_ms, 1),
        "solver": cfg["solver"],
        "solver_label": solver_meta["label"],
        "solver_description": solver_meta["description"],
        "layers": [{
            "index": i + 1,
            "color": _layer_color(i),
            "d_in_mm": l.d_in_mm,
            "d_out_mm": l.d_out_mm,
            "t_mm": l.t_mm,
            "a_mm": l.a_m * 1e3,
            "b_mm": l.b_m * 1e3,
        } for i, l in enumerate(cfg["layers"])],
        "assumptions": get_config()["assumptions"],
        "warnings": result["report"]["warnings"],
    })
    result["report"]["solver"] = cfg["solver"]
    result["report"]["solver_label"] = solver_meta["label"]
    result["report"]["p_max_MPa"] = cfg["p_max"] / 1e6
    result["report"]["elapsed_ms"] = round(elapsed_ms, 1)
    return result
