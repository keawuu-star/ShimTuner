# -*- coding: utf-8 -*-
"""交叉核对: 三求解器 + Φ 缺陷对 F-v 曲线的影响."""
import sys
import numpy as np

sys.path.insert(0, r"D:\Desktop\shim stacker\ShimTuner")

from shim_physics_core import (analytic_edge_deflection, ShimLayer, ShimStackCore,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)
from shim_fem_plate import PolarShimFEM
from shim_nl_plate import PolarShimNLPlate

E, NU = STEEL_7C27MO2_E, STEEL_7C27MO2_NU
layers = [ShimLayer(6, 10, 0.1), ShimLayer(6, 9, 0.1), ShimLayer(6, 8, 0.1)]

core = ShimStackCore(layers, E=E, nu=NU)
fem = PolarShimFEM(layers, E=E, nu=NU, n_elem_base_radial=60, n_theta=48)
nl = PolarShimNLPlate(layers, E=E, nu=NU, n_elem_base_radial=20, n_theta=24, nl=True)

ps = np.array([0.25e6, 0.5e6, 1.0e6, 2.0e6, 4.0e6])
curve = nl.solve_curve(ps, n_steps=4)
lin = core.edge_deflection_curve(ps)[0]
fem_h = fem.tip_deflections_unit()[0] * 1e6

print("=" * 82)
print("锥形 10/9/8 (t=0.1, 内径 6): 1D / 2D / NL")
print("=" * 82)
print(f"1D v0 @1MPa = {core.edge_deflections(1e6)[0]*1e3:.5f} mm")
print(f"2D Mindlin @1MPa = {fem_h*1e3:.5f} mm (vs 1D {abs(fem_h-lin[2])/lin[2]:.2e})")
print(f"NL vK: {nl.n_dof} DOF, 曲线求解 {nl.curve_info['t_total']:.1f}s, "
      f"Newton {nl.curve_info['n_newton']} 次")
print(f"{'p(MPa)':>7} {'w_NL(mm)':>10} {'w_lin(mm)':>10} {'w/t':>6} {'K_NL/K_lin':>10}")
for i, p in enumerate(ps):
    w = curve[0, i]
    print(f"{p/1e6:7.2f} {w*1e3:10.5f} {lin[i]*1e3:10.5f} {w/0.1e-3:6.2f} {lin[i]/w:10.3f}")

# ---- Φ 缺陷对 F-v 的影响: 修补 ShimTuner.py 副本中的 Cramer 项 ----
print("\n" + "=" * 82)
print("ShimTuner.py DamperModel F-v: 现状(带 Φ 缺陷) vs 修正后")
print("=" * 82)
import ShimTuner as st
from ShimTuner import DamperModel

shims = [{'d_in': 6, 'd_out': 10, 't': 0.1}] * 3
v = np.array([0.1, 0.5, 1.0, 2.0, 4.0])
m = DamperModel(bleed_area=2.5, shims=shims)
f_bug = m.get_fv_curve(v)

orig = st.ShimPlate._phi_coeff


def fixed(self):
    a, b = self.inner_radius_m, self.outer_radius_m
    if b <= 0.0 or a <= 0.0 or a >= b:
        return 0.0
    rho = a / b
    L = np.log(rho)
    rhs1 = (rho ** 2 / 8.0) * (2.0 * L + 1.0) - (rho ** 4) / 16.0
    rhs2 = (3.0 + self.poisson_ratio) / 16.0
    det = 2 * rho ** 2 * (self.poisson_ratio - 1.0) - 2 * (1.0 + self.poisson_ratio)
    B = (rhs1 * (self.poisson_ratio - 1.0) - rhs2) / det
    C = (2 * rho ** 2 * rhs2 - 2 * (1.0 + self.poisson_ratio) * rhs1) / det
    return float(B * (1 - rho ** 2) - C * L + rho ** 2 / 8 * L + (1 - rho ** 4) / 64)


st.ShimPlate._phi_coeff = fixed
f_fix = m.get_fv_curve(v)
st.ShimPlate._phi_coeff = orig

print(f"{'v(m/s)':>7} {'F_现状(N)':>11} {'F_修正(N)':>11} {'比值':>7}")
for i, vv in enumerate(v):
    print(f"{vv:7.2f} {f_bug[i]:11.1f} {f_fix[i]:11.1f} {f_bug[i]/f_fix[i]:7.3f}")
