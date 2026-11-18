# -*- coding: utf-8 -*-
"""
Q → w → P → w′ 定点迭代演示（v0）
==================================
把两个独立项目串起来（对应 ShimTunerPhysics.md「模拟逻辑设计」）：

  1. 当前挠度场 w_k(r)  →  流道几何 h(r) = h_deck − w_k(r)
  2. FlowSolver: 给定 Q 与 h(r) → 压力场 P_k(r)（含窗口射流压力包）
  3. DeflectionSolver: 把 P_k 作为阀片底面载荷 → 新挠度场 w_P(r)
  4. 残差 = max|w_P − w_k|；不收敛则 w_{k+1} = w_k + α(w_P − w_k)

注意符号：DeflectionSolver 的 w 以"压向活塞"为正；油压从底面把阀片顶开，
所以挠度载荷取 −(P − P_back)，最终 w < 0 表示抬升。

用法: python -X utf8 couple_demo.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFL = os.path.abspath(os.path.join(HERE, "..", "DeflectionSolver"))
for p in (HERE, DEFL):
    if p not in sys.path:
        sys.path.insert(0, p)

import channel_flow as cf                     # noqa: E402
from shim_layered import LayeredStack         # noqa: E402
from shim_physics_core import ShimLayer       # noqa: E402

# ---- 几何（活塞与阀片同轴，单位 mm）----
PISTON = cf.demo_piston(d_piston_mm=46, d_rod_mm=18, n_ports=6,
                        port_width_mm=8, port_radius_mm=16,
                        seat_radius_mm=9, deck_height_mm=0.10,
                        shim_outer_mm=21)
FLUID = cf.FluidSpec(rho=850.0, cSt=15.0)
LAYERS = [ShimLayer(18, 42, 0.25), ShimLayer(18, 36, 0.20),
          ShimLayer(18, 30, 0.20)]

V = 0.15                 # m/s
ALPHA = 0.50             # 松弛因子
MAX_STEP = 150e-6        # 每次迭代最大挠度增量（信赖域，防止首步爆冲）
TOL = 2e-6               # 残差 [m]
MAX_IT = 30
NL = "--nl" in sys.argv  # 用分层大挠度(vK)代替线性


def main() -> int:
    stack = LayeredStack(LAYERS, n_elem_base_radial=16, n_theta=12,
                         clamp_radius_mm=PISTON.seat_radius_mm, nl=NL)
    r_shim = stack.r_nodes                      # 顶片径向节点 [m]
    A_eff = PISTON.area_effective_m2()
    Q = V * A_eff

    w = np.zeros_like(r_shim)                   # 初始：平阀片
    print(f"迭代: v={V:.2f} m/s, Q={Q*1e6:.3f} mL/s, "
          f"层数={len(LAYERS)}, DOF={stack.n_dof}")
    print(f"{'it':>3} {'w_tip(µm)':>10} {'h_port(µm)':>11} {'Δp(MPa)':>9} "
          f"{'F(N)':>8} {'残差(µm)':>10}")

    last = None
    for it in range(1, MAX_IT + 1):
        # 1) 间隙场: h = h_deck − w   (w<0 表示抬升)
        gap = cf.ArrayGap(r_shim, -w, h_deck_m=PISTON.h_deck_m())
        # 2) 流道 → 压力场
        res = cf.solve_point(Q, PISTON, FLUID, gap, n_grid=240)
        p_back = FLUID.p_back_MPa * 1e6

        # 3) 组装阀片底面载荷: p(r)（r<r_port 用上游压力）+ 射流压力包
        r_port, r_out = PISTON.r_port_m(), PISTON.r_outer_m()
        p_ch = res.p_pa                       # [r_port, r_out]
        r_ch = res.r_m

        def pressure(r_m, _theta, _rc=r_ch, _pc=p_ch, _pp=res.p_up,
                     _rp=r_port, _ro=r_out, _sig=res.jet_sigma,
                     _pj=res.p_jet, _pb=p_back):
            r = np.asarray(r_m, dtype=float)
            base = np.where(r < _rp, _pp,
                            np.interp(r, _rc, _pc, left=_pp, right=_pb))
            bump = _pj * np.exp(-((r - _rp) / _sig) ** 2)
            return -(base - _pb + bump)      # 负号 = 顶开阀片

        stack.set_pressure(pressure)
        stack.solve(1.0, n_steps=4) if NL else stack.solve(1.0)
        w_new = np.array([stack.w_matrix(0)[i].mean()
                          for i in range(stack.n_r)])

        delta = np.clip(w_new - w, -MAX_STEP, MAX_STEP)
        d = float(np.abs(delta).max())
        w_next = w + ALPHA * delta
        print(f"{it:3d} {w_next[-1]*1e6:10.3f} {res.h_port*1e6:11.3f} "
              f"{res.dp_total/1e6:9.4f} {res.F_damper:8.1f} {d*1e6:10.4f}")
        last = (res, w_next, d)
        if d < TOL:
            print(f"收敛: {it} 次迭代, 残差 {d*1e6:.3f} µm")
            break
        w = w_next
    else:
        print(f"未在 {MAX_IT} 次内收敛 (残差 {last[2]*1e6:.3f} µm)")

    res, w_new, _ = last
    print(f"\n最终: w_tip={w_new[-1]*1e6:.2f} µm, h_port={res.h_port*1e6:.2f} µm, "
          f"Δp={res.dp_total/1e6:.4f} MPa, F={res.F_damper:.1f} N, "
          f"射流 p_jet={res.p_jet/1e6:.3f} MPa")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
