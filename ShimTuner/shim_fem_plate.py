# -*- coding: utf-8 -*-
"""
ShimTuner 基础版 2D 极坐标 FEM 求解器 (线性小挠度 Mindlin 板)
=============================================================
网格:   复用技能网格函数 shim_stack_mesh.build_polar_mesh (半径台阶 × 角度等分,
        关键半径强制节点; 单元属性 D_e = ΣDᵢ 由网格带给出)。
物理:   与 v0 (shim_physics_core.ShimStackCore) 同一堆叠模型 —— 层间粘合无滑移,
        阶梯刚度 D(r)=ΣDᵢ; 内缘 clamp 圆固支; 外缘自由; 顶片环形面 [a₀,b₀]
        均布压强 p (方向 = +z)。差异仅为: v0=1D Kirchhoff 环元(无横向剪切),
        本模块=2D 极坐标 4 节点 Mindlin 板 (含横向剪切 kG·Σt, k=5/6),
        剪切使结果略软 ~1.2(t/(b−a))² 量级 —— 这是物理修正而非误差。

单元 (Q4 Mindlin, 每节点 3 DOF: w, φx, φy, φ = 中面法线倾角向量):
    κx=φx,x, κy=φy,y, κxy=φx,y+φy,x       (工程曲率)
    γx=w,x+φx, γy=w,y+φy                  (横向剪切)
    U = ½∫[D(κx²+κy²+2νκxκy + (1−ν)/2·κxy²) + S(γx²+γy²)] dA
    D_e = ΣDᵢ (网格带, N·m),  S = k·G·Σtᵢ (G=E/2(1+ν))
积分:   弯曲 2×2 Gauss; 剪切 1×1 中心 (选择性减缩积分, 抗剪切锁死)。
载荷:   单元面力 = p (沿 +z), 只加在 face 带 (顶片环形面)。
边界:   clamp 圆整圈 w=φx=φy=0 (节点环 clamp_node·nθ+j); 外缘自然边界。
求解:   单位压强一次性求解 (线性), 任意 p 直接线性放大 —— 组装+分解只做一次。

验证 (python -X utf8 shim_fem_plate.py):
  * 单片 vs md 解析 Φ·p·b⁴/D   (Mindlin 剪切修正量级 ~1e-3)
  * 等径×3 vs 解析/3, vs v0 1D
  * 锥形 10/9/8 各片外缘 vs v0 1D
  * 环向均匀性 (轴对称载荷下 w 应与 θ 无关)
  * 网格收敛 (径向 100→400)
  * 能量守恒 ½uᵀKu = ½fᵀu 与总压力 F=p·π(b₀²−a₀²) 对照
"""
from __future__ import annotations

import math
import time
from typing import Sequence

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from shim_physics_core import (ShimLayer, ShimStackCore,
                               analytic_edge_deflection,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)
from shim_stack_mesh import build_polar_mesh, check_mesh_invariants

__all__ = ["PolarShimFEM", "auto_n_theta", "KSHEAR"]

KSHEAR = 5.0 / 6.0                     # Mindlin 剪切修正因子
SHEAR_ASPECT_TARGET = 8.0              # 自动 nθ: 外缘弧长 ≈ 8×平均径向单元长


def auto_n_theta(b_max_m: float, dr_avg_m: float, aspect: float = SHEAR_ASPECT_TARGET,
                 min_theta: int = 48, max_theta: int = 720) -> int:
    """自动周向分段数 (偶数): 外缘弧长 ≈ aspect×平均径向单元长."""
    arc = aspect * max(dr_avg_m, 1e-12)
    nt = int(round(2.0 * math.pi * b_max_m / arc))
    nt = max(min_theta, min(max_theta, nt))
    return nt + (nt % 2)                # 取偶, 便于扇区对称应用


