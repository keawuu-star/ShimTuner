# -*- coding: utf-8 -*-
"""
P1 冒烟: 技能极坐标网格 -> GetFEM Mindlin-Reissner 板 brick, 与自研 PolarShimFEM 对照
====================================================================================
运行(conda env getfem-poc):
    D:\\miniconda3\\envs\\getfem-poc\\python.exe p1_mindlin_vs_own.py

目标:
  1. 验证 2D 极坐标四边形网格可导入 GetFEM (add_point/add_convex)
  2. 验证 add_Mindlin_Reissner_plate_brick (variant=1 减缩剪切积分) 可用
  3. 单片 (6,10,0.1) 内缘固支/外缘自由/均布压强:
     GetFEM vs 自研 PolarShimFEM vs md 解析 -> 三者互相印证
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"d:\Desktop\shim stacker\ShimTuner")

import numpy as np
import getfem as gf

from shim_stack_mesh import build_polar_mesh, check_mesh_invariants
from shim_physics_core import (analytic_edge_deflection,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)


def mesh_to_gf(mesh: dict):
    """技能网格 dict -> getfem 2D 网格 (四边形 QK1). 返回 mesh 对象."""
    xy = mesh_xy(mesh)                      # (N,2) m
    m = gf.Mesh("empty", 2)
    gt_q1 = gf.GeoTrans("GT_QK(2,1)")
    for e, n4 in enumerate(mesh["conn"]):
        m.add_convex(gt_q1, xy[n4].T)       # (2,4) 坐标矩阵, 列 = 单元节点
    return m


def mesh_xy(mesh: dict) -> np.ndarray:
    th = mesh["theta_nodes"]
    r = mesh["r_nodes_m"]
    x = r[:, None] * np.cos(th)[None, :]
    y = r[:, None] * np.sin(th)[None, :]
    return np.stack([x.ravel(), y.ravel()], axis=1)


def ring_dofs(mfu, m, mesh, r_target: float) -> list:
    """半径 r_target 圆周上全部节点 -> 该 fem 的全局 dof 列表 (逐角度查 pid)."""
    th = mesh["theta_nodes"]
    dofs = []
    for a in th:
        pid = m.pid_from_coords([float(r_target * np.cos(a)),
                                 float(r_target * np.sin(a))], 1e-9)
        pid = int(np.ravel(pid)[0])
        if pid < 0:
            raise RuntimeError(f"coords not found at r={r_target:.6e}")
        dofs.extend(np.ravel(mfu.basic_dof_nodes([pid])).tolist())
    return dofs


def main():
    print("getfem revision:", gf.__revision__)
    layers = [(6.0, 10.0, 0.1)]
    E, nu = STEEL_7C27MO2_E, STEEL_7C27MO2_NU
    p = 1e6                                     # 1 MPa
    t_mm = 0.1
    radial = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    ntheta = int(sys.argv[2]) if len(sys.argv) > 2 else 48

    # ---- 1) 自研参照 ----
    from shim_fem_plate import PolarShimFEM
    fem = PolarShimFEM(layers, E=E, nu=nu, n_elem_base_radial=radial,
                       n_theta=ntheta)
    h_own = fem.tip_deflections_unit()[0] * p
    h_an = analytic_edge_deflection(6.0, 10.0, t_mm, p, E, nu)
    print(f"own-FEM tip = {h_own * 1e3:.6f} mm | analytic = {h_an * 1e3:.6f} mm")

    # ---- 2) 技能网格 -> GetFEM ----
    mesh = build_polar_mesh(layers, E=E, nu=nu,
                            n_elem_base_radial=radial, n_theta=ntheta)
    assert not check_mesh_invariants(mesh)
    t0 = time.perf_counter()
    m = mesh_to_gf(mesh)
    print(f"mesh import: {m.nbpts()} pts / {m.nbcvs()} cvx "
          f"({time.perf_counter() - t0:.2f} s)")

    # ---- 3) FEM 空间与积分 ----
    mfu = gf.MeshFem(m, 1)
    mfu.set_classical_fem(1)                 # u3 标量 Q1 (自动 FEM_QK)
    mft = gf.MeshFem(m, 2)
    mft.set_classical_fem(1)                 # theta 矢量 Q1 (2 分量)
    mim = gf.MeshIm(m, 2)                    # 完整积分 (精确到 2 次多项式)
    mim_red = gf.MeshIm(m, 1)                # 剪切减缩积分 (1 点)

    # ---- 4) 模型: Mindlin 板 brick (variant=1: 减缩剪切积分) ----
    md = gf.Model("real")
    md.add_fem_variable("u3", mfu)
    md.add_fem_variable("theta", mft)
    md.add_initialized_data("E", E)
    md.add_initialized_data("nu", nu)
    md.add_initialized_data("eps", t_mm * 1e-3)
    md.add_initialized_data("kap", 5.0 / 6.0)
    md.add_Mindlin_Reissner_plate_brick(mim, mim_red, "u3", "theta",
                                        "E", "nu", "eps", "kap", 1)

    # ---- 5) 边界: 内缘 clamp 圆固支 (u3=0, theta=0) ----
    clamp_m = mesh["clamp_radius_mm"] * 1e-3
    # outer_faces = (2,N): 行0=cvid, 行1=局部面号; 逐段找中点半径=clamp 的面
    of = np.asarray(m.outer_faces())
    sel = []
    for col in range(of.shape[1]):
        cv = int(of[0, col])
        lf = int(of[1, col])
        p4 = np.ravel(m.pid_from_cvid(cv)[0])
        p0 = np.ravel(m.pts(int(p4[lf])))
        p1 = np.ravel(m.pts(int(p4[(lf + 1) % 4])))
        r0 = float(np.hypot(*p0))
        r1 = float(np.hypot(*p1))
        # 弦段两端点都落在 clamp 圆上 (中点半径偏小, 不能用)
        if abs(r0 - clamp_m) < 1e-9 and abs(r1 - clamp_m) < 1e-9:
            sel.append(col)
    if not sel:
        raise RuntimeError("clamp 边界段未找到")
    rg_clamp = 100
    m.set_region(rg_clamp, of[:, sel])          # 内孔边界 region
    md.add_Dirichlet_condition_with_simplification("u3", rg_clamp)
    md.add_Dirichlet_condition_with_simplification("theta", rg_clamp)

    # ---- 6) 载荷: 全域均布压强 -> F3 = p (N/m^2) ----
    md.add_initialized_data("pdata", p)
    md.add_source_term_brick(mim, "u3", "pdata")

    # ---- 7) 求解 ----
    t0 = time.perf_counter()
    md.solve("noisy")
    ts = time.perf_counter() - t0
    U = np.asarray(md.variable("u3"), dtype=float)
    # 直接 pid 索引 (标量 Q1: dof == pid, 已实测验证)
    rs_all = np.array([float(np.hypot(*np.ravel(m.pts(p))))
                       for p in range(m.nbpts())])
    wmean = {}
    for lab, rt in (("clamp", 3.0e-3), ("mid", 4.0e-3), ("outer", 5.0e-3)):
        pidr = np.where(np.abs(rs_all - rt) < 1e-9)[0]
        wmean[lab] = float(np.mean(U[pidr]))
    print(f"getfem solve {ts:.2f} s | u3 dofs={U.shape[0]} | "
          f"theta dofs={md.variable('theta').shape[0]}")
    print(f"getfem w: clamp={wmean['clamp'] * 1e6:.4f} um | "
          f"mid={wmean['mid'] * 1e6:.4f} um | "
          f"outer={wmean['outer'] * 1e6:.4f} um | "
          f"own-outer={h_own * 1e6:.4f} um | "
          f"analytic-outer={h_an * 1e6:.4f} um")
    print("注意: getfem Q1+减缩剪切 在该极坐标梯形网格上随加密发散 "
          "(沙漏失稳); 规则矩形网格上 brick 正确 (见 p1b: variant1 "
          "+0.8%, variant2/MITC +0.15%). 本文件只验证 P1 管道: "
          "网格导入/模型/BC/载荷/求解/提取. Mindlin 大挠度 B1 须改用 "
          "p1b 验证过的 MITC 表述或薄实体有限应变路线.")
    assert wmean["clamp"] == 0.0
    assert 0.0 < wmean["mid"] < wmean["outer"]
    print("P1 OK: skill mesh -> getfem Mindlin 管道打通 "
          "(数值行为见上述注意)")


if __name__ == "__main__":
    main()
