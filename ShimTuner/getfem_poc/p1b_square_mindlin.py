# -*- coding: utf-8 -*-
"""
P1b 隔离测试: GetFEM Mindlin brick 在规则矩形网格上的正确性
===========================================================
简支方板 LxL, 均布 p, 中心挠度 vs Kirchhoff 解析
    w_c = 0.00406235 * p * L^4 / D      (SS square, uniform load)
    D = E t^3 / 12(1-nu^2)
薄板 (L/t=100) 时 Mindlin 剪切修正 < 0.1%.
"""
import sys

import getfem as gf
import numpy as np

L, t = 1.0, 0.01
E, nu = 2.0e11, 0.3
p = 1e3
D = E * t ** 3 / (12.0 * (1.0 - nu ** 2))
w_an = 0.00406235 * p * L ** 4 / D
print(f"analytic w_c = {w_an * 1e3:.5f} mm")

n = 20
xs = np.linspace(0.0, L, n + 1)
m = gf.Mesh("cartesian", xs, xs)
mfu = gf.MeshFem(m, 1)
mfu.set_classical_fem(1)
mft = gf.MeshFem(m, 2)
mft.set_classical_fem(1)
mim = gf.MeshIm(m, 2)
mim_red = gf.MeshIm(m, 1)

md = gf.Model("real")
md.add_fem_variable("u3", mfu)
md.add_fem_variable("theta", mft)
md.add_initialized_data("E", E)
md.add_initialized_data("nu", nu)
md.add_initialized_data("eps", t)
md.add_initialized_data("kap", 5.0 / 6.0)

for variant in (1, 2):
    md2 = gf.Model("real")
    md2.add_fem_variable("u3", mfu)
    md2.add_fem_variable("theta", mft)
    md2.add_initialized_data("E", E)
    md2.add_initialized_data("nu", nu)
    md2.add_initialized_data("eps", t)
    md2.add_initialized_data("kap", 5.0 / 6.0)
    md2.add_Mindlin_Reissner_plate_brick(mim, mim_red, "u3", "theta",
                                         "E", "nu", "eps", "kap", variant)
    # 四边简支: w=0 (theta 自由)
    rg = m.outer_faces()
    rid = 200
    m.set_region(rid, rg)
    md2.add_Dirichlet_condition_with_simplification("u3", rid)
    md2.add_initialized_data("pdata", p)
    md2.add_source_term_brick(mim, "u3", "pdata")
    md2.solve()
    U = np.asarray(md2.variable("u3"), dtype=float)
    # 中心点
    pid = m.pid_from_coords([L / 2.0, L / 2.0], 1e-9)
    pid = int(np.ravel(pid)[0])
    wc = float(U[pid])
    print(f"variant={variant}: w_c = {wc * 1e3:.6f} mm | "
          f"w/w_an = {wc / w_an:.5f}")