class PolarShimFEM:
    """
    2D 极坐标 Mindlin 板 FEM: 输入阀片堆叠 -> 单位压强线性刚度解.

    与 v0 ShimStackCore 同物理 (粘合 ΣD 堆叠), 但 2D 网格 + Mindlin 剪切.
    GUI 用法: 几何/E/ν 不变时求解一次, h(p) 曲线 = 单位解 × p 即时缩放.
    """

    def __init__(self, layers_mm: Sequence, E: float = STEEL_7C27MO2_E,
                 nu: float = STEEL_7C27MO2_NU,
                 n_elem_base_radial: int = 100, n_theta: int = 0,
                 clamp_radius_mm: float | None = None):
        """
        layers_mm          : 阀片列表 (自上而下, [0]=受油压顶片)
        n_elem_base_radial : 目标径向单元总数 (mesh 带内 ceil 取整)
        n_theta            : 周向分段; 0 → auto_n_theta 自动 (偶数)
        clamp_radius_mm    : 夹持半径, 默认 min(d_in)/2
        """
        if int(n_elem_base_radial) < 1:
            raise ValueError("n_elem_base_radial 必须 >= 1")
        self.layers = [l if isinstance(l, ShimLayer) else
                       ShimLayer(*_triple(l)) for l in layers_mm]
        for l in self.layers:
            l.validate()
        self.E, self.nu = E, nu
        # ---- 关键半径跨度估算 dr_avg (供自动 nθ) ----
        a_min = min(l.a_m for l in self.layers)
        b_max = max(l.b_m for l in self.layers)
        clamp_mm = (clamp_radius_mm if clamp_radius_mm is not None
                    else min(l.d_in_mm for l in self.layers) / 2.0)
        if n_theta == 0:
            dr_avg = (b_max - clamp_mm * 1e-3) / n_elem_base_radial
            n_theta = auto_n_theta(b_max, dr_avg)

        # ---- 网格 (技能函数, 契约见 shim_stack_mesh.py) ----
        self.mesh = build_polar_mesh(self.layers, E=E, nu=nu,
                                     n_elem_base_radial=n_elem_base_radial,
                                     n_theta=n_theta, clamp_radius_mm=clamp_mm)
        probs = check_mesh_invariants(self.mesh)
        if probs:
            raise RuntimeError("网格不变量失败: " + "; ".join(probs))

        self.n_theta = self.mesh["n_theta"]
        self.n_cell_r = self.mesh["n_cell_r"]
        self.n_nodes = self.mesh["n_nodes"]
        self.n_dof = 3 * self.n_nodes

        # ---- 每条带: 弯曲 D (网格) + 剪切 S = k·G·Σt (物理 Σt) ----
        rm = self.mesh["rm_c"]
        tsum_c = np.zeros(self.n_cell_r)
        for l in self.layers:
            m = (rm >= l.a_m - 1e-12) & (rm <= l.b_m + 1e-12)
            tsum_c[m] += l.t_mm * 1e-3
        G = E / (2.0 * (1.0 + nu))
        self.S_c = KSHEAR * G * tsum_c          # (n_cell_r,) N/m
        self.D_c = self.mesh["De_c"]            # (n_cell_r,) N·m

        # ---- 组装 + 单位压强求解 ----
        t0 = time.perf_counter()
        self._assemble()
        self.t_assemble = time.perf_counter() - t0
        t0 = time.perf_counter()
        self._solve_unit()
        self.t_solve = time.perf_counter() - t0

    # ==================================================
    # 1. 组装
    # ==================================================
    def _assemble(self):
        mesh = self.mesh
        conn = mesh["conn"]                       # (M, 4)
        xy = self._node_xy()                      # (N, 2)
        M = mesh["n_elem"]
        n_theta = self.n_theta
        x = xy[conn, 0]                           # (M, 4)
        y = xy[conn, 1]
        er_map = np.arange(M) // n_theta          # 每个单元的径向带号

        # clamp 环节点: 径向 index = clamp_node
        clamp_nodes = mesh["clamp_node"] * n_theta + np.arange(n_theta)
        self.clamped_dofs = np.concatenate(
            [3 * clamp_nodes + d for d in (0, 1, 2)])
        free = np.ones(self.n_dof, dtype=bool)
        free[self.clamped_dofs] = False
        self.free = free
        self.rmap = np.full(self.n_dof, -1, dtype=np.int64)
        self.rmap[free] = np.arange(int(free.sum()))

        # ---- 等参 Q4, 全 M 个单元向量化组装 ----
        xi_a = np.array([-1.0, 1.0, 1.0, -1.0])
        eta_a = np.array([-1.0, -1.0, 1.0, 1.0])
        q = 1.0 / math.sqrt(3.0)
        gpts_b = [(-q, -q), (q, -q), (q, q), (-q, q)]
        Dm_e = np.zeros((M, 3, 3))                # 每单元弯曲本构 (带 D_e)
        Dm_base = np.array([[1.0, self.nu, 0.0],
                            [self.nu, 1.0, 0.0],
                            [0.0, 0.0, (1.0 - self.nu) / 2.0]])
        Dm_e[:] = Dm_base[None, :, :] * self.D_c[er_map][:, None, None]
        S_e = self.S_c[er_map]                    # (M,) 剪切刚度
        face_c = self.mesh["face_c"]
        is_face = face_c[er_map]                  # (M,) bool

        Ke = np.zeros((M, 12, 12))
        fe = np.zeros((M, 4))

        def _geo(pts):
            """对一组高斯点返回 (N4, dNxi, dNeta, |det|, dNx, dNy) 列表."""
            out = []
            for xi, eta in pts:
                N = 0.25 * (1.0 + xi * xi_a) * (1.0 + eta * eta_a)
                dNxi = 0.25 * xi_a * (1.0 + eta * eta_a)
                dNeta = 0.25 * (1.0 + xi * xi_a) * eta_a
                xxi = x @ dNxi                     # (M,)
                xet = x @ dNeta
                yxi = y @ dNxi
                yet = y @ dNeta
                jd = xxi * yet - yxi * xet
                if np.any(np.abs(jd) < 1e-30):
                    raise RuntimeError("存在 Jacobian 退化单元")
                dNx = (yet[:, None] * dNxi[None, :]
                       - yxi[:, None] * dNeta[None, :]) / jd[:, None]
                dNy = (-xet[:, None] * dNxi[None, :]
                       + xxi[:, None] * dNeta[None, :]) / jd[:, None]
                out.append((N, dNxi, dNeta, np.abs(jd), dNx, dNy))
            return out

        # ---- 弯曲: 2×2 Gauss (向量化全单元) ----
        for N, dNxi, dNeta, ajd, dNx, dNy in _geo(gpts_b):
            Bb = np.zeros((M, 3, 12))
            Bb[:, 0, 1::3] = dNx                  # κx  ← φx,x
            Bb[:, 1, 2::3] = dNy                  # κy  ← φy,y
            Bb[:, 2, 1::3] = dNy                  # κxy ← φx,y
            Bb[:, 2, 2::3] = dNx                  # κxy ← φy,x
            BT = np.transpose(Bb, (0, 2, 1))      # (M,12,3)
            Ke += ajd[:, None, None] * np.einsum(
                "mij,mjk,mkl->mil", BT, Dm_e, Bb)
            fe += ajd[:, None] * N[None, :]       # w 载荷 (单位压强, 先全积)
        fe[~is_face] = 0.0                        # 只保留受压面 (顶片环形面)

        # ---- 剪切: 1×1 中心 (减缩, 防锁) ----
        N0, dNxi0, dNeta0, ajd0, dNx0, dNy0 = _geo([(0.0, 0.0)])[0]
        Bs = np.zeros((M, 2, 12))
        Bs[:, 0, 0::3] = dNx0                     # γx ← w,x
        Bs[:, 0, 1::3] = 0.25                      # γx ← φx
        Bs[:, 1, 0::3] = dNy0                     # γy ← w,y
        Bs[:, 1, 2::3] = 0.25                      # γy ← φy
        BsT = np.transpose(Bs, (0, 2, 1))
        Ke += 4.0 * ajd0[:, None, None] * S_e[:, None, None] * np.einsum(
            "mij,mjk->mik", BsT, Bs)

        # ---- 载荷: w DOF (np.add.at 汇总邻元贡献) ----
        f_full = np.zeros(self.n_dof)
        np.add.at(f_full, 3 * conn, fe)

        # ---- 组装 K_red: 删除 clamp 行/列 (coo 自动合并重复项) ----
        dof_flat = 3 * conn[:, :, None] + np.arange(3)[None, None, :]
        dof_flat = dof_flat.reshape(M, 12)        # (M,12) 局部→全局 DOF
        rows_free = free[dof_flat]                # (M,12) bool
        keep = rows_free[:, :, None] & rows_free[:, None, :]
        idx = self.rmap[dof_flat]                 # −1 表示 clamp
        I = np.broadcast_to(idx[:, :, None], (M, 12, 12))[keep]
        J = np.broadcast_to(idx[:, None, :], (M, 12, 12))[keep]
        V = Ke[keep]
        K_red = sp.coo_matrix((V, (I, J)),
                              shape=(int(free.sum()), int(free.sum()))).tocsc()
        self.K_red = K_red
        self.f_red = f_full[free]
        self.f_full = f_full
        self.area_face = float(f_full[0::3].sum())   # Σ∫N dA ≈ 受压面面积

    def _node_xy(self) -> np.ndarray:
        th = self.mesh["theta_nodes"]
        r = self.mesh["r_nodes_m"]
        x = r[:, None] * np.cos(th)[None, :]
        y = r[:, None] * np.sin(th)[None, :]
        return np.stack([x.ravel(), y.ravel()], axis=1)

    # ==================================================
    # 2. 单位压强求解
    # ==================================================
    def _solve_unit(self):
        u_free = spsolve(self.K_red, self.f_red)
        u = np.zeros(self.n_dof)
        u[self.free] = u_free
        self.u_unit = u                          # (3N,) m/Pa (0,1,2 = w,φx,φy)
        # 能量守恒核对: ½uᵀKu ≈ ½fᵀu
        self.E_int = 0.5 * float(u_free @ (self.K_red @ u_free))
        self.E_ext = 0.5 * float(self.f_full @ self.u_unit)
        self.force_total = float(self.f_full[0::3].sum())   # 总压力 @1Pa ≈ p·A

    # ==================================================
    # 3. 结果接口 (全部针对单位压强, GUI 乘 p 即可)
    # ==================================================
    def w_matrix_unit(self) -> np.ndarray:
        """w 解 (n_r, nθ) m/Pa."""
        return self.u_unit[0::3].reshape(self.mesh["n_r"], self.n_theta)

    def profile_unit_w(self) -> tuple[np.ndarray, np.ndarray]:
        """环向平均剖面: (r_nodes[m], w̄(r) [m/Pa])."""
        wm = self.w_matrix_unit()
        return self.mesh["r_nodes_m"], wm.mean(axis=1)

    def ring_uniformity(self) -> float:
        """轴对称载荷下环向一致性: max|w−w̄|/max|w̄| (应 ~1e-12)."""
        wm = self.w_matrix_unit()
        wbar = wm.mean(axis=1)
        denom = float(np.max(np.abs(wbar))) or 1.0
        return float(np.max(np.abs(wm - wbar[:, None]))) / denom

    def tip_deflections_unit(self) -> np.ndarray:
        """每片阀片外缘 (bᵢ 圆环平均) 挠度 [m/Pa], 顺序 = 输入 layers."""
        rn = self.mesh["r_nodes_m"]
        wm = self.w_matrix_unit()
        out = []
        for l in self.layers:
            idx = int(np.argmin(np.abs(rn - l.b_m)))
            if abs(rn[idx] - l.b_m) > 1e-9:
                raise RuntimeError("外缘半径未成为网格节点 (网格化 bug)")
            out.append(float(wm[idx].mean()))
        return np.asarray(out)

    def deflection_curve_unit(self, p_pa: float = 1e6):
        """便捷: 每片外缘 h(p) 数组 [m] (线性放大)."""
        return self.tip_deflections_unit() * p_pa

    def solve_energy_ok(self, tol: float = 1e-8) -> bool:
        """½uᵀKu = ½fᵀu 能量守恒 (线性系统应精确成立)."""
        return abs(self.E_int - self.E_ext) <= tol * max(abs(self.E_ext), 1e-300)


