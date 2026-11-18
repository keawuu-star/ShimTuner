# -*- coding: utf-8 -*-
"""NL (von Karman) 网格收敛核查: 单片与锥形堆叠在 1 MPa 下的 w 与刚化因子."""
import sys
import time
import numpy as np

sys.path.insert(0, r"D:\Desktop\shim stacker\ShimTuner")
from shim_physics_core import ShimLayer, ShimStackCore, STEEL_7C27MO2_E, STEEL_7C27MO2_NU
from shim_nl_plate import PolarShimNLPlate

E, NU = STEEL_7C27MO2_E, STEEL_7C27MO2_NU
P = 1e6


def run(layers, label, meshes):
    core = ShimStackCore(layers, E=E, nu=NU)
    lin = core.edge_deflections(P)[0]
    print(f"\n== {label} @1MPa (线性 1D = {lin*1e3:.5f} mm) ==")
    print(f"{'nr×nt':>9} {'DOF':>6} {'w_NL(mm)':>10} {'w/t':>6} {'K_NL/K_lin':>10} {'相对上一档':>10}")
    prev = None
    for nr, nt in meshes:
        t0 = time.perf_counter()
        nl = PolarShimNLPlate(layers, E=E, nu=NU, n_elem_base_radial=nr, n_theta=nt, nl=True)
        nl.solve(P, n_steps=6, tol=1e-7)
        w = float(abs(nl.tip_deflections()[0]))
        d = "" if prev is None else f"{abs(w-prev)/prev:.2e}"
        print(f"{nr:4d}x{nt:<4d} {nl.n_dof:6d} {w*1e3:10.5f} {w/0.1e-3:6.3f} "
              f"{lin/w:10.4f} {d:>10}  ({time.perf_counter()-t0:.1f}s)")
        prev = w


run([ShimLayer(6, 10, 0.1)], "单片 6/10 t0.1",
    [(12, 12), (16, 16), (20, 24), (30, 36), (40, 48), (60, 72)])

run([ShimLayer(6, 10, 0.1), ShimLayer(6, 9, 0.1), ShimLayer(6, 8, 0.1)],
    "锥形 10/9/8 t0.1",
    [(12, 12), (16, 16), (20, 24), (30, 36), (40, 48)])
