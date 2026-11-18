# -*- coding: utf-8 -*-
"""
定常流道 → 压力场求解器 (v1)
============================
给定**挠度场** `w(r)`（来自 DeflectionSolver）与**总流量** `Q`（或活塞速度 v），
求阀片/活塞面之间流道的**稳态压力场** `p(r)` 与阻尼力 `F`。
服务于最终 Q→w→P→w′ 定点迭代器（见 ShimTunerPhysics.md「模拟逻辑设计」）。

物理图像（轴对称、不可压、定常）:

    Q ──▶ 活塞窗口(射流) ──▶ 阀片-活塞面截流面积 A_th ──▶ 径向间隙流道 h(r) ──▶ 外缘背压

主导阻尼机制（v1，按项目要求）:

1. **截流面积**（阀片底面与活塞面之间，阀片抬升形成的"帘幕"面积）
       A_th = N · w_port · h(r_port),      h(r) = h_deck + w(r)
   局部收缩损失:  Δp_c = ρ Q² / (2 C_d² A_th²)

2. **射流效应**（油束经窗口冲击阀片，动量直接传给阀片）
       F_jet = C_f · ρ Q² / A_th          (Lang 1977, C_f ≈ 0.3)

3. 径向间隙流的 **黏性 + 惯性** 压力梯度（次要，但小间隙/大直径时不可忽略）
       u(r) = Q / (2π r h)
       dp/dr = −12 μ u / h² + ρ u² (1/r + h′/h)

   外缘 p(r_out) = p_back；上游 p_up = p(r_port) + Δp_c。

输出:

* 压力场 `p(r)`、间隙 `h(r)`、速度 `u(r)`；
* 阻尼力 `F_damper = (p_up − p_back) · A_eff`（A_eff = 活塞面积 − 杆面积）；
* 阀片受载 `F_shim = ∫(p − p_back) dA + F_jet`（供挠度求解器使用）；
* 损失分解（截流 / 黏性 / 惯性）、Re、告警（空化、湍流、间隙过小）。

单位: 内部 SI（m, Pa, kg/m³, Pa·s）；对外接口用 mm / MPa / cSt。
自检: python -X utf8 channel_flow.py
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from typing import Sequence

import numpy as np

# NumPy ≥2.0 把 trapz 改名为 trapezoid, 且旧名已移除
_trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz  # noqa: B010

__all__ = ["PistonSpec", "FluidSpec", "PowerLawGap", "ArrayGap",
           "FlowResult", "solve_point", "solve_split", "solve_curve",
           "rod_flows", "DEMO_PISTON", "DEMO_FLUID", "demo_piston"]


# ============================================================
# 1. 输入结构
# ============================================================
@dataclass(frozen=True)
class PistonSpec:
    """活塞/阀体几何（mm）。默认值是一组典型 MX 减震器活塞数据。"""
    d_piston_mm: float = 46.0        # 活塞直径
    d_rod_mm: float = 18.0           # 活塞杆直径
    n_ports: int = 6                 # 活塞窗口数
    port_width_mm: float = 8.0       # 每个窗口的周向宽度（弧长）
    port_radial_mm: float = 4.0      # 每个窗口的径向宽度（径向尺寸）
    port_radius_mm: float = 16.0     # 窗口中心半径
    seat_radius_mm: float = 9.0      # 阀片压紧(夹持)半径（界面以夹持直径输入）
    deck_height_mm: float = 0.10     # 阀片底面与活塞面的初始间隙 h_deck
    shim_outer_mm: float = 21.0      # 最大阀片半径（界面以最大阀片直径输入）
    # ---- 定常旁通孔（常通孔，几何固定、与阀片挠度/迭代无关）----
    bypass_d_mm: float = 0.0         # 单个常通孔直径（0 = 无）
    bypass_n: int = 0                # 常通孔个数
    bypass_Cd: float = 0.62          # 常通孔流量系数

    def validate(self) -> None:
        if not (0 < self.d_rod_mm < self.d_piston_mm):
            raise ValueError("要求 0 < 杆径 < 活塞直径")
        if self.n_ports < 1:
            raise ValueError("窗口数必须 ≥ 1")
        if not (0 < self.seat_radius_mm < self.port_radius_mm < self.shim_outer_mm):
            raise ValueError("半径需满足 夹持半径 < 窗口中心半径 < 最大阀片半径"
                             "（界面以夹持直径/最大阀片直径输入）")
        if not (0 < self.port_width_mm < 2 * math.pi * self.port_radius_mm):
            raise ValueError("窗口周向宽度超过所在圆周周长")
        if self.port_radial_mm <= 0:
            raise ValueError("窗口径向宽度必须 > 0")
        if self.deck_height_mm <= 0:
            raise ValueError("初始间隙 h_deck 必须 > 0")
        if self.bypass_n < 0 or self.bypass_d_mm < 0:
            raise ValueError("常通孔个数/直径不能为负")
        if self.bypass_n > 0 and self.bypass_d_mm <= 0:
            raise ValueError("常通孔个数 > 0 时直径必须 > 0")
        if not (0 < self.bypass_Cd <= 1):
            raise ValueError("常通孔流量系数需在 (0, 1]")

    # ---- 派生量 (SI) ----
    def area_effective_m2(self) -> float:
        """活塞阀过流有效面积 = 活塞面积 − 杆面积 (A_p − A_rod).

        压缩行程: 活塞阀流量 Q_valve = (A_p − A_rod)·v;
        杆侵入油缸排开的体积速度 Q_rod = A_rod·v（单筒进入气室 / 双筒经底阀回油）.
        """
        d_p, d_r = self.d_piston_mm * 1e-3, self.d_rod_mm * 1e-3
        return 0.25 * math.pi * (d_p ** 2 - d_r ** 2)

    def r_port_m(self) -> float:
        return self.port_radius_mm * 1e-3

    def r_seat_m(self) -> float:
        return self.seat_radius_mm * 1e-3

    def r_outer_m(self) -> float:
        return self.shim_outer_mm * 1e-3

    # ---- 界面口径（直径 / 角度）----
    def clamp_diameter_mm(self) -> float:
        """夹持直径 = 2×夹持半径（界面输入用直径）."""
        return 2.0 * self.seat_radius_mm

    def shim_outer_diameter_mm(self) -> float:
        """最大阀片直径 = 2×最大阀片半径（界面输入用直径）."""
        return 2.0 * self.shim_outer_mm

    def port_arc_deg(self) -> float:
        """单个窗口的周向张角（度）：弧长 / 中心半径."""
        return math.degrees(self.port_width_mm / self.port_radius_mm)

    def h_deck_m(self) -> float:
        return self.deck_height_mm * 1e-3

    # ---- 运动学面积（杆速 ↔ 流量）----
    def area_piston_m2(self) -> float:
        """活塞全截面面积 π/4·D²."""
        return 0.25 * math.pi * (self.d_piston_mm * 1e-3) ** 2

    def area_rod_m2(self) -> float:
        """活塞杆截面积 π/4·d_rod² —— 杆侵入油缸的体积速度 = A_rod·v."""
        return 0.25 * math.pi * (self.d_rod_mm * 1e-3) ** 2

    def area_bypass_m2(self) -> float:
        """常通孔总面积 = n·π/4·d²（固定几何，与阀片挠度无关）.

        常通孔与"窗口-阀片间隙"并联：总流量 Q_valve 分成
        Q_main（经阀片间隙，受迭代影响）+ Q_bypass（经常通孔，只由 Δp 决定）。
        """
        if self.bypass_n <= 0 or self.bypass_d_mm <= 0:
            return 0.0
        return (self.bypass_n * 0.25 * math.pi
                * (self.bypass_d_mm * 1e-3) ** 2)


# ============================================================
# 油液黏度：ASTM D341 (Walther) + ASTM D2270 (VI)
# ============================================================
def _walther_y(nu_cst: float) -> float:
    return math.log10(math.log10(nu_cst + 0.7))


def _walther_nu(y: float) -> float:
    return 10.0 ** (10.0 ** y) - 0.7


def kv_at_temperature(kv40: float, kv100: float, T_C: float) -> float:
    """工作温度下的运动黏度 (cSt)，ASTM D341 (Walther).

    log10(log10(ν+0.7)) = A − B·log10(T[K])；由 (40°C, KV40) 与 (100°C, KV100)
    两点确定 A、B，再外推到工作温度 T。
    """
    for name, v in (("KV40", kv40), ("KV100", kv100)):
        if not (v > 0.3):
            raise ValueError(f"{name} 必须 > 0.3 cSt（ASTM D341 要求 ν+0.7>1）")
    if abs(kv40 - kv100) < 1e-9:
        raise ValueError("KV40 与 KV100 不能相等")
    T_K = float(T_C) + 273.15
    if T_K <= 1.0:
        raise ValueError("工作温度 T 无效")
    x40 = math.log10(40.0 + 273.15)
    x100 = math.log10(100.0 + 273.15)
    y40, y100 = _walther_y(kv40), _walther_y(kv100)
    B = (y40 - y100) / (x100 - x40)
    A = y40 + B * x40
    nu = _walther_nu(A - B * math.log10(T_K))
    if not math.isfinite(nu) or nu <= 0:
        raise ValueError("按 ASTM D341 外推的黏度无效，请检查 KV40/KV100/T")
    return float(nu)


def _vi_LH(kv100: float):
    """ASTM D2270: 同 KV100 的 0 VI / 100 VI 参考油在 40°C 的黏度 L/H."""
    y = float(kv100)
    L = 0.8353 * y * y + 14.67 * y - 216.0
    H = 0.1684 * y * y + 11.85 * y - 97.0
    return L, H


def viscosity_index(kv40: float, kv100: float) -> float:
    """由 KV40/KV100 计算黏度指数 VI（ASTM D2270，适用 KV100 2–70 cSt）."""
    if not (2.0 <= kv100 <= 70.0):
        raise ValueError("VI 公式适用 KV100 2–70 cSt")
    L, H = _vi_LH(kv100)
    if abs(L - H) < 1e-12:
        raise ValueError("L≈H，无法计算 VI")
    return float((L - kv40) / (L - H) * 100.0)


@dataclass(frozen=True)
class FluidSpec:
    rho: float = 850.0               # kg/m³
    cSt: float = 15.0                # 运动黏度 cSt (= mm²/s)，KV 曲线未启用时使用
    p_vapor_kPa: float = 0.0         # 蒸气压（表压, 0 = 忽略空化）
    p_back_MPa: float = 0.0          # 外缘背压（表压）
    # 工作温度 + 黏度曲线：KV40 / KV100 / VI（0 = 未提供）
    T_C: float = 40.0
    KV40: float = 0.0
    KV100: float = 0.0
    VI: float = 0.0

    def resolved_kv100(self) -> float:
        """KV100（VI 不能唯一反解 KV100，故必须直接给 KV100）."""
        return float(self.KV100) if self.KV100 > 0 else 0.0

    def kv_at_T(self) -> float:
        """工作温度 T 下的运动黏度 cSt（KV 曲线未启用时返回 cSt）."""
        if self.KV40 > 0 and self.KV100 > 0:
            return kv_at_temperature(self.KV40, self.KV100, self.T_C)
        return float(self.cSt)

    def effective_cSt(self) -> float:
        return self.kv_at_T()

    def mu(self) -> float:
        """动力黏度 Pa·s = ν(T)[m²/s] · ρ."""
        return self.effective_cSt() * 1e-6 * self.rho

    def describe_viscosity(self) -> str:
        if self.KV40 > 0 and self.KV100 > 0:
            vi_calc = viscosity_index(self.KV40, self.KV100)
            vi_txt = (f"输入 VI={self.VI:g}, 反算 VI={vi_calc:.0f}"
                      if self.VI != 0 else f"VI={vi_calc:.0f}")
            return (f"KV40={self.KV40:g}, KV100={self.KV100:.2f} ({vi_txt}) → "
                    f"T={self.T_C:g}°C 时 ν={self.kv_at_T():.3f} cSt")
        return f"ν={self.cSt:g} cSt（手动给定）"

    def validate(self) -> None:
        if self.rho <= 0 or self.cSt <= 0:
            raise ValueError("密度与黏度必须 > 0")
        if not (-60.0 <= self.T_C <= 300.0):
            raise ValueError("工作温度 T 需在 -60~300 °C")
        if self.KV40 > 0:
            if self.KV40 <= 0.3:
                raise ValueError("KV40 必须 > 0.3 cSt")
            if self.KV100 <= 0:
                raise ValueError("给 KV40 时必须同时给 KV100"
                                 "（VI 不能唯一反解 KV100）")
            if self.KV100 <= 0.3:
                raise ValueError("KV100 必须 > 0.3 cSt")
            if self.KV100 > 0 and not (2.0 <= self.KV100 <= 70.0):
                raise ValueError("KV100 需在 2–70 cSt（VI 公式适用范围）")
            if abs(self.VI) > 400:
                raise ValueError("VI 超范围")
        elif self.KV100 > 0 or self.VI != 0:
            raise ValueError("给 KV100/VI 时必须同时给 KV40")


def rod_flows(v_mps: float, piston: PistonSpec) -> dict:
    """杆速 → 体积流量拆分（运动学，压力场求解的输入）.

        Q_rod   = A_rod · v          活塞杆侵入油缸的体积速度
                                     （单筒: 进入蓄能/气室；双筒: 经底阀回油）
        Q_valve = (A_p − A_rod) · v  通过活塞阀的流量 = 本求解器的 Q

    两者之比由杆/活塞直径唯一确定，和阀片挠度无关。
    """
    A_p = piston.area_piston_m2()
    A_rod = piston.area_rod_m2()
    A_eff = piston.area_effective_m2()
    v = max(float(v_mps), 0.0)
    return dict(A_p_m2=A_p, A_rod_m2=A_rod, A_eff_m2=A_eff,
                Q_rod_m3s=A_rod * v, Q_valve_m3s=A_eff * v, v_mps=v)


# ============================================================
# 2. 挠度场（间隙形状）输入
# ============================================================
class PowerLawGap:
    """演示用挠度场:  w(r) = lift · ((r − r_seat)/(r_outer − r_seat))^power.

    lift 为阀片外缘抬升量 (m)。h(r) = h_deck + w(r)。
    """

    def __init__(self, lift_m: float, r_seat_m: float, r_outer_m: float,
                 power: float = 2.0, h_deck_m: float = 0.0):
        if lift_m < 0:
            raise ValueError("lift 必须 ≥ 0")
        if not (0 < r_seat_m < r_outer_m):
            raise ValueError("半径非法")
        self.lift_m, self.r_seat, self.r_outer = lift_m, r_seat_m, r_outer_m
        self.power, self.h_deck = float(power), float(h_deck_m)

    def __call__(self, r_m: np.ndarray) -> np.ndarray:
        x = np.clip((np.asarray(r_m) - self.r_seat) / (self.r_outer - self.r_seat),
                    0.0, None)
        return self.h_deck + self.lift_m * x ** self.power

    def describe(self) -> str:
        return (f"幂律挠度场: h = {self.h_deck*1e3:.4g} + "
                f"{self.lift_m*1e3:.4g}·x^{self.power:g} mm")


class ArrayGap:
    """由离散挠度场（r, w 数组）插值得到间隙 h(r) = h_deck + w(r).

    r 单位 m，w 单位 m；超出范围时按端点值延拓（并给出告警由求解器处理）。
    """

    def __init__(self, r_m: Sequence[float], w_m: Sequence[float],
                 h_deck_m: float = 0.0):
        r = np.asarray(r_m, dtype=float)
        w = np.asarray(w_m, dtype=float)
        if r.ndim != 1 or w.shape != r.shape or r.size < 2:
            raise ValueError("r / w 必须是一维等长数组 (≥2 点)")
        if np.any(np.diff(r) <= 0):
            raise ValueError("r 必须严格递增")
        self.r, self.w, self.h_deck = r, w, float(h_deck_m)

    def __call__(self, r_m: np.ndarray) -> np.ndarray:
        w = np.interp(np.asarray(r_m), self.r, self.w)
        return self.h_deck + w

    def describe(self) -> str:
        return (f"离散挠度场: {self.r.size} 点, "
                f"r = {self.r[0]*1e3:.2f}–{self.r[-1]*1e3:.2f} mm, "
                f"w_max = {self.w.max()*1e3:.4f} mm")


# ============================================================
# 3. 求解
# ============================================================
@dataclass
class FlowResult:
    Q_m3s: float
    v_mps: float
    p_back: float
    p_up: float
    dp_total: float
    dp_curtain: float
    dp_viscous: float
    dp_inertia: float
    A_th: float
    h_port: float
    v_port: float            # 截流处平均流速 Q/A_th
    p_jet: float             # 射流滞止压力 0.5·ρ·v_port²
    jet_sigma: float         # 射流冲击的径向宽度（建议压力包 σ = w_port/2）
    F_damper: float
    F_jet: float
    F_shim: float
    Re: float
    r_m: np.ndarray
    h_m: np.ndarray
    p_pa: np.ndarray
    u_mps: np.ndarray
    warnings: list[str] = dc_field(default_factory=list)
    # ---- 常通孔流量拆分（默认 0 = 全部经主阀，兼容旧结果）----
    Q_total_m3s: float = 0.0         # 活塞阀总流量（主阀 + 常通孔）
    Q_bypass_m3s: float = 0.0        # 经常通孔的分流量（固定几何）
    A_bypass_m2: float = 0.0         # 常通孔总面积

    def summary(self) -> str:
        s = (f"Q = {self.Q_m3s*1e3:.4f} L/s | v = {self.v_mps:.3f} m/s | "
             f"Δp = {self.dp_total/1e6:.4f} MPa | F = {self.F_damper:.2f} N | "
             f"截流 {self.dp_curtain/self.dp_total*100:.1f}% | "
             f"射流 {self.F_jet:.2f} N | Re = {self.Re:.0f}")
        if self.A_bypass_m2 > 0:
            s += (f" | 常通孔 {self.Q_bypass_m3s*1e3:.4f} L/s "
                  f"({self.Q_bypass_m3s/max(self.Q_total_m3s, 1e-18)*100:.0f}%)")
        return s


def _bypass_flow(dp_pa: float, A_b: float, C_d: float, rho: float) -> float:
    """常通孔（薄壁孔）流量: Q = C_d·A·sqrt(2Δp/ρ)."""
    if A_b <= 0 or dp_pa <= 0:
        return 0.0
    return C_d * A_b * math.sqrt(2.0 * dp_pa / rho)


def solve_split(Q_total_m3s: float, piston: PistonSpec, fluid: FluidSpec,
                gap, n_grid: int = 400, C_d: float = 0.70,
                C_f: float = 0.30, h_min_m: float = 1e-6) -> FlowResult:
    """常通孔与主阀并联：总流量按压力自动分配.

    给定总流量 Q_total = Q_main + Q_bypass，求分流量与压力场：
      * 主阀路径：`solve_point(Q_main)` → Δp_main(Q_main)（含截流/黏性/惯性）
      * 常通孔：Q_bypass = C_d,b·A_b·sqrt(2·Δp/ρ)，几何固定、与挠度无关
      * 并联要求：Q_main + Q_bypass(Δp(Q_main)) = Q_total

    因 Δp(Q_main) 单调递增，方程单调，用 brentq（无 scipy 时退化为二分）。
    """
    piston.validate()
    fluid.validate()
    Q_total = float(Q_total_m3s)
    if Q_total < 0:
        raise ValueError("Q 必须 ≥ 0")
    A_b = piston.area_bypass_m2()
    if A_b <= 0:
        res = solve_point(Q_total, piston, fluid, gap, n_grid=n_grid,
                          C_d=C_d, C_f=C_f, h_min_m=h_min_m)
        res.Q_total_m3s = Q_total
        res.Q_bypass_m3s = 0.0
        res.A_bypass_m2 = 0.0
        return res

    rho = fluid.rho
    Cd_b = piston.bypass_Cd
    kw = dict(n_grid=n_grid, C_d=C_d, C_f=C_f, h_min_m=h_min_m)

    def surplus(Q_main: float) -> float:
        r = solve_point(Q_main, piston, fluid, gap, **kw)
        return Q_main + _bypass_flow(r.dp_total, A_b, Cd_b, rho) - Q_total

    lo, hi = 0.0, Q_total
    if surplus(hi) < 0:            # 极端：常通孔几乎堵死，主阀无法在 Q_total 内平衡
        Q_main = Q_total
    else:
        try:
            from scipy.optimize import brentq
            Q_main = float(brentq(surplus, lo, hi, xtol=max(Q_total * 1e-12, 1e-16),
                                  rtol=1e-12, maxiter=200))
        except Exception:          # noqa: BLE001 - 无 scipy 或数值异常时二分
            for _ in range(120):
                mid = 0.5 * (lo + hi)
                if surplus(mid) > 0:
                    hi = mid
                else:
                    lo = mid
                if hi - lo <= max(Q_total * 1e-12, 1e-16):
                    break
            Q_main = 0.5 * (lo + hi)

    res = solve_point(Q_main, piston, fluid, gap, **kw)
    res.Q_total_m3s = Q_total
    res.Q_bypass_m3s = max(Q_total - Q_main, 0.0)
    res.A_bypass_m2 = A_b
    res.v_mps = Q_total / piston.area_effective_m2()
    if res.Q_bypass_m3s > 0.9 * Q_total:
        res.warnings.append("常通孔承担 >90% 流量：主阀接近关闭，"
                            "阻尼几乎全部来自常通孔")
    elif res.Q_bypass_m3s > 0.5 * Q_total:
        res.warnings.append("常通孔分流 >50%：常通孔已明显削弱主阀阻尼")
    return res


def _gap_on_grid(gap, r_port: float, r_outer: float, n: int):
    r = np.linspace(r_port, r_outer, int(n))
    h = np.asarray(gap(r), dtype=float)
    if np.any(~np.isfinite(h)):
        raise ValueError("挠度场返回了非有限值")
    return r, h


def solve_point(Q_m3s: float, piston: PistonSpec, fluid: FluidSpec,
                gap, n_grid: int = 400, C_d: float = 0.70,
                C_f: float = 0.30, h_min_m: float = 1e-6) -> FlowResult:
    """给定流量 Q 与间隙场 gap(r)，求压力场与阻尼力."""
    piston.validate()
    fluid.validate()
    if Q_m3s < 0:
        raise ValueError("Q 必须 ≥ 0")

    r_port = piston.r_port_m()
    r_out = piston.r_outer_m()
    r_seat = piston.r_seat_m()
    p_back = fluid.p_back_MPa * 1e6
    A_eff = piston.area_effective_m2()
    v = Q_m3s / A_eff

    r, h_raw = _gap_on_grid(gap, r_port, r_out, n_grid)
    warn: list[str] = []
    if np.any(h_raw < h_min_m):
        warn.append(f"间隙低于 {h_min_m*1e6:.1f} µm 被截断（可能已贴合/闭死）")
    h = np.clip(h_raw, h_min_m, None)

    # ---- 径向间隙流: 黏性 + 惯性压力梯度 ----
    dh = np.gradient(h, r)
    u = Q_m3s / (2.0 * math.pi * r * h)
    mu = fluid.mu()
    visc = 12.0 * mu * u / h ** 2                    # -dp/dr 的黏性项
    inert = fluid.rho * u ** 2 * (1.0 / r + dh / h)  # 惯性项 (扩张时压升)
    dpdr = -visc + inert                             # dp/dr

    # 从外缘 (p_back) 向内积分得到 p(r)
    # 向量化：p[k] = p_back − Σ_{i=k..n−2} 0.5·(dpdr[i]+dpdr[i+1])·dr[i]
    term = 0.5 * (dpdr[:-1] + dpdr[1:]) * np.diff(r)
    p = np.empty_like(r)
    p[-1] = p_back
    p[:-1] = p_back - np.cumsum(term[::-1])[::-1]
    dp_channel = float(p[0] - p_back)
    dp_visc = float(_trapz(visc, r))
    dp_inert = float(_trapz(inert, r))

    # ---- 截流面积（阀片底面 × 窗口周向宽度）----
    h_port = float(gap(np.array([r_port]))[0])
    A_th = max(piston.n_ports * piston.port_width_mm * 1e-3
               * max(h_port, 0.0), 1e-12)
    dp_curtain = fluid.rho * Q_m3s ** 2 / (2.0 * C_d ** 2 * A_th ** 2)
    v_port = Q_m3s / A_th
    p_jet = 0.5 * fluid.rho * v_port ** 2
    jet_sigma = 0.5 * piston.port_width_mm * 1e-3

    p_up = p_back + dp_channel + dp_curtain
    dp_total = p_up - p_back

    # ---- 力 ----
    # 阀片底面压力 (相对背压): 落座半径→窗口半径为上游压力, 其后为 p(r)
    n_land = max(int(round(n_grid * (r_port - r_seat) / (r_out - r_seat))), 2)
    r_land = np.linspace(r_seat, r_port, n_land)
    F_land = float(_trapz((p_up - p_back) * 2.0 * math.pi * r_land, r_land))
    F_channel = float(_trapz((p - p_back) * 2.0 * math.pi * r, r))
    F_jet = C_f * fluid.rho * Q_m3s ** 2 / A_th

    F_shim = F_land + F_channel + F_jet
    F_damper = dp_total * A_eff

    # ---- 诊断 ----
    Re = float(fluid.rho * u[0] * h[0] / mu) if mu > 0 else 0.0
    if h_port < 5e-6:
        warn.append("窗口处间隙 < 5 µm：截流面积极小，Δp 对挠度极敏感")
    if v_port > 50.0:
        warn.append(f"截流流速 {v_port:.0f} m/s 过高：不可压/层流假设失效，"
                    f"实际会出现空化与 choked flow")
    if Re > 2000:
        warn.append(f"窗口处 Re = {Re:.0f} > 2000：层流假设失效（v1 未建湍流模型）")
    if dp_inert > 0.05 * max(dp_visc, 1e-12):
        warn.append("惯性项占黏性项 >5%：流道扩张段可能出现压力恢复")
    if p.min() < fluid.p_vapor_kPa * 1e3:
        warn.append("局部压力低于蒸气压：可能空化（v1 未建空化模型）")
    if dp_total < 0:
        warn.append("总压降为负：惯性恢复过强，检查挠度场/流量")

    return FlowResult(
        Q_m3s=Q_m3s, v_mps=v, p_back=p_back, p_up=p_up, dp_total=dp_total,
        dp_curtain=dp_curtain, dp_viscous=dp_visc, dp_inertia=dp_inert,
        A_th=A_th, h_port=h_port, v_port=v_port, p_jet=p_jet,
        jet_sigma=jet_sigma,
        F_damper=F_damper, F_jet=F_jet, F_shim=F_shim,
        Re=Re, r_m=r, h_m=h_raw, p_pa=p, u_mps=u, warnings=warn)


def solve_curve(velocities_mps: Sequence[float], piston: PistonSpec,
                fluid: FluidSpec, gap, split: bool = False, **kw) -> dict:
    """F(v) 或 F(Q) 曲线: 返回 numpy 数组与逐点结果.

    split=True 时计入常通孔并联分流（solve_split），否则只走主阀。
    """
    vs = np.asarray(velocities_mps, dtype=float)
    A_eff = piston.area_effective_m2()
    F = np.zeros_like(vs)
    P = np.zeros_like(vs)
    Fjet = np.zeros_like(vs)
    results: list[FlowResult] = []
    solver = solve_split if split else solve_point
    for i, v in enumerate(vs):
        res = solver(max(v, 0.0) * A_eff, piston, fluid, gap, **kw)
        results.append(res)
        F[i], P[i], Fjet[i] = res.F_damper, res.dp_total, res.F_jet
    return dict(v_mps=vs, Q_m3s=vs * A_eff, F_N=F, dp_Pa=P, F_jet_N=Fjet,
                results=results)


# ============================================================
# 4. 演示数据与自检
# ============================================================
DEMO_PISTON = PistonSpec()
DEMO_FLUID = FluidSpec()


def demo_piston(**kw) -> PistonSpec:
    base = dict(d_piston_mm=46.0, d_rod_mm=18.0, n_ports=6,
                port_width_mm=8.0, port_radial_mm=4.0, port_radius_mm=16.0,
                seat_radius_mm=9.0, deck_height_mm=0.10, shim_outer_mm=21.0)
    base.update(kw)
    return PistonSpec(**base)


def _selftest() -> None:
    print("=" * 78)
    print("channel_flow 自检 (定常流道-压力场 v1)")
    print("=" * 78)
    pis, flu = DEMO_PISTON, DEMO_FLUID
    gap = PowerLawGap(lift_m=0.15e-3, r_seat_m=pis.r_seat_m(),
                      r_outer_m=pis.r_outer_m(), power=2.0,
                      h_deck_m=pis.h_deck_m())
    print(f"几何: D={pis.d_piston_mm} / d_rod={pis.d_rod_mm} mm, "
          f"N={pis.n_ports}×{pis.port_width_mm} mm @ r={pis.port_radius_mm} mm")
    print(f"流体: ρ={flu.rho} kg/m³, ν={flu.cSt} cSt")
    print(gap.describe())

    # ---- 1) Q=0 极限 ----
    z = solve_point(0.0, pis, flu, gap)
    print(f"[1] Q=0: Δp={z.dp_total:.3e} Pa, F={z.F_damper:.3e} N")
    assert abs(z.dp_total) < 1e-6 and abs(z.F_damper) < 1e-6

    # ---- 2) 纯黏性解析对照 (忽略惯性/截流) ----
    # 常间隙 h, 环向 Poiseuille: Δp = 12 μ Q /(2π h³) · ln(r_out/r_port)
    h0 = 0.2e-3
    flat = PowerLawGap(lift_m=0.0, r_seat_m=pis.r_seat_m(),
                       r_outer_m=pis.r_outer_m(), power=2.0, h_deck_m=h0)
    Q = 2e-5
    res = solve_point(Q, pis, flu, flat, C_d=1e6, C_f=0.0)  # 抹掉截流与射流
    mu = flu.mu()
    dp_an = 12.0 * mu * Q / (2.0 * math.pi * h0 ** 3) * math.log(pis.r_outer_m() / pis.r_port_m())
    print(f"[2] 纯黏性: 数值 Δp_channel={res.dp_viscous:.6e} Pa | 解析 {dp_an:.6e} Pa | "
          f"rel={abs(res.dp_viscous-dp_an)/dp_an:.2e}")
    assert abs(res.dp_viscous - dp_an) / dp_an < 2e-3

    # ---- 3) 截流解析对照 ----
    A_th = pis.n_ports * pis.port_width_mm * 1e-3 * gap(np.array([pis.r_port_m()]))[0]
    dp_c_an = flu.rho * Q ** 2 / (2 * 0.7 ** 2 * A_th ** 2)
    print(f"[3] 截流: Δp_c={res.dp_curtain:.6e} Pa (C_d=1e6 时≈0) | 参考 {dp_c_an:.3e}")
    assert res.dp_curtain < 1e-3 * dp_an

    # ---- 4) 射流力公式 ----
    r4 = solve_point(Q, pis, flu, gap)
    F_jet_an = 0.30 * flu.rho * Q ** 2 / r4.A_th
    print(f"[4] 射流力: {r4.F_jet:.6f} N | ρQC_f/A_th = {F_jet_an:.6f} N")
    assert abs(r4.F_jet - F_jet_an) / F_jet_an < 1e-12

    # ---- 5) 阻尼力 = Δp·A_eff ----
    print(f"[5] F_damper={r4.F_damper:.3f} N | Δp·A_eff="
          f"{r4.dp_total*pis.area_effective_m2():.3f} N")
    assert abs(r4.F_damper - r4.dp_total * pis.area_effective_m2()) < 1e-9

    # ---- 6) 压力场边界与单调性 ----
    print(f"[6] p(r_out)={r4.p_pa[-1]:.3e} (=p_back {flu.p_back_MPa*1e6:.1e}) | "
          f"p(r_port)={r4.p_pa[0]:.4e} | p_up={r4.p_up:.4e}")
    assert abs(r4.p_pa[-1] - flu.p_back_MPa * 1e6) < 1e-9
    assert np.all(np.diff(r4.p_pa) <= 1e-6), "压力应随 r 单调下降"

    # ---- 7) 曲线单调 + 网格收敛 ----
    vs = np.array([0.01, 0.05, 0.1, 0.25, 0.5])
    cur = solve_curve(vs, pis, flu, gap)
    print("[7] F(v): " + ", ".join(f"{v:.2f}→{f:.1f}N"
                                   for v, f in zip(vs, cur["F_N"])))
    assert np.all(np.diff(cur["F_N"]) > 0), "F(v) 应单调递增"
    f200 = solve_point(1e-4, pis, flu, gap, n_grid=200).dp_total
    f800 = solve_point(1e-4, pis, flu, gap, n_grid=800).dp_total
    print(f"    网格收敛: 200→800 相对差 {abs(f800-f200)/f800:.2e}")
    assert abs(f800 - f200) / f800 < 1e-3

    # ---- 8) 主导因素占比 ----
    print(f"[8] 截流流速 {r4.v_port:.1f} m/s | 射流滞止压力 "
          f"{r4.p_jet/1e6:.3f} MPa")
    print(f"    损失分解: 截流 {r4.dp_curtain/r4.dp_total*100:.1f}% | "
          f"黏性 {r4.dp_viscous/r4.dp_total*100:.1f}% | "
          f"惯性 {r4.dp_inertia/r4.dp_total*100:.1f}%")
    print(f"    射流力 {r4.F_jet:.3f} N vs 压力力 {r4.F_damper:.1f} N "
          f"({r4.F_jet/r4.F_damper*100:.2f}%) | 阀片受载 {r4.F_shim:.1f} N")
    print(f"    告警: {r4.warnings if r4.warnings else '无'}")
    # ---- 9) 常通孔并联分流 ----
    r_split0 = solve_split(Q, pis, flu, gap)
    print(f"[9] 无孔: Q={r_split0.Q_total_m3s*1e3:.5f} L/s vs 主阀 "
          f"{r4.Q_m3s*1e3:.5f} L/s | F={r_split0.F_damper:.2f} N")
    assert abs(r_split0.Q_total_m3s - Q) < 1e-15
    assert r_split0.Q_bypass_m3s == 0.0
    assert abs(r_split0.F_damper - r4.F_damper) < 1e-9 * max(1, r4.F_damper)

    p_b = demo_piston(bypass_d_mm=1.0, bypass_n=2)
    r9 = solve_split(Q, p_b, flu, gap)
    A_b = p_b.area_bypass_m2()
    qb_an = p_b.bypass_Cd * A_b * math.sqrt(2.0 * r9.dp_total / flu.rho)
    print(f"    常通孔 2×Ø1.0 mm (A_b={A_b*1e6:.3f} mm²): F={r9.F_damper:.2f} N "
          f"(主阀独行 {r4.F_damper:.2f} N) | Q_main={r9.Q_m3s*1e3:.5f} / "
          f"Q_byp={r9.Q_bypass_m3s*1e3:.5f} L/s | Δp={r9.dp_total/1e6:.4f} MPa")
    print(f"    分流守恒误差 |Q_main+Q_byp−Q|/Q = "
          f"{abs(r9.Q_m3s+r9.Q_bypass_m3s-Q)/Q:.2e} | "
          f"孔流量解析相对差 {abs(r9.Q_bypass_m3s-qb_an)/qb_an:.2e}")
    assert abs(r9.Q_m3s + r9.Q_bypass_m3s - Q) / Q < 1e-8
    assert abs(r9.Q_bypass_m3s - qb_an) / qb_an < 1e-6
    assert r9.F_damper < r4.F_damper, "常通孔应降低阻尼力"

    print("自检: 全部断言通过 ✓")


if __name__ == "__main__":
    _selftest()
