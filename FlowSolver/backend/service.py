# -*- coding: utf-8 -*-
"""数值后端服务：把 `channel_flow` 封装成 JSON（曲线 + 结构化报告）。

设计约定（与 DeflectionSolver 前端一致）：
* 数值统一为 **mm / MPa / N / m/s**；不可用值输出 null（前端断线）；
* 所有曲线都来自同一次求解，前端不做二次换算；
* 输入/几何错误抛 `ValidationError`（400 + 中文 detail）。
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Any

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
# 同级项目 DeflectionSolver（挠度求解器）供 Q→w→P 迭代使用
_DEFL = os.path.abspath(os.path.join(_ROOT, "..", "DeflectionSolver"))
# 本仓库路径优先；DeflectionSolver 追加末尾，避免其 backend/ 包遮蔽本包
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _DEFL not in sys.path:
    sys.path.append(_DEFL)

import channel_flow as cf  # noqa: E402
from .schemas import AnalyzeRequest  # noqa: E402

BACKEND_VERSION = "1.0.0"

PALETTE = dict(bg="#060a12", panel="#0d1626", accent="#38bdf8",
               accent2="#22d3ee", warn="#fbbf24", danger="#f87171",
               ok="#34d399", muted="#94a3b8")


class ValidationError(ValueError):
    """输入/几何语义错误（→ HTTP 400，中文 detail）。"""


# ------------------------------------------------------------
# 请求 → 物理对象
# ------------------------------------------------------------
def _piston_from(p) -> cf.PistonSpec:
    # 界面口径：夹持直径 / 最大阀片直径（旧 radius 字段仍兼容）
    clamp_d = getattr(p, "clamp_diameter_mm", None)
    if clamp_d is not None and clamp_d > 0:
        seat_radius = clamp_d / 2.0
    else:
        seat_radius = p.seat_radius_mm
    outer_d = getattr(p, "shim_outer_diameter_mm", None)
    if outer_d is not None and outer_d > 0:
        shim_outer = outer_d / 2.0
    else:
        shim_outer = p.shim_outer_mm
    spec = cf.PistonSpec(
        d_piston_mm=p.d_piston_mm, d_rod_mm=p.d_rod_mm, n_ports=p.n_ports,
        port_width_mm=p.port_width_mm,
        port_radial_mm=getattr(p, "port_radial_mm", 4.0),
        port_radius_mm=p.port_radius_mm,
        seat_radius_mm=seat_radius, deck_height_mm=p.deck_height_mm,
        shim_outer_mm=shim_outer,
        bypass_d_mm=getattr(p, "bypass_d_mm", 0.0),
        bypass_n=getattr(p, "bypass_n", 0),
        bypass_Cd=getattr(p, "bypass_Cd", 0.62))
    try:
        spec.validate()
    except ValueError as exc:
        raise ValidationError(f"活塞几何非法: {exc}") from exc
    return spec


def _fluid_from(f) -> cf.FluidSpec:
    spec = cf.FluidSpec(rho=f.rho, cSt=f.cSt, p_vapor_kPa=f.p_vapor_kPa,
                        p_back_MPa=f.p_back_MPa,
                        T_C=getattr(f, "T_C", 40.0),
                        KV40=getattr(f, "KV40", 0.0),
                        KV100=getattr(f, "KV100", 0.0),
                        VI=getattr(f, "VI", 0.0))
    try:
        spec.validate()
    except ValueError as exc:
        raise ValidationError(f"油液参数非法: {exc}") from exc
    return spec




def _fluid_warnings(fluid) -> list[str]:
    out = []
    if fluid.KV40 > 0 and fluid.VI != 0:
        try:
            vi_calc = cf.viscosity_index(fluid.KV40, fluid.KV100)
        except ValueError:
            return out
        if abs(vi_calc - fluid.VI) > max(5.0, 0.05 * abs(fluid.VI)):
            out.append(f"输入 VI={fluid.VI:g} 与 KV40/KV100 反算 VI="
                       f"{vi_calc:.0f} 不一致（以 KV40/KV100 外推为准）")
    return out

def _gap(req: AnalyzeRequest, piston: cf.PistonSpec):
    g = req.gap
    if g.mode == "power_law":
        return cf.PowerLawGap(lift_m=float(g.lift_mm) * 1e-3,
                              r_seat_m=piston.r_seat_m(),
                              r_outer_m=piston.r_outer_m(),
                              power=float(g.power),
                              h_deck_m=piston.h_deck_m())
    if not g.r_mm or not g.w_mm:
        raise ValidationError("数组模式必须提供 r_mm 与 w_mm")
    if len(g.r_mm) != len(g.w_mm):
        raise ValidationError("r_mm 与 w_mm 长度不一致")
    try:
        return cf.ArrayGap(np.asarray(g.r_mm, float) * 1e-3,
                           np.asarray(g.w_mm, float) * 1e-3,
                           h_deck_m=piston.h_deck_m())
    except ValueError as exc:
        raise ValidationError(f"挠度场数组非法: {exc}") from exc


# ------------------------------------------------------------
# 图表构造
# ------------------------------------------------------------
def _series(sid: str, label: str, color: str, x, y, **kw) -> dict:
    out = dict(id=sid, label=label, color=color, dash=kw.get("dash", "solid"),
               width=kw.get("width", 2.0), marker=kw.get("marker", "none"),
               x=[float(v) for v in x], y=[float(v) for v in y])
    return out



def _grid_text(piston, spec, par) -> str:
    """网格描述；扇区降维时标注 1/N 扇区."""
    sec, nt = cp.sector_plan(piston, spec)
    base = f"{par.n_radial}×{spec.n_theta_eff()}"
    return base + (f"（1/{sec} 扇区×{nt}）" if sec > 1 else "")

def _markers(piston: cf.PistonSpec) -> list[dict]:
    return [
        dict(axis="x", value=piston.seat_radius_mm, label="夹持半径",
             color=PALETTE["muted"], style="dash"),
        dict(axis="x", value=piston.port_radius_mm, label="窗口中心",
             color=PALETTE["warn"], style="dash"),
        dict(axis="x", value=piston.r_curtain_m() * 1e3, label="节流外缘",
             color=PALETTE["accent"], style="dash"),
        dict(axis="x", value=piston.shim_outer_mm, label="最大阀片半径",
             color=PALETTE["accent2"], style="dash"),
    ]


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    t0 = time.perf_counter()
    piston = _piston_from(req.piston)
    fluid = _fluid_from(req.fluid)
    gap = _gap(req, piston)
    coef = req.coefficients

    v_point = (req.operating.v_point_mps if req.operating.v_point_mps
               is not None else req.operating.v_max_mps * 0.5)
    A_eff = piston.area_effective_m2()
    Q_point = float(v_point) * A_eff
    A_bypass = piston.area_bypass_m2()
    has_bypass = A_bypass > 0

    res = cf.solve_split(Q_point, piston, fluid, gap, n_grid=req.grid_points,
                         C_d=coef.C_d, C_f=coef.C_f)

    vs = np.linspace(0.0, req.operating.v_max_mps, req.operating.n_points)
    cur = cf.solve_curve(vs, piston, fluid, gap,
                         split=has_bypass,
                         n_grid=max(200, req.grid_points // 2),
                         C_d=coef.C_d, C_f=coef.C_f)

    r_mm = res.r_m * 1e3
    h_mm = res.h_m * 1e3
    p_mpa = res.p_pa / 1e6

    charts = {
        "gap": dict(
            title="阀片-活塞面间隙 h(r)", x_label="半径 r [mm]",
            y_label="间隙 h [mm]",
            series=[_series("h", "间隙 h(r)", PALETTE["accent2"], r_mm, h_mm)],
            markers=_markers(piston)),
        "pressure": dict(
            title="流道压力场 p(r)", x_label="半径 r [mm]",
            y_label="压力 p [MPa]",
            series=[_series("p", "压力场 p(r)", PALETTE["accent"], r_mm, p_mpa)],
            markers=_markers(piston)),
        "force": dict(
            title="阻尼力 F(v)", x_label="活塞速度 v [m/s]", y_label="阻尼力 F [N]",
            series=[_series("F", "总阻尼力 F", PALETTE["accent"], vs, cur["F_N"]),
                    _series("Fj", "射流分力 F_jet", PALETTE["warn"], vs,
                            cur["F_jet_N"], dash="dash")],
            markers=[]),
    }

    total = max(res.dp_total, 1e-12)
    share_curtain = res.dp_curtain / total * 100
    share_visc = res.dp_viscous / total * 100
    share_inert = res.dp_inertia / total * 100

    kpis = [
        dict(label="阻尼力 F", value=f"{res.F_damper:.1f} N",
             hint=f"@ v = {v_point:.3f} m/s"),
        dict(label="总压降 Δp", value=f"{res.dp_total/1e6:.4f} MPa",
             hint=f"上游 {res.p_up/1e6:.4f} MPa"),
        dict(label="截流面积 A_th", value=f"{res.A_th*1e6:.2f} mm²",
             hint=f"h_port = {res.h_port*1e6:.1f} µm"),
        dict(label="截流流速", value=f"{res.v_port:.1f} m/s",
             hint=f"射流滞止压 {res.p_jet/1e6:.3f} MPa"),
        dict(label="射流力占比", value=f"{res.F_jet/max(res.F_damper,1e-9)*100:.2f} %",
             hint=f"{res.F_jet:.2f} N"),
        dict(label="Reynolds", value=f"{res.Re:.0f}", hint="间隙入口处"),
    ]
    if has_bypass:
        q_share = res.Q_bypass_m3s / max(res.Q_total_m3s, 1e-18) * 100
        kpis.append(dict(
            label="常通孔分流", value=f"{q_share:.1f} %",
            hint=f"A_b = {A_bypass*1e6:.3f} mm² · "
                 f"Q_b = {res.Q_bypass_m3s*1e6:.2f} mL/s"))

    lines = [
        f"[定常流道 v1] 活塞 Ø{piston.d_piston_mm:g} / 杆 Ø{piston.d_rod_mm:g} mm, "
        f"{piston.n_ports}×{piston.port_width_mm:g}(周向)×{piston.port_radial_mm:g}(径向) mm "
        f"窗口 @ r={piston.port_radius_mm:g} mm, 张角 {piston.port_arc_deg():.1f}°",
        f"流体 ρ={fluid.rho:g} kg/m³, {fluid.describe_viscosity()}, "
        f"背压 {fluid.p_back_MPa:g} MPa",
        f"挠度场: {gap.describe()}",
        f"Q_total = {res.Q_total_m3s*1e6:.3f} mL/s (v={v_point:.3f} m/s), "
        f"Q_main = {res.Q_m3s*1e6:.3f} mL/s, A_eff = {A_eff*1e6:.1f} mm²",
    ]
    if has_bypass:
        lines.append(f"常通孔: n={piston.bypass_n}×Ø{piston.bypass_d_mm:g} mm, "
                     f"A_b = {A_bypass*1e6:.3f} mm², "
                     f"Q_b = {res.Q_bypass_m3s*1e6:.3f} mL/s "
                     f"({res.Q_bypass_m3s/max(res.Q_total_m3s,1e-18)*100:.1f}%)")
    lines += [
        f"损失分解: 截流 {share_curtain:.1f}% | 黏性 {share_visc:.1f}% | "
        f"惯性 {share_inert:.1f}%",
        f"阀片受载 F_shim = {res.F_shim:.1f} N (含射流 {res.F_jet:.2f} N)",
        f"求解 {req.grid_points} 网格点, 耗时 "
        f"{(time.perf_counter()-t0)*1e3:.0f} ms",
    ]

    notes = [
        "v1 假设：轴对称、不可压、定常、层流；未建空化/湍流/choked flow 模型。",
        "主导阻尼：窗口-阀片截流面积（帘幕 A_th = N·w_out·h(r_out)，取窗口外缘）与"
        "射流冲击；黏性/惯性径向流道为次要项。",
        "挠度场为输入：可与 DeflectionSolver 组成 Q→w→P→w′ 定点迭代。",
    ]
    if has_bypass:
        notes.insert(1, "常通孔为固定几何旁通（Q_b = C_d·A_b·sqrt(2Δp/ρ)），"
                        "与主阀并联、按压力自动分流，不随阀片挠度/迭代变化。")

    report = dict(
        kpis=kpis,
        warnings=list(res.warnings) + _fluid_warnings(fluid),
        notes=notes,
        lines=lines,
        layers=[],
    )

    return dict(
        solver="flow1d", solver_label="定常流道-压力场 v1",
        headline=(f"F = {res.F_damper:.1f} N @ v={v_point:.2f} m/s · "
                  f"Δp = {res.dp_total/1e6:.3f} MPa · "
                  f"截流 {share_curtain:.0f}%"),
        elapsed_ms=round((time.perf_counter() - t0) * 1e3, 1),
        charts=charts, report=report,
        meta=dict(Q_m3s=res.Q_m3s, Q_total_m3s=res.Q_total_m3s,
                  Q_bypass_m3s=res.Q_bypass_m3s, A_bypass_m2=A_bypass,
                  A_eff_m2=A_eff, A_th_m2=res.A_th,
                  h_port_m=res.h_port, v_port_mps=res.v_port,
                  p_jet_Pa=res.p_jet, F_jet_N=res.F_jet, F_shim_N=res.F_shim,
                  dp_curtain_Pa=res.dp_curtain, dp_viscous_Pa=res.dp_viscous,
                  dp_inertia_Pa=res.dp_inertia, Re=res.Re,
                  v_point_mps=v_point, grid_points=req.grid_points))


def config() -> dict[str, Any]:
    return dict(
        version=BACKEND_VERSION,
        solver=dict(id="flow1d", label="定常流道-压力场 v1",
                    description="给定挠度场与流量，求截流压降、压力场与阻尼力"),
        palette=PALETTE,
        preset=dict(piston=dict(d_piston_mm=46.0, d_rod_mm=18.0, n_ports=6,
                                port_width_mm=8.0, port_radial_mm=4.0,
                                port_radius_mm=16.0,
                                clamp_diameter_mm=18.0, seat_radius_mm=9.0,
                                deck_height_mm=0.10,
                                shim_outer_diameter_mm=42.0, shim_outer_mm=21.0,
                                bypass_d_mm=0.0,
                                bypass_n=0, bypass_Cd=0.62),
                    fluid=dict(rho=850.0, cSt=15.0, p_back_MPa=0.0,
                               T_C=40.0, KV40=0.0, KV100=0.0, VI=0.0,
                               p_vapor_kPa=0.0),
                    gap=dict(mode="power_law", lift_mm=0.15, power=2.0),
                    coefficients=dict(C_d=0.70, C_f=0.30),
                    operating=dict(v_max_mps=0.5, n_points=21),
                    layers=[dict(d_in_mm=18, d_out_mm=42, t_mm=0.25),
                            dict(d_in_mm=18, d_out_mm=36, t_mm=0.20),
                            dict(d_in_mm=18, d_out_mm=30, t_mm=0.20)],
                    iterate=dict(v_mps=0.15, alpha=0.5, tol_um=2.0,
                                 max_iter=25, relax_step_um=150.0, nl=False,
                                 n_radial=16, n_theta=12,
                                 window_load=False, window_contrast=None,
                                 sector_reduction=True,
                                 sector_load=False, sector_scale=True,
                                 nl_tol=1e-6),
                    curve=dict(v_min_mps=0.02, v_max_mps=0.50,
                               n_points=6)),
        limits=dict(grid_points=dict(min=50, max=4000, default=400),
                    n_points=dict(min=2, max=200, default=21),
                    max_iter=dict(min=1, max=100, default=25)))



# ------------------------------------------------------------
# 耦合迭代（Q → w → P → w′；内核见 FlowSolver/coupling.py）
# ------------------------------------------------------------
import coupling as cp  # noqa: E402


def _layers_mm(req) -> list[tuple[float, float, float]]:
    if not req.layers:
        raise ValidationError("迭代模式需要至少一片阀片")
    return [(l.d_in_mm, l.d_out_mm, l.t_mm) for l in req.layers]


def _spec_from(par, coef) -> cp.IterationSpec:
    return cp.IterationSpec(
        alpha=par.alpha, tol_um=par.tol_um, max_iter=par.max_iter,
        relax_step_um=par.relax_step_um, nl=par.nl,
        n_radial=par.n_radial, n_theta=par.n_theta,
        C_d=coef.C_d, C_f=coef.C_f,
        window_load=getattr(par, "window_load", False),
        window_contrast=getattr(par, "window_contrast", None),
        sector_reduction=getattr(par, "sector_reduction", True),
        sector_load=getattr(par, "sector_load", False),
        sector_scale=getattr(par, "sector_scale", True),
        nl_tol=getattr(par, "nl_tol", 1e-6))


def _make_stack(layers_mm, piston, spec):
    try:
        return cp.build_stack(layers_mm, piston, spec)
    except Exception as exc:                      # noqa: BLE001
        raise ValidationError(f"阀片堆叠非法: {exc}") from exc


def iterate(req) -> dict[str, Any]:
    """单点闭环迭代：流道压力场 ↔ 阀片挠度场，直到残差收敛."""
    t0 = time.perf_counter()
    piston = _piston_from(req.piston)
    fluid = _fluid_from(req.fluid)
    par, coef = req.iterate, req.coefficients
    layers_mm = _layers_mm(req)
    spec = _spec_from(par, coef)
    stack = _make_stack(layers_mm, piston, spec)

    pt = cp.iterate_point(par.v_mps, piston, fluid, stack, spec)
    fin, hist, w = pt["res"], pt["history"], pt["w"]
    total = max(fin.dp_total, 1e-12)

    conv = dict(title="迭代收敛（残差）", x_label="迭代次数",
                y_label="残差 max|Δw| [µm]",
                series=[_series("res", "残差", PALETTE["warn"],
                                [h["iter"] for h in hist],
                                [h["residual_um"] for h in hist])],
                markers=[])
    pres = dict(title="收敛后的流道压力场 p(r)", x_label="半径 r [mm]",
                y_label="压力 p [MPa]",
                series=[_series("p", "压力场 p(r)", PALETTE["accent"],
                                fin.r_m * 1e3, fin.p_pa / 1e6)],
                markers=_markers(piston))
    defl_series = []
    n_layers = len(stack.layers)
    for i, layer in enumerate(stack.layers):
        ir = np.unique(stack.nodes_i[i] // stack.n_theta_nodes)
        r_i = stack.r_nodes[ir] * 1e3
        w_i = [stack.w_matrix(i)[j].mean() * 1e6 for j in ir]
        defl_series.append(_series(
            f"w{i}", f"第{n_layers - i}片 Ø{layer.d_out_mm:g}（自活塞向上）",
            PALETTE["accent2"],
            r_i, w_i, dash="solid" if i == n_layers - 1 else "dash"))
    defl = dict(title="收敛后的挠度场 w(r)（负值 = 抬升）",
                x_label="半径 r [mm]", y_label="挠度 w [µm]",
                series=defl_series, markers=_markers(piston))

    kpis = [
        dict(label="阻尼力 F", value=f"{fin.F_damper:.1f} N",
             hint=f"@ v = {par.v_mps:.3f} m/s"),
        dict(label="总压降 Δp", value=f"{fin.dp_total/1e6:.4f} MPa",
             hint=f"截流 {fin.dp_curtain/total*100:.1f}%"),
        dict(label="阀片外缘抬升", value=f"{abs(w[-1])*1e6:.1f} µm",
             hint=f"h_port = {fin.h_port*1e6:.1f} µm"),
        dict(label="迭代", value=f"{pt['iterations']} 次",
             hint=f"残差 {pt['residual_um']:.3f} µm"),
        dict(label="射流滞止压", value=f"{fin.p_jet/1e6:.3f} MPa",
             hint=f"F_jet = {fin.F_jet:.2f} N"),
        dict(label="A_th", value=f"{fin.A_th*1e6:.2f} mm²",
             hint=f"v_port = {fin.v_port:.1f} m/s"),
    ]
    if fin.A_bypass_m2 > 0:
        kpis.insert(3, dict(
            label="常通孔分流",
            value=f"{fin.Q_bypass_m3s/max(fin.Q_total_m3s,1e-18)*100:.1f} %",
            hint=f"A_b = {fin.A_bypass_m2*1e6:.3f} mm² · "
                 f"Q_b = {fin.Q_bypass_m3s*1e6:.2f} mL/s"))
    warn = list(fin.warnings) + _fluid_warnings(fluid)
    if not pt["converged"]:
        warn.append(f"未在 {par.max_iter} 次内收敛（最后残差 "
                    f"{pt['residual_um']:.3f} µm）")

    lines = [
        f"[迭代耦合] {len(stack.layers)} 片 · v={par.v_mps:.3f} m/s · "
        f"松弛 α={par.alpha:g} · 信赖域 {par.relax_step_um:g} µm · "
        f"{'vK' if par.nl else '线性'}",
        (f"网格: {par.n_radial}×{spec.n_theta_eff()}"
         + (f"（1/{stack.sector} 扇区×{stack.n_theta}）"
            if getattr(stack, 'sector', 1) > 1 else "")
         + f" · Q={pt['Q_m3s']*1e6:.2f} mL/s · "),
        f"A_eff={piston.area_effective_m2()*1e6:.1f} mm²",
        f"收敛: {pt['converged']} · 迭代 {pt['iterations']} 次 · "
        f"残差 {pt['residual_um']:.3f} µm",
        f"最终: F={fin.F_damper:.1f} N · Δp={fin.dp_total/1e6:.4f} MPa · "
        f"w_tip={w[-1]*1e6:.2f} µm · h_port={fin.h_port*1e6:.2f} µm",
        f"损失: 截流 {fin.dp_curtain/total*100:.1f}% | 黏性 "
        f"{fin.dp_viscous/total*100:.1f}% | 惯性 "
        f"{fin.dp_inertia/total*100:.1f}%",
    ]
    if spec.window_load:
        _prof, cov, contrast = cp.window_profile(piston, spec.window_contrast)
        lines.insert(2, f"载荷: 窗口分布（平滑 W=1+{contrast:.2f}·cos(Nθ)）· "
                        f"覆盖率 {cov*100:.1f}% · 射流为 {piston.n_ports} 个高斯冲击斑（周向 w/2 × 径向 dr/2）")
    if fin.A_bypass_m2 > 0:
        lines.insert(2, f"常通孔: n={piston.bypass_n}×Ø{piston.bypass_d_mm:g} mm · "
                        f"A_b={fin.A_bypass_m2*1e6:.3f} mm² · "
                        f"Q_main={fin.Q_m3s*1e6:.2f} / "
                        f"Q_bypass={fin.Q_bypass_m3s*1e6:.2f} mL/s")
    for h in hist[-6:]:
        lines.append(f"  it {h['iter']:>2}: 残差 {h['residual_um']:8.3f} µm · "
                     f"F {h['F_N']:8.1f} N · h_port {h['h_port_um']:7.1f} µm")

    notes = ["迭代变量 w(r) = 阀片挠度场；压力场由 FlowSolver 给出，"
             "挠度由 DeflectionSolver 分层模型给出。",
             "负 w = 阀片被油压顶开；间隙 h = h_deck − w。",
             "层序：layers[0] = 贴活塞第 1 片（迎油面），油压作用其底面，向上逐层接触传力。",
             "帘幕：节流面积取窗口外缘 r_out = r_port + dr/2 处的弧长与外缘间隙；"
             "流道积分也从 r_out 开始。",
             "v0 迭代：固定松弛 + 信赖域；收敛判据 max|Δw| < tol。"]
    if spec.window_load:
        notes.append("窗口分布载荷（v2）：基础流场压力乘以周向平滑调制 "
                     "W(θ)=1+c·cos(Nθ)（均值 1、处处为正，无阶跃），"
                     "射流为 N 个高斯冲击压强斑叠加（周向 σ=w/2、径向 σ=dr/2），不删除任何区域。")
    if fin.A_bypass_m2 > 0:
        notes.append("常通孔为固定几何旁通，按压力与主阀自动分流，"
                     "面积不参与迭代。")

    return dict(
        solver="iterate", solver_label="流固耦合定点迭代 v0",
        headline=(f"F = {fin.F_damper:.1f} N @ v={par.v_mps:.2f} m/s · "
                  f"{pt['iterations']} 次迭代 · 残差 "
                  f"{pt['residual_um']:.2f} µm"),
        elapsed_ms=round((time.perf_counter() - t0) * 1e3, 1),
        charts=dict(convergence=conv, pressure=pres, deflection=defl),
        report=dict(kpis=kpis, warnings=warn, notes=notes, lines=lines,
                    layers=[]),
        meta=dict(converged=pt["converged"], iterations=pt["iterations"],
                  residual_um=pt["residual_um"], history=hist,
                  w_tip_um=float(w[-1]) * 1e6,
                  F_damper=fin.F_damper, dp_MPa=fin.dp_total / 1e6,
                  h_port_um=fin.h_port * 1e6, p_jet_MPa=fin.p_jet / 1e6,
                  A_th_m2=fin.A_th, v_port_mps=fin.v_port, Re=fin.Re,
                  Q_m3s=pt["Q_m3s"], v_mps=par.v_mps,
                  Q_total_m3s=fin.Q_total_m3s,
                  Q_bypass_m3s=fin.Q_bypass_m3s,
                  A_bypass_m2=fin.A_bypass_m2))


def fv_curve(req) -> dict[str, Any]:
    """Q–F 曲线：沿速度序列逐点闭环迭代（热启动），输出 F(Q) 与逐点收敛信息."""
    t0 = time.perf_counter()
    piston = _piston_from(req.piston)
    fluid = _fluid_from(req.fluid)
    par, coef = req.curve, req.coefficients
    if not (0.0 < par.v_min_mps < par.v_max_mps):
        raise ValidationError("要求 0 < v_min < v_max")
    layers_mm = _layers_mm(req)
    spec = _spec_from(par, coef)
    vs = np.linspace(par.v_min_mps, par.v_max_mps, par.n_points)

    out = cp.fv_curve(vs, piston, fluid, layers_mm, spec)
    pts = out["points"]
    q_ml = [p["Q_m3s"] * 1e6 for p in pts]
    f_n = [p["F_N"] for p in pts]
    iters = [p["iterations"] for p in pts]
    resid = [p["residual_um"] for p in pts]
    n_ok = out["n_converged"]
    i_max = max(range(len(f_n)), key=lambda i: f_n[i])

    curve = dict(
        title="Q–F 阻尼力曲线（闭环迭代）", x_label="流量 Q [mL/s]",
        y_label="阻尼力 F [N]",
        series=[_series("F", "耦合迭代 F(Q)", PALETTE["accent"], q_ml, f_n)],
        markers=[])
    convc = dict(
        title="逐点迭代次数与残差", x_label="流量 Q [mL/s]",
        y_label="迭代次数 / 残差 [µm]",
        series=[_series("it", "迭代次数", PALETTE["accent2"], q_ml, iters),
                _series("res", "残差 [µm]", PALETTE["warn"], q_ml, resid,
                        dash="dash")],
        markers=[])

    kpis = [
        dict(label="最大阻尼力", value=f"{f_n[i_max]:.1f} N",
             hint=f"@ Q = {q_ml[i_max]:.1f} mL/s"),
        dict(label="最小阻尼力", value=f"{min(f_n):.1f} N",
             hint=f"@ Q = {q_ml[f_n.index(min(f_n))]:.1f} mL/s"),
        dict(label="收敛点", value=f"{n_ok}/{len(pts)}",
             hint="逐点定点迭代"),
        dict(label="平均迭代", value=f"{sum(iters)/len(iters):.1f} 次",
             hint=f"最大 {max(iters)} 次"),
        dict(label="最大残差", value=f"{max(resid):.3f} µm",
             hint="max|Δw|"),
        dict(label="总耗时", value=f"{out['elapsed_ms']/1000:.1f} s",
             hint=f"{len(pts)} 个工况点"),
    ]

    warn = _fluid_warnings(fluid)
    if n_ok < len(pts):
        warn.append(f"{len(pts)-n_ok} 个工况点未收敛（可减小 α 或放宽 tol）")
    for p in reversed(pts):
        if p["warnings"]:
            warn.append(f"Q={p['Q_m3s']*1e6:.1f} mL/s: {p['warnings'][0]}")
            break

    lines = [
        f"[Q–F 曲线] {len(layers_mm)} 片 · "
        f"v {par.v_min_mps:g}→{par.v_max_mps:g} m/s · {len(pts)} 点 · "
        f"{'vK' if par.nl else '线性'} · α={par.alpha:g} · tol={par.tol_um:g} µm",
        f"网格 {_grid_text(piston, spec, par)} · 总耗时 {out['elapsed_ms']/1000:.1f} s",
        f"{'Q(mL/s)':>9} {'v(m/s)':>8} {'F(N)':>10} {'Δp(MPa)':>9} "
        f"{'w_tip(µm)':>10} {'it':>3} {'res(µm)':>8}",
    ]
    if spec.window_load:
        _pf, cov, contrast = cp.window_profile(piston, spec.window_contrast)
        lines.insert(3, f"载荷: 窗口分布（平滑）· N={piston.n_ports} · "
                        f"对比度 c={contrast:.2f} · 覆盖率 {cov*100:.1f}% · "
                        f"射流 {piston.n_ports} 个高斯冲击斑")
    if piston.area_bypass_m2() > 0:
        p_end = pts[-1]
        lines.insert(2, f"常通孔 n={piston.bypass_n}×Ø{piston.bypass_d_mm:g} mm · "
                        f"A_b={piston.area_bypass_m2()*1e6:.3f} mm² · "
                        f"最大点 Q_bypass={p_end['Q_bypass_m3s']*1e6:.2f} mL/s "
                        f"({p_end['Q_bypass_m3s']/max(p_end['Q_m3s'],1e-18)*100:.1f}%)")
    for p in pts:
        lines.append(
            f"{p['Q_m3s']*1e6:9.2f} {p['v_mps']:8.3f} {p['F_N']:10.1f} "
            f"{p['dp_MPa']:9.4f} {p['w_tip_um']:10.1f} "
            f"{p['iterations']:3d} {p['residual_um']:8.3f}")

    return dict(
        solver="fv-curve", solver_label="闭环 Q–F 曲线 v0",
        headline=(f"Q–F: {len(pts)} 点 · {n_ok} 收敛 · "
                  f"F {min(f_n):.0f}→{max(f_n):.0f} N · "
                  f"{out['elapsed_ms']/1000:.0f} s"),
        elapsed_ms=round((time.perf_counter() - t0) * 1e3, 1),
        charts=dict(curve=curve, convergence=convc),
        report=dict(kpis=kpis, warnings=warn,
                    notes=["Q–F 曲线 = 每个流量点做一次 Q→w→P→w′ 定点迭代；"
                           "相邻点用上一点挠度热启动。",
                           "热启动只影响收敛速度，不改变收敛解；"
                           "未收敛点仍会输出结果但标注告警。"],
                    lines=lines, layers=[]),
        meta=dict(points=pts, n_converged=n_ok, n_points=len(pts),
                  total_ms=out["elapsed_ms"],
                  layers=[dict(d_in_mm=a, d_out_mm=b, t_mm=c)
                          for a, b, c in layers_mm]))

def rod_curve(req) -> dict[str, Any]:
    """活塞杆速度 → 阻尼力：Q 由杆速决定，输出压缩/回弹 F(v) 分支."""
    t0 = time.perf_counter()
    piston = _piston_from(req.piston)
    fluid = _fluid_from(req.fluid)
    par, coef = req.curve, req.coefficients
    if not (0.0 < par.v_min_mps < par.v_max_mps):
        raise ValidationError("要求 0 < v_min < v_max")
    comp_layers = _layers_mm(req)
    reb_layers = None
    if req.layers_rebound:
        reb_layers = [(l.d_in_mm, l.d_out_mm, l.t_mm)
                      for l in req.layers_rebound]
    spec = _spec_from(par, coef)
    vs = np.linspace(par.v_min_mps, par.v_max_mps, par.n_points)

    out = cp.rod_curve(vs, piston, fluid, comp_layers, spec, reb_layers)
    branches = out["branches"]
    kin = out["kinematics"]
    A_p, A_rod, A_eff = (kin["A_p_m2"], kin["A_rod_m2"], kin["A_eff_m2"])
    q_valve = [A_eff * v * 1e6 for v in vs]
    q_rod = [A_rod * v * 1e6 for v in vs]

    colors = {"compression": PALETTE["accent"], "rebound": PALETTE["warn"]}
    labels = {"compression": "压缩", "rebound": "回弹"}
    force_series = []
    for name in ("compression", "rebound"):
        if name not in branches:
            continue
        pts = branches[name]["points"]
        force_series.append(_series(
            f"F-{name}", f"{labels[name]}行程 F(v)", colors[name],
            [p["v_mps"] for p in pts], [p["F_N"] for p in pts],
            dash="solid" if name == "compression" else "dash"))
    force = dict(title="活塞杆速度–阻尼力 F(v)", x_label="杆速 v [m/s]",
                 y_label="阻尼力 F [N]", series=force_series, markers=[])
    # 杆速↔流量是纯线性换算，不单独出图（数值见报告中的 Q_valve / Q_rod）

    kpis = [
        dict(label="活塞面积 A_p", value=f"{A_p*1e6:.1f} mm²", hint="π/4·D²"),
        dict(label="杆面积 A_rod", value=f"{A_rod*1e6:.1f} mm²", hint="π/4·d²"),
        dict(label="活塞阀面积", value=f"{A_eff*1e6:.1f} mm²", hint="A_p − A_rod"),
        dict(label="A_rod/A_p", value=f"{A_rod/A_p*100:.1f} %",
             hint="决定杆腔排量占比"),
    ]
    for name in ("compression", "rebound"):
        if name in branches:
            pts = branches[name]["points"]
            f_n = [p["F_N"] for p in pts]
            kpis.append(dict(label=f"{labels[name]}最大 F",
                             value=f"{max(f_n):.1f} N",
                             hint=f"@ v={vs[f_n.index(max(f_n))]:.2f} m/s"))
    total_pts = sum(len(b["points"]) for b in branches.values())
    total_ok = sum(b["n_converged"] for b in branches.values())
    kpis.append(dict(label="收敛点", value=f"{total_ok}/{total_pts}",
                     hint=f"{len(branches)} 条分支"))
    kpis.append(dict(label="总耗时", value=f"{out['elapsed_ms']/1000:.1f} s",
                     hint=f"{len(vs)} 个杆速点"))

    warn = _fluid_warnings(fluid)
    if total_ok < total_pts:
        warn.append(f"{total_pts-total_ok} 个点未收敛（可减小 α 或放宽 tol）")
    for name, br in branches.items():
        for p in reversed(br["points"]):
            if p["warnings"]:
                warn.append(f"{labels[name]} v={p['v_mps']:.2f} m/s: "
                            f"{p['warnings'][0]}")
                break

    lines = [
        f"[杆速→阻尼力] {len(comp_layers)} 片 · v {par.v_min_mps:g}→"
        f"{par.v_max_mps:g} m/s · {len(vs)} 点 · "
        f"{'vK' if par.nl else '线性'} · α={par.alpha:g}",
        f"面积: A_p={A_p*1e6:.1f}, A_rod={A_rod*1e6:.1f}, "
        f"A_eff={A_eff*1e6:.1f} mm²",
        f"流量: Q_valve=(A_p−A_rod)·v = {q_valve[0]:.1f}→{q_valve[-1]:.1f} mL/s; "
        f"Q_rod=A_rod·v = {q_rod[0]:.1f}→{q_rod[-1]:.1f} mL/s",
        f"网格 {_grid_text(piston, spec, par)} · 总耗时 {out['elapsed_ms']/1000:.1f} s",
    ]
    if spec.window_load:
        _pf, cov, contrast = cp.window_profile(piston, spec.window_contrast)
        lines.insert(3, f"载荷: 窗口分布（平滑）· N={piston.n_ports} · "
                        f"对比度 c={contrast:.2f} · 覆盖率 {cov*100:.1f}% · "
                        f"射流 {piston.n_ports} 个高斯冲击斑")
    if piston.area_bypass_m2() > 0:
        lines.insert(3, f"常通孔 n={piston.bypass_n}×Ø{piston.bypass_d_mm:g} mm · "
                        f"A_b={piston.area_bypass_m2()*1e6:.3f} mm² "
                        f"(与主阀并联, 按压力分流)")
    for name in ("compression", "rebound"):
        if name not in branches:
            continue
        lines.append(f"{labels[name]}:")
        lines.append(f"  {'v(m/s)':>7} {'F(N)':>10} {'Q_valve':>9} "
                     f"{'Q_rod':>8} {'Q_byp':>8} {'it':>3} {'res(µm)':>8}")
        for p in branches[name]["points"]:
            lines.append(
                f"  {p['v_mps']:7.3f} {p['F_N']:10.1f} "
                f"{p['Q_valve_m3s']*1e6:9.1f} {p['Q_rod_m3s']*1e6:8.1f} "
                f"{p['Q_bypass_m3s']*1e6:8.1f} "
                f"{p['iterations']:3d} {p['residual_um']:8.3f}")

    headline = (f"F(v): " + " / ".join(
        f"{labels[n]} {max(p['F_N'] for p in b['points']):.0f} N"
        for n, b in branches.items())
        + f" @ v≤{par.v_max_mps:g} m/s · {total_ok}/{total_pts} 收敛")
    return dict(
        solver="rod-curve", solver_label="杆速–阻尼力（闭环）v0",
        headline=headline,
        elapsed_ms=round((time.perf_counter() - t0) * 1e3, 1),
        charts=dict(force=force),
        report=dict(kpis=kpis, warnings=warn,
                    notes=["Q 由活塞杆侵入油缸的体积速度决定："
                           "Q_rod=A_rod·v，活塞阀 Q=(A_p−A_rod)·v。",
                           "压缩/回弹分支可为不同阀片堆叠（API 的 layers_rebound）；"
                           "未提供时仅输出压缩分支。",
                           "F(v) 的每个点内部仍是 Q→w→P→w′ 定点迭代。"],
                    lines=lines, layers=[]),
        meta=dict(kinematics=kin, branches=branches,
                  total_ok=total_ok, total_pts=total_pts,
                  v_mps=list(vs), q_valve_mL_s=q_valve, q_rod_mL_s=q_rod))