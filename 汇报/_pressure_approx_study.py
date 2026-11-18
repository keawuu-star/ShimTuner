# -*- coding: utf-8 -*-
"""压力场近似对挠度/F 的影响估计（报告附录 C 的可复现脚本）

对比四种加载方式（同一点、同一网格 20×48、力学量取绝对值比较）：
  A 轴对称径向分布 p(r)          —— 当前耦合迭代实际使用的方式
  B 力等效均布 p_eq = F_load/A   —— “压差等效均布”假设
  C1 全窗口离散（缩放保持总力）  —— 周向离散上界
  C2 仅落座区离散、流道区环向均布

用法: python -X utf8 汇报/_pressure_approx_study.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "FlowSolver"))

import channel_flow as cf      # noqa: E402
import coupling as cp          # noqa: E402
from shim_layered import LayeredStack          # noqa: E402
from shim_physics_core import ShimLayer        # noqa: E402

LAYERS = [(18.0, 42.0, 0.25), (18.0, 36.0, 0.20), (18.0, 30.0, 0.20)]
V = 0.25


def main() -> int:
    piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
    p_back = fluid.p_back_MPa * 1e6

    # 1) 得到 v=0.25 的收敛状态（压力场取自该状态）
    spec = cp.IterationSpec(n_radial=16, n_theta=12, max_iter=40)
    stack0 = cp.build_stack(LAYERS, piston, spec)
    pt = cp.iterate_point(V, piston, fluid, stack0, spec)
    fin = pt["res"]
    print(f"耦合收敛: F={fin.F_damper:.1f} N, h_port={fin.h_port*1e6:.1f} µm, "
          f"w_tip={pt['w'][-1]*1e6:.1f} µm, it={pt['iterations']}")

    p_fn = cp._pressure_fn(fin, piston, p_back)      # 负值 = 顶开

    def study_stack():
        return LayeredStack([ShimLayer(*l) for l in LAYERS],
                            n_elem_base_radial=20, n_theta=48,
                            clamp_radius_mm=piston.seat_radius_mm)

    def solve_with(fn):
        st = study_stack()
        st.set_pressure(fn)
        st.solve(1.0)
        return st.r_nodes, st.w_matrix(0)

    r, wmA = solve_with(p_fn)

    N = piston.n_ports
    half = (piston.port_width_mm * 1e-3 / 2) / piston.r_port_m()   # [rad]
    frac = N * 2 * half / (2 * math.pi)
    print(f"窗口角占比 {frac*100:.1f}%  (N={N}, 半角 "
          f"{math.degrees(half):.1f}°)")

    def mask(th):
        ph = (th + math.pi / N) % (2 * math.pi / N) - math.pi / N
        return (np.abs(ph) <= half).astype(float)

    F_load = fin.F_shim                              # ≈ ∫|p| dA
    A_loaded = math.pi * (piston.r_outer_m() ** 2 - piston.r_seat_m() ** 2)
    p_uni = F_load / A_loaded
    print(f"载荷合力 {F_load:.1f} N (F_damper={fin.F_damper:.1f} N), "
          f"力等效均布 {p_uni/1e6:.4f} MPa")

    _, wmB = solve_with(lambda r_m, th: -p_uni)

    def p_arr(r_m, th):
        return np.asarray(p_fn(r_m, th), float)

    def fn_C1(r_m, th):
        return p_arr(r_m, th) * mask(th) * (1.0 / frac)

    def fn_C2(r_m, th):
        r = np.asarray(r_m, float)
        th = np.asarray(th, float)
        p = p_arr(r, th)
        m = mask(th)
        out = p.copy()
        land = r < piston.r_port_m()
        out[land] = p[land] * m[land] * (1.0 / frac)
        return out

    _, wmC1 = solve_with(fn_C1)
    _, wmC2 = solve_with(fn_C2)

    print("\n外缘抬升 [µm]（mean / max / min over θ）:")
    for name, wm in (("A 轴对称(当前)", wmA), ("B 均布(力等效)", wmB),
                     ("C1 全窗口离散", wmC1), ("C2 仅落座区离散", wmC2)):
        lf = -wm[-1]
        print(f"  {name:<16} {lf.mean()*1e6:8.1f} / {lf.max()*1e6:8.1f} / "
              f"{lf.min()*1e6:8.1f}")

    tipA = -wmA[-1].mean()
    print("\n相对 A 的外缘均值偏差:")
    for name, wm in (("B 均布", wmB), ("C1 全离散", wmC1),
                     ("C2 落座区离散", wmC2)):
        tip = -wm[-1].mean()
        print(f"  {name:<12} {abs(tip-tipA)/tipA*100:5.2f}%")

    i_rp = int(np.argmin(np.abs(r - piston.r_port_m())))
    print("\nr_port 处开度 [µm]（mean / min / max over θ）:")
    for name, wm in (("A", wmA), ("B", wmB), ("C1", wmC1), ("C2", wmC2)):
        lf = -wm[i_rp]
        print(f"  {name:<3} {lf.mean()*1e6:8.1f} / {lf.min()*1e6:8.1f} / "
              f"{lf.max()*1e6:8.1f}")

    d = np.abs(wmC1 - wmA)
    print(f"\nC1 vs A 全周最大差 {d.max()*1e6:.1f} µm "
          f"({d.max()/tipA*100:.1f}% of tip mean)")
    print("\n折算到 F 的影响（Δh/h → ΔF/F）：")
    for name, wm in (("B", wmB), ("C1", wmC1), ("C2", wmC2)):
        hm = (-wm[i_rp]).mean()
        hA = (-wmA[i_rp]).mean()
        dh = (hm - hA) / hA
        print(f"  {name}: Δh/h={dh*100:+.2f}% → ΔF/F≈"
              f"{2*abs(dh)*100:.1f}%(截流) ~ {3*abs(dh)*100:.1f}%(黏性)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
