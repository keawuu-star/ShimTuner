# -*- coding: utf-8 -*-
"""
B1 MVP: 单片大挠度 3D 薄实体 (极坐标 hexa) + GetFEM 有限应变 SVK brick
======================================================================
路线: 直接全几何精确 (用户拍板) + 最小自研 (吃现成 brick) + 结构化网格.

网格: 复用技能网格的 (r, theta) 极坐标分层 (shim_stack_mesh.build_polar_mesh),
      沿厚度 z 拉伸 nz 层 -> GT_QK(3,1) 六面体.
物理: SaintVenant-Kirchhoff 有限应变 (add_finite_strain_elasticity_brick),
      夹持内柱面 (u=0), 顶面均布向下压强 p (top-down, 与 v0 约定一致).
验证: ①小 p 线性段 vs md 解析 / 自研 PolarShimFEM; ②大 p 扫描 h(p) 记录.
局部面号 (hexa): lf0=+r(外), lf1=-r(内柱面=clamp), lf2=+θ, lf3=-θ,
                 lf4=+z(顶面), lf5=-z(底面)  [经 unit cube normal 探针确认]
运行: D:\\miniconda3\\envs\\getfem-poc\\python.exe b1_hexa_svk.py
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"d:\Desktop\shim stacker\ShimTuner")

import numpy as np
import getfem as gf

from shim_stack_mesh import build_polar_mesh
from shim_physics_core import (analytic_edge_deflection, ShimLayer,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)


def build_annulus_hexa(radial=40, nth=48, nz=3, t_mm=0.1,
                       d_in_mm=6.0, d_out_mm=10.0):
    """极坐标 hexa 网格: 返回 (gfmesh, r_nodes_m, nth, nz)."""
    pm = build_polar_mesh([(d_in_mm, d_out_mm, t_mm)],
                          n_elem_base_radial=radial, n_theta=nth)
    r = pm["r_nodes_m"]                       # (n_r,) m
    th = pm["theta_nodes"]                    # (nth,) rad
    n_r = len(r)
    t = t_mm * 1e-3
    z = np.linspace(-t / 2.0, t / 2.0, nz + 1)

    m = gf.Mesh("empty", 3)
    pid = {}
    for k in range(nz + 1):
        for i in range(n_r):
            for j in range(nth):
                pid[(i, j, k)] = int(m.add_point(
                    [float(r[i] * np.cos(th[j])),
                     float(r[i] * np.sin(th[j])),
                     float(z[k])])[0])
    # GT_QK(3,1) 顶点序 (参考位型 b1=r, b2=th, b3=z; 位模式经 cartesian 探针确认)
    bits = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0),
            (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)]
    gt = gf.GeoTrans("GT_QK(3,1)")
    for i in range(n_r - 1):
        for j in range(nth):
            for k in range(nz):
                verts = [pid[(i + b1, (j + b2) % nth, k + b3)]
                         for b1, b2, b3 in bits]
                # 坐标列向量 (3,8) 按顶点序
                pts = np.array([[float(np.ravel(m.pts(p))[d]) for p in verts]
                                for d in range(3)])
                m.add_convex(gt, pts)
    return m, r, th, nz, z


def boundary_regions(m, n_r, nth, nz):
    """按局部面号建 region: 返回 rg_inner(lf1), rg_top(lf4)."""
    of = np.asarray(m.outer_faces())
    sel_in = np.where(of[1] == 1)[0]          # -r 内柱面
    sel_top = np.where(of[1] == 4)[0]         # +z 顶面
    m.set_region(1, of[:, sel_in])
    m.set_region(2, of[:, sel_top])
    return 1, 2


def solve_p(md, m, th, p_pa, r_target, z_target):
    md.set_variable("pdata", [0.0, 0.0, -p_pa])
    md.solve("max_iter", 40, "max_res", 1e-10)
    pts = [[float(r_target * np.cos(a)), float(r_target * np.sin(a)),
            float(z_target)] for a in th]
    V = np.asarray(md.interpolation("u(3)", pts, m), dtype=float)
    return float(np.mean(V))


_last_p = [0.0]   # 当前已收敛载荷 (模块级, 跨 solve 热启动)


def drive_p(md, m, th, p_target, r_target, z_target):
    """自适应载荷步进: 全量尝试, 失败对半重试 (牛顿热启动). 返回 w."""
    pcur = _last_p[0]
    if p_target < pcur - 1e-12:
        raise ValueError("p_target 必须递增")
    Ustore = np.asarray(md.variable("u"), dtype=float)
    rem = p_target - pcur
    guard = 0
    while rem > 1e-12 * p_target:
        step = rem
        ok = False
        while True:
            md.set_variable("pdata", [0.0, 0.0, -(pcur + step)])
            _, conv = md.solve("max_iter", 15, "max_res", 1e-8)
            if conv:
                ok = True
                break
            md.set_variable("u", Ustore)          # 回滚到上一收敛态
            step *= 0.5
            guard += 1
            if guard > 30 or step < 1e-9 * max(p_target, 1.0):
                raise RuntimeError("载荷步进无法收敛")
        Ustore = np.asarray(md.variable("u"), dtype=float)
        pcur += step
        _last_p[0] = pcur
        rem = p_target - pcur
    pts = [[float(r_target * np.cos(a)), float(r_target * np.sin(a)),
            float(z_target)] for a in th]
    V = np.asarray(md.interpolation("u(3)", pts, m), dtype=float)
    return float(np.mean(V))


def ring_pids_at(m, r_target, z_target, r, th):
    out = []
    for a in th:
        p = m.pid_from_coords([float(r_target * np.cos(a)),
                               float(r_target * np.sin(a)),
                               float(z_target)], 1e-9)
        p = int(np.ravel(p)[0])
        if p < 0:
            raise RuntimeError("pid not found")
        out.append(p)
    return out


def main():
    E, nu = STEEL_7C27MO2_E, STEEL_7C27MO2_NU
    lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
    mu = E / (2.0 * (1.0 + nu))
    radial = int(sys.argv[1]) if len(sys.argv) > 1 else 48
    nth = int(sys.argv[2]) if len(sys.argv) > 2 else 48
    nz = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    deg = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    imdeg = 3 if deg == 1 else 4

    t0 = time.perf_counter()
    m, r, th, nz_, z_ = build_annulus_hexa(radial, nth, nz)
    print(f"mesh: {m.nbpts()} pts, {m.nbcvs()} hexa "
          f"({time.perf_counter() - t0:.2f} s)")
    rg_inner, rg_top = boundary_regions(m, len(r), nth, nz)
    print(f"regions: inner faces={len(np.where(np.asarray(m.outer_faces())[1] == 1)[0])}, "
          f"top faces={len(np.where(np.asarray(m.outer_faces())[1] == 4)[0])}")

    mf = gf.MeshFem(m, 3)
    mf.set_classical_fem(deg)
    mim = gf.MeshIm(m, imdeg)

    md = gf.Model("real")
    md.add_fem_variable("u", mf)
    if len(sys.argv) > 6 and sys.argv[6] == "lin":
        md.add_initialized_data("lambda_d", lam)
        md.add_initialized_data("mu_d", mu)
        md.add_isotropic_linearized_elasticity_brick(mim, "u",
                                                     "lambda_d", "mu_d")
    else:
        md.add_initialized_data("coefs", [lam, mu])
        md.add_finite_strain_elasticity_brick(mim, "SaintVenant_Kirchhoff",
                                              "u", "coefs")
    # 载荷向量数据: pv = {0,0,-p} (顶面压强, top-down)
    md.add_initialized_data("pdata", [0.0, 0.0, 0.0])
    md.add_source_term_brick(mim, "u", "pdata", rg_top)
    # clamp: 内柱面 u=0
    md.add_Dirichlet_condition_with_simplification("u", rg_inner)
    print("model built, ndof(u) =", md.nbdof())

    # 外缘顶面环 pid (输出用) - 改用 interpolation 取点, 见 solve_p
    z_top = z_[-1]
    b = 5.0e-3
    # 解析/自研对照 (线性段)
    h_an_p = lambda p: analytic_edge_deflection(6.0, 10.0, 0.1, p, E, nu)

    # p 扫描 (小 p 段回归 + 大挠度段); argv4 可指定单一 p(MPa)
    if len(sys.argv) > 4:
        p_list = [float(sys.argv[4]) * 1e6]
    else:
        p_list = [0.2e6, 1e6, 2e6, 4e6, 6e6, 8e6]
    print(f"{'p(MPa)':>7} {'w_top(mm)':>10} {'analyt(mm)':>11} {'w/wan':>8}",
          flush=True)
    for p in p_list:
        w = drive_p(md, m, th, p, b, z_top)
        h_an = h_an_p(p)
        print(f"{p / 1e6:7.3f} {w * 1e3:10.5f} {h_an * 1e3:11.5f} "
              f"{abs(w) / h_an:8.3f}", flush=True)
    # 线性段 gate: p=0.2MPa 相对解析 (薄板小挠度)
    w_small = drive_p(md, m, th, 0.2e6, b, z_top)
    rel = abs(abs(w_small) - h_an_p(0.2e6)) / h_an_p(0.2e6)
    print(f"linear-gate rel@0.2MPa = {rel:.4f}")
    # 单点调试: 径向剖面 (z=中面)
    if len(sys.argv) > 4:
        z_mid = z_[nz_ // 2]
        print("profile (z-mid):")
        for rt in (3.2e-3, 3.5e-3, 4.0e-3, 4.5e-3, 4.8e-3, 5.0e-3):
            try:
                wm = drive_p(md, m, th, 0.2e6, rt, z_mid)
            except RuntimeError:
                continue
            print(f"  r={rt * 1e3:.1f}mm w={wm * 1e6:.4f} um", flush=True)
    assert rel < 0.10, f"线性段超差 rel={rel:.4f}"


if __name__ == "__main__":
    main()
