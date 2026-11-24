# -*- coding: utf-8 -*-
"""
流固耦合内核：Q → w → P → w′ 定点迭代（可复用、无 Web 依赖）
=============================================================
被三处复用：
  * `backend/service.py`：HTTP 接口 `/api/iterate`、`/api/fv-curve`
  * `ShimTuner/fv_curve.py`：总项目命令行（跨两个 solver）
  * `couple_demo.py`：最小演示

单点迭代（`iterate_point`）：
  1. 间隙场 h(r) = h_deck − w(r)
  2. FlowSolver: 给定 Q 与 h(r) → p(r)（含窗口射流压力包）
  3. DeflectionSolver: 以 −(p−p_back) 为阀片底面载荷 → w_P
  4. w ← w + α·clip(w_P − w, ±Δmax)，直到 max|Δw| < tol

曲线（`fv_curve`）：沿速度序列逐点求解，用上一点收敛的 w 热启动。
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFL = os.path.abspath(os.path.join(_HERE, "..", "DeflectionSolver"))
# 本目录优先；DeflectionSolver 追加到末尾，避免其 backend/ 包遮蔽 FlowSolver/backend
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _DEFL not in sys.path:
    sys.path.append(_DEFL)

import channel_flow as cf                       # noqa: E402
from shim_layered import LayeredStack           # noqa: E402
from shim_physics_core import ShimLayer         # noqa: E402

__all__ = ["build_stack", "iterate_point", "fv_curve",
           "window_profile", "IterationSpec"]


class IterationSpec:
    """迭代参数（纯 Python，避免依赖 pydantic）。"""

    def __init__(self, alpha: float = 0.5, tol_um: float = 2.0,
                 max_iter: int = 25, relax_step_um: float = 150.0,
                 nl: bool = False, n_radial: int = 16, n_theta: int = 12,
                 grid: int = 240, C_d: float = 0.70, C_f: float = 0.30,
                 window_load: bool = False, window_contrast: float | None = None,
                 sector_reduction: bool = True,
                 sector_load: bool | None = None,
                 sector_scale: bool | None = None,
                 nl_tol: float = 1e-6):
        self.alpha = float(alpha)
        self.tol_um = float(tol_um)
        self.max_iter = int(max_iter)
        self.relax_step_um = float(relax_step_um)
        self.nl = bool(nl)
        self.n_radial = int(n_radial)
        self.n_theta = int(n_theta)
        self.grid = int(grid)
        self.C_d = float(C_d)
        self.C_f = float(C_f)
        # 窗口分布载荷（v2）：周向平滑调制 W(θ) + N 个圆形射流冲击压强叠加。
        # 旧参数 sector_load 作为兼容别名（旧的"阶跃掩膜"已删除）。
        self.window_load = bool(window_load) or bool(sector_load)
        self.window_contrast = (None if window_contrast is None
                                else float(window_contrast))
        self.sector_load = self.window_load          # 兼容旧字段（废弃）
        self.sector_scale = (True if sector_scale is None
                             else bool(sector_scale))  # 废弃，仅保留
        # 扇区降维：N 重周期载荷（N 个均布窗口/轴对称）只解 1/N 扇区 + 周期边界
        self.sector_reduction = bool(sector_reduction)
        # vK Newton 内层相对残差容差（耦合中间步可放宽到 1e-4 提速）
        self.nl_tol = float(nl_tol)

    def n_theta_eff(self) -> int:
        """分布载荷要分辨射流斑（σ≈w_port/2）：自动把 nθ 抬到 ≥48."""
        if self.window_load:
            return max(self.n_theta, 48)
        return self.n_theta

    def as_dict(self) -> dict:
        return dict(alpha=self.alpha, tol_um=self.tol_um,
                    max_iter=self.max_iter, relax_step_um=self.relax_step_um,
                    nl=self.nl, n_radial=self.n_radial,
                    n_theta=self.n_theta, n_theta_eff=self.n_theta_eff(),
                    grid=self.grid, C_d=self.C_d, C_f=self.C_f,
                    window_load=self.window_load,
                    window_contrast=self.window_contrast,
                    sector_reduction=self.sector_reduction,
                    nl_tol=self.nl_tol)


def sector_plan(piston, spec: IterationSpec):
    """返回 (sector, n_theta_per_sector)；sector=1 表示不降维（整环）.

    条件：n_theta_eff 能被窗口数 N 整除，且每扇区 ≥3 个周向单元。
    """
    n_theta = spec.n_theta_eff()
    if getattr(spec, "sector_reduction", True):
        n_ports = int(piston.n_ports)
        if (n_ports >= 2 and n_theta % n_ports == 0
                and n_theta // n_ports >= 3):
            return n_ports, n_theta // n_ports
    return 1, n_theta


def build_stack(layers_mm, piston, spec: IterationSpec) -> LayeredStack:
    """按阀片堆叠（mm）建分层模型；clamp = 活塞落座半径.

    扇区降维：载荷是 N 重周期（N 个均布窗口；轴对称载荷同样成立）时，
    只解 1/N 扇区 + 周期边界，DOF 降为 1/N；等效全环分辨率 = n_theta_eff。
    要求 n_theta_eff 能被 N 整除且每扇区 ≥3 个周向单元，否则退回整环。
    """
    # FlowSolver 约定：layers_mm[0] = 贴活塞的第 1 片（迎油面），向上依次；
    # LayeredStack 约定：layers[0] = 最上层（离活塞最远）。
    # 因此这里反转层序，并把载荷施加到反转后的最底层（= 用户第 1 片）。
    layers_std = [ShimLayer(float(a), float(b), float(t))
                  for a, b, t in layers_mm]
    layers = list(reversed(layers_std))
    sector, n_theta = sector_plan(piston, spec)
    return LayeredStack(layers, n_elem_base_radial=spec.n_radial,
                        n_theta=n_theta,
                        clamp_radius_mm=piston.seat_radius_mm,
                        nl=spec.nl, sector=sector,
                        load_layer=len(layers) - 1)


def window_profile(piston, contrast=None):
    """周向平滑窗口权重 W(θ)（均值 1、处处为正、无阶跃）.

    物理含义：油从 N 个窗口进入 deck 间隙后沿周向扩散，压力在窗口中心最高、
    窗间最低，但窗间不会掉到 0（油是连通的）。用 N 次谐波表示：

        W(θ) = 1 + c·cos(Nθ),    c = window_contrast（默认 1−覆盖率）

    返回 (profile_fn, coverage, contrast)。
    """
    n = int(piston.n_ports)
    coverage = (n * piston.port_width_mm * 1e-3
                / (2.0 * np.pi * piston.r_port_m()))
    coverage = float(min(max(coverage, 1e-6), 1.0))
    c = (1.0 - coverage) if contrast is None else float(contrast)
    c = float(min(max(c, 0.0), 0.95))

    def prof(theta):
        th = np.asarray(theta, dtype=float)
        return 1.0 + c * np.cos(n * th)

    return prof, coverage, c


def _jet_spots(r, theta, piston, p_jet, sigma_c, sigma_r):
    """N 个窗口射流在阀片上的**椭圆（默认圆形）**冲击压强分布（周期叠加）.

    每个窗口一个高斯斑：p_k = p_jet·exp(−½[(Δr/σ_r)² + (rΔθ/σ_c)²])；
    σ_c = 周向宽度/2，σ_r = 径向宽度/2 —— 因此圆形过孔（σ_c=σ_r）与
    长条窗口会给出不同的冲击足迹。多个斑叠加在流场压力之上（不删除任何区域）。
    """
    r = np.asarray(r, dtype=float)
    th = np.asarray(theta, dtype=float)
    sigma_c = max(float(sigma_c), 1e-12)
    sigma_r = max(float(sigma_r), 1e-12)
    if p_jet <= 0.0:
        return np.zeros_like(r)
    n = int(piston.n_ports)
    rp = piston.r_port_m()
    ang = th[..., None] - (2.0 * np.pi / n) * np.arange(n)
    ang = (ang + np.pi) % (2.0 * np.pi) - np.pi      # 最小角差
    d2 = ((r[..., None] - rp) / sigma_r) ** 2 \
        + ((r[..., None] * ang) / sigma_c) ** 2
    return (p_jet * np.exp(-0.5 * d2)).sum(axis=-1)


def _pressure_fn(res_flow, piston, p_back, spec: IterationSpec | None = None):
    """阀片底面载荷 p_load(r,θ)（负号 = 顶开）.

    ring（默认）：轴对称径向分布 p(r)，射流为整圈高斯环；
    window_load ：基础流场压力 p(r) + 周向**平滑**调制 W(θ)（无阶跃、不删除），
                 射流改为 N 个**圆形高斯冲击斑**叠加在压力场上。
    """
    r_port = piston.r_port_m()
    r_out = piston.r_outer_m()
    r_ch, p_ch = res_flow.r_m, res_flow.p_pa
    win = bool(spec is not None and spec.window_load)
    if win:
        prof, coverage, contrast = window_profile(piston, spec.window_contrast)
        # 周向非均匀性沿径向向下游衰减（油在间隙内逐渐铺开）
        Lc = max(r_out - r_port, 1e-9)
    sigma = res_flow.jet_sigma

    def fn(r_m, theta):
        r = np.asarray(r_m, dtype=float)
        th = np.asarray(theta, dtype=float)
        land = r < r_port
        p_raw = np.where(land, res_flow.p_up,
                         np.interp(r, r_ch, p_ch, left=res_flow.p_up,
                                   right=p_back))
        if not win:
            bump = res_flow.p_jet * np.exp(-((r - r_port) / sigma) ** 2)
            return -(p_raw - p_back + bump)
        # v2：基础压力 × 周向平滑调制（均值 1）+ 圆形射流斑叠加
        W = prof(th)
        decay = np.where(land, 1.0, np.exp(-(r - r_port) / Lc))
        mod = 1.0 + (W - 1.0) * decay
        base = p_back + (p_raw - p_back) * mod
        sigma_r = 0.5 * piston.port_radial_mm * 1e-3
        jet = _jet_spots(r, th, piston, res_flow.p_jet, sigma, sigma_r)
        return -(base - p_back + jet)
    return fn


def _stack_w(stack: LayeredStack) -> np.ndarray:
    # 流通间隙由"贴活塞第 1 片"（反转后的 load_layer）决定
    li = int(getattr(stack, "load_layer", 0))
    return np.array([stack.w_matrix(li)[i].mean() for i in range(stack.n_r)])


def iterate_point(v_mps: float, piston, fluid, stack: LayeredStack,
                  spec: IterationSpec, w0: np.ndarray | None = None) -> dict:
    """单点闭环迭代；返回 w / 最终流场 / 历史 / 收敛信息."""
    r_shim = stack.r_nodes
    w = np.zeros_like(r_shim) if w0 is None else np.asarray(w0).copy()
    kin = cf.rod_flows(v_mps, piston)
    Q = kin["Q_valve_m3s"]                 # 通过活塞阀的总流量（主阀 + 常通孔）
    Q_rod = kin["Q_rod_m3s"]               # 杆侵入油缸的体积速度
    p_back = fluid.p_back_MPa * 1e6
    step0 = spec.relax_step_um * 1e-6
    step = step0
    hist: list[dict] = []
    converged = False
    res = None
    t0 = time.perf_counter()

    for it in range(1, spec.max_iter + 1):
        gap = cf.ArrayGap(r_shim, -w, h_deck_m=piston.h_deck_m())
        # 常通孔并联：总流量按压力自动拆分（常通孔几何固定、不随 w 变化）
        res = cf.solve_split(Q, piston, fluid, gap, n_grid=spec.grid,
                             C_d=spec.C_d, C_f=spec.C_f)
        stack.set_pressure(_pressure_fn(res, piston, p_back, spec))
        if spec.nl:
            # vK 非线性：从上一迭代位移场热启动，后续迭代单步载荷即可
            u0 = getattr(stack, "u", None) if it > 1 else None
            try:
                stack.solve(1.0, n_steps=(1 if u0 is not None else 4),
                            U0=u0, tol=spec.nl_tol)
            except RuntimeError:
                stack.solve(1.0, n_steps=4, tol=spec.nl_tol)
        else:
            stack.solve(1.0)
        w_new = _stack_w(stack)
        raw = w_new - w
        clipped = bool(np.any(np.abs(raw) > step))
        delta = np.clip(raw, -step, step)
        d = float(np.abs(delta).max())
        # 自适应松弛：残差稳定下降时逐步加大 α；回升时回退（外层定点加速）
        alpha = spec.alpha
        if hist:
            ratio = d / max(hist[-1]["_d_m"], 1e-18)
            if ratio < 0.8:
                alpha = min(0.85, alpha + 0.05)
            elif ratio > 1.05:
                alpha = max(0.2, alpha * 0.8)
        w = w + alpha * delta
        # 信赖域扩张：持续被截断且残差未回升时放大步长（高速工况少迭代）
        if clipped and (not hist or d <= hist[-1]["residual_um"] * 1e-6 + 1e-18):
            step = min(step * 1.6, step0 * 4.0)
        hist.append(dict(iter=it, residual_um=d * 1e6, _alpha=alpha,
                         F_N=res.F_damper, dp_MPa=res.dp_total / 1e6,
                         h_port_um=res.h_port * 1e6,
                         w_tip_um=float(w[-1]) * 1e6,
                         Q_bypass_m3s=res.Q_bypass_m3s,
                         _d_m=d))
        if d < spec.tol_um * 1e-6:
            converged = True
            break

    # 用收敛后的 w 再算一次流场作为最终结果
    gap = cf.ArrayGap(r_shim, -w, h_deck_m=piston.h_deck_m())
    fin = cf.solve_split(Q, piston, fluid, gap, n_grid=spec.grid,
                         C_d=spec.C_d, C_f=spec.C_f)
    for h in hist:
        h.pop("_d_m", None)
        h.pop("_alpha", None)
    return dict(v_mps=float(v_mps), Q_m3s=Q, Q_rod_m3s=Q_rod, kin=kin,
                w=w, res=fin, history=hist,
                converged=converged, iterations=len(hist),
                residual_um=hist[-1]["residual_um"],
                elapsed_ms=(time.perf_counter() - t0) * 1e3)


def fv_curve(velocities, piston, fluid, layers_mm, spec: IterationSpec,
             direction: str = "compression") -> dict:
    """v–F 曲线：逐速度点迭代，热启动；返回点集与汇总.

    direction 只用于标注（同一阀片堆叠时压缩/回弹的流量与刚度对称）；
    若要真实的两侧不同堆叠，调用 `rod_curve(..., layers_rebound=...)`.
    """
    t0 = time.perf_counter()
    stack = build_stack(layers_mm, piston, spec)
    w = np.zeros_like(stack.r_nodes)
    points = []
    for v in velocities:
        pt = iterate_point(v, piston, fluid, stack, spec, w0=w)
        w = pt["w"]                      # 热启动
        fin = pt["res"]
        points.append(dict(
            direction=direction,
            v_mps=pt["v_mps"], Q_m3s=pt["Q_m3s"],
            Q_valve_m3s=pt["Q_m3s"], Q_rod_m3s=pt["Q_rod_m3s"],
            Q_main_m3s=fin.Q_m3s, Q_bypass_m3s=fin.Q_bypass_m3s,
            A_bypass_mm2=fin.A_bypass_m2 * 1e6,
            F_N=fin.F_damper, dp_MPa=fin.dp_total / 1e6,
            h_port_um=fin.h_port * 1e6, w_tip_um=float(pt["w"][-1]) * 1e6,
            p_jet_MPa=fin.p_jet / 1e6, A_th_mm2=fin.A_th * 1e6,
            iterations=pt["iterations"], residual_um=pt["residual_um"],
            converged=pt["converged"], elapsed_ms=pt["elapsed_ms"],
            warnings=list(fin.warnings)))
    return dict(points=points, layers=layers_mm, spec=spec.as_dict(),
                direction=direction,
                elapsed_ms=(time.perf_counter() - t0) * 1e3,
                n_converged=sum(1 for p in points if p["converged"]))


def rod_curve(velocities, piston, fluid, layers_compression, spec,
              layers_rebound=None) -> dict:
    """活塞杆速度 → 阻尼力：压缩/回弹两条分支（可各自给定阀片堆叠）.

    流量由杆速唯一决定：
        Q_valve = (A_p − A_rod)·v     （通过活塞阀，本求解器输入）
        Q_rod   = A_rod·v             （杆侵入油缸的体积速度）
    """
    t0 = time.perf_counter()
    branches = {}
    if layers_compression:
        branches["compression"] = fv_curve(
            velocities, piston, fluid, layers_compression, spec,
            direction="compression")
    if layers_rebound:
        branches["rebound"] = fv_curve(
            velocities, piston, fluid, layers_rebound, spec,
            direction="rebound")
    vs = list(velocities) if len(velocities) else [0.0]
    kin = cf.rod_flows(max(vs), piston)
    return dict(branches=branches, kinematics=kin,
                elapsed_ms=(time.perf_counter() - t0) * 1e3)