def _triple(x):
    if isinstance(x, dict):
        return (float(x["d_in_mm"]), float(x["d_out_mm"]), float(x["t_mm"]))
    try:
        return (float(x.d_in_mm), float(x.d_out_mm), float(x.t_mm))
    except AttributeError:
        return (float(x[0]), float(x[1]), float(x[2]))


# ==================================================
# 4. 自检: 解析 / v0 1D / 收敛 / 均匀性 / 能量
# ==================================================
def _selftest() -> None:
    print("=" * 78)
    print("shim_fem_plate 自检 (2D 极坐标 Mindlin 板 vs md 解析 / v0 1D)")
    print("=" * 78)
    p = 1e6                                   # 对比压强 1 MPa
    cases = {
        "单片 (6,10,0.1)":      [(6.0, 10.0, 0.1)],
        "等径×3 (6,10,0.1)":    [(6.0, 10.0, 0.1)] * 3,
        "锥形 10/9/8":          [(6.0, 10.0, 0.1), (6.0, 9.0, 0.1),
                                 (6.0, 8.0, 0.1)],
    }
    worst = 0.0
    for name, layers in cases.items():
        fem = PolarShimFEM(layers, n_elem_base_radial=100, n_theta=0)
        v0 = ShimStackCore([ShimLayer(*t) for t in layers], n_elem_base=400)
        tips_fem = fem.tip_deflections_unit() * p
        tips_1d = v0.edge_deflections(p)
        top = layers[0]
        h_an = analytic_edge_deflection(top[0], top[1], top[2], p)
        print("-" * 78)
        print(f"[{name}]  网格 {fem.n_cell_r}×{fem.n_theta} = "
              f"{fem.mesh['n_elem']} 单元, {fem.n_nodes} 节点, "
              f"DOF={fem.n_dof}, 组装 {fem.t_assemble * 1e3:.0f} ms, "
              f"求解 {fem.t_solve * 1e3:.0f} ms")
        rel_an = abs(tips_fem[0] - h_an) / h_an
        rel_1d = abs(tips_fem[0] - tips_1d[0]) / tips_1d[0]
        worst = max(worst, rel_1d)
        print(f"  顶片外缘 h: FEM={tips_fem[0] * 1e3:.6f} mm | "
              f"解析={h_an * 1e3:.6f} mm (rel={rel_an:.2e}) | "
              f"v0-1D={tips_1d[0] * 1e3:.6f} mm (rel={rel_1d:.2e})")
        for i in range(1, len(layers)):
            rel_i = abs(tips_fem[i] - tips_1d[i]) / tips_1d[i]
            worst = max(worst, rel_i)
            print(f"  第{i + 1}片: FEM={tips_fem[i] * 1e3:.6f} mm | "
                  f"v0-1D={tips_1d[i] * 1e3:.6f} mm (rel={rel_i:.2e})")
        uni = fem.ring_uniformity()
        print(f"  环向均匀性 (max|Δw|/w̄) = {uni:.2e}  "
              f"| 能量 ½uᵀKu vs ½fᵀu rel = "
              f"{abs(fem.E_int - fem.E_ext) / fem.E_ext:.2e}")

        # ---- 断言 (Mindlin 剪切为物理修正, 容差相应放宽) ----
        if len(layers) == 1:
            assert rel_an < 5e-3, f"单片 vs 解析 rel={rel_an:.2e}"
        else:
            assert rel_1d < 2e-2, f"{name} vs v0-1D rel={rel_1d:.2e}"
        assert uni < 1e-6, f"环向均匀性 {uni:.2e}"
        assert fem.solve_energy_ok(), "能量守恒失败"

    print("-" * 78)
    print(f"最坏 FEM vs v0-1D 相对差 = {worst:.2e}")

    # ---- 网格收敛: 单片, 固定 nθ=120, 径向 50→100→200 ----
    hs = []
    for nr in (50, 100, 200):
        f = PolarShimFEM([(6.0, 10.0, 0.1)], n_elem_base_radial=nr,
                         n_theta=120)
        hs.append(f.tip_deflections_unit()[0] * p)
    conv = abs(hs[2] - hs[1]) / hs[1]
    print(f"收敛 (单片, nθ=120): nr=50:{hs[0] * 1e3:.6f} "
          f"100:{hs[1] * 1e3:.6f} 200:{hs[2] * 1e3:.6f} mm | "
          f"100→200 rel={conv:.2e}")
    assert conv < 2e-3, f"网格收敛不足 {conv:.2e}"

    # ---- 总压力守恒: Σf = p·A_face (A=π(b₀²−a₀²)) ----
    a0, b0 = 3e-3, 5e-3
    a_mesh = math.pi * (b0 ** 2 - a0 ** 2)
    fem1 = PolarShimFEM([(6.0, 10.0, 0.1)], n_elem_base_radial=100, n_theta=0)
    relF = abs(fem1.force_total - a_mesh) / a_mesh
    print(f"总压力守恒: Σf={fem1.force_total:.6e} m² (p=1Pa) vs "
          f"A={a_mesh:.6e} m²  rel={relF:.2e}")
    assert relF < 1e-3, f"总压力守恒失败 rel={relF:.2e}"
    print("自检: 全部断言通过 ✓")


if __name__ == "__main__":
    _selftest()
