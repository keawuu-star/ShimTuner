# -*- coding: utf-8 -*-
"""
ShimTuner 大挠度 2D 极坐标 FEM — von Kármán Mindlin 板 (含膜拉伸)
==================================================================
定位: shim_fem_plate.PolarShimFEM 的**几何非线性升级版**。网格与单元几何
      完全不变 (技能 polar quad 网格), 只升级应变度量与本构层级 —— 即
      "中面壳网格 + 大挠度修正", 而不是把网格加厚。

新增物理 (相对线性 PolarShimFEM):
  1. **膜自由度 (u,v)**: 线性版只有 (w,φx,φy), 无面内变形能力 →
     大挠度下的中面拉伸刚化 (主因) 完全缺失。
  2. **von Kármán 膜应变**:
        εxx = u,x + ½(w,x)²
        εyy = v,y + ½(w,y)²
        γxy = u,y + v,x + w,x·w,y
     (w,α)² 项即大挠度几何非线性来源, 中等转动域 (w/t ≲ 2~3, 斜率 ≲ 10°)
     内精度良好; 超出后需全 Green-Lagrange (见文档)。
  3. **切线 = 材料切线 + 几何切线** K = ∫BmᵀABm + ∫BbᵀDBb + ∫BsᵀSBs + ∫GᵀNσG
  4. **follower 压力 (可选)**: 压力随变形后法线施加, 领先项
        δW = ∫p[(1+½|∇w|²)δw − w,x δu − w,y δv]dA
     (死载荷 = 只用第一项 1·δw; 大斜率时 follower 才显著)

层合刚度 (与"层间无摩擦/可自由滑移"假设一致, B=0):
  A = Σ E tᵢ/(1−ν²)      [N/m]    膜刚度
  B = 0                            (无摩擦滑移 → 无膜-弯耦合)
  D = Σ E tᵢ³/12(1−ν²)   [N·m]    弯曲刚度 (与 v0/PolarShimFEM 一致)
  S = k·G·Σtᵢ, k=5/6     [N/m]    横向剪切
  !! 若将来考虑层间摩擦/粘合, 应改为 B≠0 且 D 含 Σtᵢz̄ᵢ² 项 (→(Σt)³ 上限)。

DOF/节点 = 5: (u, v, w, φx, φy); 节点 global = i·nθ + j。
边界: clamp 圆整圈 5 DOF 全固支; 外缘自由。
求解: 载荷步进 + Newton (自适应对分续载), 切线一致性经有限差分自检。

自检 (python -X utf8 shim_nl_plate.py):
  * nl=False vs 线性 PolarShimFEM (同网格同积分, 应逐位吻合)
  * 切线 vs 有限差分 (残差雅可比一致性)
  * nl=True 载荷扫描: 挠度低于线性解 (刚化), 刚度增强因子单调上升
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

__all__ = ["PolarShimNLPlate", "KSHEAR", "NDOF_PER_NODE"]

KSHEAR = 5.0 / 6.0
NDOF_PER_NODE = 5          # u, v, w, phix, phiy


def _triple(x):
    if isinstance(x, dict):
        return (float(x["d_in_mm"]), float(x["d_out_mm"]), float(x["t_mm"]))
    try:
        return (float(x.d_in_mm), float(x.d_out_mm), float(x.t_mm))
    except AttributeError:
        return (float(x[0]), float(x[1]), float(x[2]))


class PolarShimNLPlate:
    """
    大挠度 (von Kármán) 极坐标 Mindlin 板: 层合堆叠 -> 载荷-挠度非线性解。

    nl=False 时退化为线性膜+弯+剪 (应与 PolarShimFEM 一致, 用作回归 gate)。
    """

    def __init__(self, layers_mm: Sequence, E: float = STEEL_7C27MO2_E,
                 nu: float = STEEL_7C27MO2_NU,
                 n_elem_base_radial: int = 60, n_theta: int = 48,
                 clamp_radius_mm: float | None = None,
                 nl: bool = True, follower: bool = False):
        self.layers = [l if isinstance(l, ShimLayer) else ShimLayer(*_triple(l))
                       for l in layers_mm]
        for l in self.layers:
            l.validate()
        self.E, self.nu, self.nl, self.follower = E, nu, bool(nl), bool(follower)

        clamp_mm = (clamp_radius_mm if clamp_radius_mm is not None
                    else min(l.d_in_mm for l in self.layers) / 2.0)
        self.mesh = build_polar_mesh(self.layers, E=E, nu=nu,
                                     n_elem_base_radial=n_elem_base_radial,
                                     n_theta=n_theta,
                                     clamp_radius_mm=clamp_mm)
        probs = check_mesh_invariants(self.mesh)
        if probs:
            raise RuntimeError("网格不变量失败: " + "; ".join(probs))

        self.n_theta = self.mesh["n_theta"]
        self.n_cell_r = self.mesh["n_cell_r"]
        self.n_nodes = self.mesh["n_nodes"]
        self.n_dof = NDOF_PER_NODE * self.n_nodes

        # ---- 每条带层合刚度 A / D / S (B=0: 层间无摩擦可滑移) ----
        rm = self.mesh["rm_c"]
        tsum = np.zeros(self.n_cell_r)
        for l in self.layers:
            m = (rm >= l.a_m - 1e-12) & (rm <= l.b_m + 1e-12)
            tsum[m] += l.t_mm * 1e-3
        G = E / (2.0 * (1.0 + nu))
        self.A_c = E * tsum / (1.0 - nu ** 2)      # 膜刚度 N/m
        self.D_c = self.mesh["De_c"]               # 弯刚度 N·m (Σ t³)
        self.S_c = KSHEAR * G * tsum               # 剪切 N/m
        self._G_shear = G

        # ---- 几何/边界/载荷 预计算 ----
        self._precompute()
        self._setup_boundary()

    # ============================================================
    # 1. 预计算: 高斯点几何 + 单元自由度表
    # ============================================================
    def _node_xy(self) -> np.ndarray:
        th = self.mesh["theta_nodes"]
        r = self.mesh["r_nodes_m"]
        x = r[:, None] * np.cos(th)[None, :]
        y = r[:, None] * np.sin(th)[None, :]
        return np.stack([x.ravel(), y.ravel()], axis=1)

    def _precompute(self):
        mesh = self.mesh
        conn = mesh["conn"]                              # (M,4)
        self.conn = conn
        xy = self._node_xy()
        M = mesh["n_elem"]
        n_th = self.n_theta
        x = xy[conn, 0]
        y = xy[conn, 1]
        self.x, self.y = x, y
        xi_a = np.array([-1.0, 1.0, 1.0, -1.0])
        eta_a = np.array([-1.0, -1.0, 1.0, 1.0])
        q = 1.0 / math.sqrt(3.0)

        def geo(xi, eta):
            N = 0.25 * (1.0 + xi * xi_a) * (1.0 + eta * eta_a)
            dNxi = 0.25 * xi_a * (1.0 + eta * eta_a)
            dNeta = 0.25 * (1.0 + xi * xi_a) * eta_a
            xxi = x @ dNxi
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
            return N, dNx, dNy, np.abs(jd)

        self.gb = [geo(xi, eta) for xi, eta in
                   ((-q, -q), (q, -q), (q, q), (-q, q))]     # 弯曲/膜 2×2
        self.gs = geo(0.0, 0.0)                              # 剪切 1×1 (减缩)
        self.er_map = np.arange(M) // n_th                   # 单元→带号

        # 局部 DOF 表 (M,20): 节点 a 的 dof = a*5 + {0..4}
        ld = NDOF_PER_NODE * conn[:, :, None] + np.arange(NDOF_PER_NODE)
        self.ldof = ld.reshape(M, 4 * NDOF_PER_NODE)

        # 单位压强外载荷 (face 带, 作用在 w DOF): f = ∫ N_a dA
        face_c = mesh["face_c"]
        is_face = face_c[self.er_map]
        w_idx = NDOF_PER_NODE * conn + 2                 # (M,4) 四节点 w DOF
        f_unit = np.zeros(self.n_dof)
        for (N, dNx, dNy, ajd) in self.gb:
            fe = ajd[:, None] * N
            fe[~is_face] = 0.0
            np.add.at(f_unit, w_idx, fe)
        self.f_unit = f_unit

    def _setup_boundary(self):
        n_th = self.n_theta
        clamp_nodes = self.mesh["clamp_node"] * n_th + np.arange(n_th)
        cd = (NDOF_PER_NODE * clamp_nodes[:, None]
              + np.arange(NDOF_PER_NODE)[None, :]).ravel()
        self.clamped = cd
        self.free = np.ones(self.n_dof, dtype=bool)
        self.free[cd] = False
        self.rmap = np.full(self.n_dof, -1, dtype=np.int64)
        self.rmap[self.free] = np.arange(int(self.free.sum()))

    # ============================================================
    # 2. 组装: 残差 + 切线 (给定位移 U, 压强 p)
    # ============================================================
    def _elem_dofs(self, U):
        return U[self.ldof]                               # (M,20)

    def assemble(self, U: np.ndarray, p: float, want_K: bool = True):
        """返回 (r_red, K_red or None). r = f_int − f_ext."""
        M = self.mesh["n_elem"]
        Amat_base = np.array([[1.0, self.nu, 0.0],
                              [self.nu, 1.0, 0.0],
                              [0.0, 0.0, (1.0 - self.nu) / 2.0]])
        Dmat_base = Amat_base
        A_e = self.A_c[self.er_map] if self.nl else self.A_c[self.er_map]
        D_e = self.D_c[self.er_map]
        S_e = self.S_c[self.er_map]
        Amat = Amat_base[None] * A_e[:, None, None]
        Dmat = Dmat_base[None] * D_e[:, None, None]

        ue = self._elem_dofs(U)
        u_e = ue[:, 0::5]
        v_e = ue[:, 1::5]
        w_e = ue[:, 2::5]
        px_e = ue[:, 3::5]
        py_e = ue[:, 4::5]

        f_int = np.zeros((M, 4 * NDOF_PER_NODE))
        Ke = np.zeros((M, 4 * NDOF_PER_NODE, 4 * NDOF_PER_NODE)) if want_K \
            else None
        f_follow = np.zeros((M, 4 * NDOF_PER_NODE))
        K_follow = np.zeros_like(Ke) if (want_K and self.follower
                                         and p != 0.0) else None

        for (N, dNx, dNy, ajd) in self.gb:
            wx = np.einsum("ma,ma->m", dNx, w_e)
            wy = np.einsum("ma,ma->m", dNy, w_e)

            # ---- 膜 B (3×20): 应变用 B_eps (非线性项带 ½),
            #      残差/切线用其雅可比 B_jac = ∂ε/∂u (非线性项不带 ½) ----
            Blin = np.zeros((M, 3, 4 * NDOF_PER_NODE))
            Blin[:, 0, 0::5] = dNx
            Blin[:, 1, 1::5] = dNy
            Blin[:, 2, 0::5] = dNy
            Blin[:, 2, 1::5] = dNx
            B_eps = Blin.copy()
            B_jac = Blin.copy()
            if self.nl:
                wcol_x = wx[:, None] * dNx          # ∂εxx/∂w_b (full)
                wcol_y = wy[:, None] * dNy          # ∂εyy/∂w_b (full)
                wcol_xy = (wy[:, None] * dNx
                           + wx[:, None] * dNy)     # ∂γxy/∂w_b (full)
                B_eps[:, 0, 2::5] = 0.5 * wcol_x
                B_eps[:, 1, 2::5] = 0.5 * wcol_y
                B_eps[:, 2, 2::5] = 0.5 * wcol_xy
                B_jac[:, 0, 2::5] = wcol_x
                B_jac[:, 1, 2::5] = wcol_y
                B_jac[:, 2, 2::5] = wcol_xy

            # ---- 弯 B (3×20) ----
            Bb = np.zeros((M, 3, 4 * NDOF_PER_NODE))
            Bb[:, 0, 3::5] = dNx
            Bb[:, 1, 4::5] = dNy
            Bb[:, 2, 3::5] = dNy
            Bb[:, 2, 4::5] = dNx

            # ---- 内力: 膜 (B_jacᵀ·N) + 弯 ----
            eps = np.einsum("mij,mj->mi", B_eps, ue)
            Nmem = np.einsum("mij,mj->mi", Amat, eps)
            kap = np.einsum("mij,mj->mi", Bb, ue)
            Mom = np.einsum("mij,mj->mi", Dmat, kap)
            BjT = np.transpose(B_jac, (0, 2, 1))
            BbT = np.transpose(Bb, (0, 2, 1))
            f_int += (np.einsum("mij,mj->mi", BjT, Nmem)
                      + np.einsum("mij,mj->mi", BbT, Mom)) * ajd[:, None]

            if want_K:
                # 材料切线: 膜 (B_jacᵀ A B_jac) + 弯
                Ke += ajd[:, None, None] * (
                    np.einsum("mij,mjk,mkl->mil", BjT, Amat, B_jac)
                    + np.einsum("mij,mjk,mkl->mil", BbT, Dmat, Bb))
                # 几何切线: Gᵀ Nσ G (Nσ 为膜应力合力)
                if self.nl:
                    G = np.zeros((M, 2, 4 * NDOF_PER_NODE))
                    G[:, 0, 2::5] = dNx
                    G[:, 1, 2::5] = dNy
                    GT = np.transpose(G, (0, 2, 1))
                    Nsig = np.zeros((M, 2, 2))
                    Nsig[:, 0, 0] = Nmem[:, 0]
                    Nsig[:, 1, 1] = Nmem[:, 1]
                    Nsig[:, 0, 1] = Nsig[:, 1, 0] = Nmem[:, 2]
                    Ke += ajd[:, None, None] * np.einsum(
                        "mij,mjk,mkl->mil", GT, Nsig, G)

            # ---- follower 压力领先项 ----
            if self.follower and p != 0.0:
                half_sq = 0.5 * (wx ** 2 + wy ** 2)
                ff = np.zeros((M, 4 * NDOF_PER_NODE))
                ff[:, 2::5] = (half_sq * ajd)[:, None] * N
                ff[:, 0::5] = (-wx * ajd)[:, None] * N
                ff[:, 1::5] = (-wy * ajd)[:, None] * N
                f_follow += ff
                if K_follow is not None:
                    Df = np.zeros((M, 4 * NDOF_PER_NODE, 4 * NDOF_PER_NODE))
                    # ∂f_w/∂w: (wx dNx_b + wy dNy_b) N_a
                    gw = (wx[:, None] * dNx + wy[:, None] * dNy)   # (M,4)
                    Na = N[None, :, None]                          # (1,4,1)
                    Df[:, 2::5, 2::5] = (ajd[:, None, None] * Na
                                         * gw[:, None, :])
                    # ∂f_u/∂w = −dNx_b N_a ; ∂f_v/∂w = −dNy_b N_a
                    Df[:, 0::5, 2::5] = (-ajd[:, None, None] * Na
                                         * dNx[:, None, :])
                    Df[:, 1::5, 2::5] = (-ajd[:, None, None] * Na
                                         * dNy[:, None, :])
                    K_follow += Df

        # ---- 剪切 (减缩 1×1) ----
        N0, dNx0, dNy0, ajd0 = self.gs
        Bs = np.zeros((M, 2, 4 * NDOF_PER_NODE))
        Bs[:, 0, 2::5] = dNx0
        Bs[:, 0, 3::5] = N0
        Bs[:, 1, 2::5] = dNy0
        Bs[:, 1, 4::5] = N0
        BsT = np.transpose(Bs, (0, 2, 1))
        gam = np.einsum("mij,mj->mi", Bs, ue)
        Q = S_e[:, None] * gam
        f_int += (4.0 * ajd0[:, None]
                  * np.einsum("mij,mj->mi", BsT, Q))
        if want_K:
            Ke += (4.0 * ajd0[:, None, None] * S_e[:, None, None]
                   * np.einsum("mij,mjk->mik", BsT, Bs))

        # ---- 组装全局 ----
        f_int_full = np.zeros(self.n_dof)
        np.add.at(f_int_full, self.ldof, f_int)
        f_ext_full = p * self.f_unit
        if self.follower and p != 0.0:
            np.add.at(f_ext_full, self.ldof, p * f_follow)
        r_full = f_int_full - f_ext_full
        r = r_full[self.free]

        if not want_K:
            return r, None, f_int_full, f_ext_full

        if K_follow is not None:
            Ke = Ke - p * K_follow
        idx = self.rmap[self.ldof]
        keep = (idx[:, :, None] >= 0) & (idx[:, None, :] >= 0)
        I = np.broadcast_to(idx[:, :, None], Ke.shape)[keep]
        J = np.broadcast_to(idx[:, None, :], Ke.shape)[keep]
        K = sp.coo_matrix((Ke[keep], (I, J)),
                          shape=(int(self.free.sum()),
                                 int(self.free.sum()))).tocsc()
        return r, K, f_int_full, f_ext_full

    # ============================================================
    # 3. Newton + 载荷步进
    # ============================================================
    def solve(self, p_target: float, n_steps: int = 8,
              max_iter: int = 30, tol: float = 1e-6,
              verbose: bool = False):
        """从零加载到 p_target (冷启动), 返回 (U, info)."""
        return self._load_to(np.zeros(self.n_dof), 0.0, p_target,
                             n_steps, max_iter, tol, verbose)

    def continue_to(self, p_target: float, n_steps: int = 4,
                    max_iter: int = 30, tol: float = 1e-6,
                    verbose: bool = False):
        """热启动: 从上次收敛态继续加载到 p_target (须 p_target ≥ 上次值)."""
        if not hasattr(self, "U") or p_target < getattr(self, "p_last", 0.0):
            return self.solve(p_target, n_steps, max_iter, tol, verbose)
        return self._load_to(self.U.copy(), self.p_last, p_target,
                             n_steps, max_iter, tol, verbose)

    def solve_curve(self, p_list, n_steps: int = 4, tol: float = 1e-6,
                    verbose: bool = False):
        """按 (递增) 压强序列求解 h(p) 曲线, 逐点热启动. 返回 (nl, n_p)."""
        ps = np.asarray(p_list, dtype=float)
        out = np.zeros((len(self.layers), len(ps)))
        t0 = time.perf_counter()
        n_newton = 0
        n_steps_tot = 0
        for k, p in enumerate(ps):
            self.continue_to(float(p), n_steps=n_steps, tol=tol,
                             verbose=verbose)
            n_newton += self.info["n_newton"]
            n_steps_tot += self.info["n_steps"]
            out[:, k] = self.tip_deflections()
        self.curve_info = dict(t_total=time.perf_counter() - t0,
                               n_newton=n_newton, n_steps=n_steps_tot,
                               n_points=len(ps))
        return out

    def _load_to(self, U, p_cur, p_target, n_steps, max_iter, tol, verbose):
        hist = []
        t0 = time.perf_counter()
        step = (p_target - p_cur) / max(1, n_steps)
        n_newton = 0
        while p_cur < p_target - 1e-12 * abs(p_target):
            step = min(step, p_target - p_cur)
            ok, U_new, it = self._newton(U, p_cur + step, max_iter, tol)
            if ok:
                U, p_cur = U_new, p_cur + step
                n_newton += it
                hist.append((p_cur, it))
                if verbose:
                    print(f"  p={p_cur / 1e6:8.4f} MPa  Newton {it} it",
                          flush=True)
                step = min(step * 1.6, (p_target / max(1, n_steps)) * 4)
            else:
                step *= 0.5
                if step < 1e-9 * max(abs(p_target), 1.0):
                    raise RuntimeError(
                        f"载荷步进停滞于 p={p_cur / 1e6:.4f} MPa "
                        f"(目标 {p_target / 1e6:.4f} MPa)")
        self.U = U
        self.p_last = p_cur
        self.info = dict(t_total=time.perf_counter() - t0,
                         n_newton=n_newton, n_steps=len(hist), hist=hist)
        return U, self.info

    def _newton(self, U0, p, max_iter, tol):
        """
        纯 Newton 全步 + 发散守卫 + 噪声底停滞 (von Kármán u-膜耦合使
        第 1 步残差可暴涨数千倍, 单调回溯判据不可靠 —— 见会话记录经验)。
        """
        U = U0.copy()
        r, K, _, f_ext = self.assemble(U, p, want_K=True)
        nrm0 = np.linalg.norm(f_ext[self.free])
        if nrm0 == 0.0:
            return True, U, 0
        norm_u = max(1.0, float(np.linalg.norm(U0)))
        for it in range(1, max_iter + 1):
            rn = np.linalg.norm(r)
            if rn <= tol * nrm0:
                return True, U, it - 1
            try:
                du = spsolve(K, -r)
            except Exception:
                return False, U, it
            if not np.all(np.isfinite(du)):
                return False, U, it
            # 噪声底停滞收敛 (残差已到机器精度, 无法再降)
            if np.linalg.norm(du) <= 1e-12 * norm_u:
                return True, U, it - 1
            U = U.copy()
            U[self.free] += du
            r, K, _, _ = self.assemble(U, p, want_K=True)
            rn_new = np.linalg.norm(r)
            if (not np.isfinite(rn_new)
                    or rn_new > 1e10 * max(rn, nrm0)):
                return False, U, it          # 发散 → 交给载荷步对分
        return False, U, max_iter

    # ============================================================
    # 4. 结果接口
    # ============================================================
    def w_matrix(self) -> np.ndarray:
        w = self.U[2::NDOF_PER_NODE]
        return w.reshape(self.mesh["n_r"], self.n_theta)

    def tip_deflections(self) -> np.ndarray:
        rn = self.mesh["r_nodes_m"]
        wm = self.w_matrix()
        out = []
        for l in self.layers:
            i = int(np.argmin(np.abs(rn - l.b_m)))
            out.append(float(wm[i].mean()))
        return np.asarray(out)

    def profile(self):
        return self.mesh["r_nodes_m"], self.w_matrix().mean(axis=1)

    def ring_uniformity(self) -> float:
        wm = self.w_matrix()
        wb = wm.mean(axis=1)
        den = float(np.max(np.abs(wb))) or 1.0
        return float(np.max(np.abs(wm - wb[:, None]))) / den

    def tangent_fd_check(self, U: np.ndarray, p: float, eps: float = 1e-9):
        """切线一致性: 中心差分 vs 解析切线 (最大相对误差).

        注意 eps 必须远小于位移尺度, 否则是差分截断误差而非切线错误
        (实测 eps=1e-6 时截断误差 ~5e-3, eps=1e-9 时 ~1e-9)。
        """
        r0, K, _, f_ext = self.assemble(U, p, want_K=True)
        Kd = K.toarray()
        free_idx = np.where(self.free)[0]
        nf = len(free_idx)
        worst = 0.0
        cols = np.linspace(0, nf - 1, min(12, nf)).astype(int)
        for c in cols:
            Up, Um = U.copy(), U.copy()
            Up[free_idx[c]] += eps
            Um[free_idx[c]] -= eps
            fd = (self.assemble(Up, p, want_K=False)[0]
                  - self.assemble(Um, p, want_K=False)[0]) / (2.0 * eps)
            scale = np.linalg.norm(fd)
            if scale < 1e-14:
                continue
            worst = max(worst, np.linalg.norm(Kd[:, c] - fd) / scale)
        return worst


# ================================================================
# 自检
# ================================================================
def _selftest():
    print("=" * 78)
    print("shim_nl_plate 自检 (von Kármán 大挠度 vs 线性 / 切线一致性)")
    print("=" * 78)
    E, nu = STEEL_7C27MO2_E, STEEL_7C27MO2_NU
    layers = [(6.0, 10.0, 0.1)]

    # ---- 1) 线性退化解 vs PolarShimFEM ----
    from shim_fem_plate import PolarShimFEM
    nr, nt = 40, 48
    lin = PolarShimFEM(layers, E=E, nu=nu, n_elem_base_radial=nr, n_theta=nt)
    nl_off = PolarShimNLPlate(layers, E=E, nu=nu, n_elem_base_radial=nr,
                              n_theta=nt, nl=False)
    p = 1e5
    h_lin = float(lin.tip_deflections_unit()[0] * p)
    nl_off.solve(p, n_steps=1, tol=1e-9)
    h_nl_off = float(nl_off.tip_deflections()[0])
    rel = abs(h_nl_off - h_lin) / abs(h_lin)
    print(f"[1] 线性退化 gate:  NLPlate(nl=False) = {h_nl_off * 1e3:.8f} mm | "
          f"PolarShimFEM = {h_lin * 1e3:.8f} mm | rel = {rel:.2e}")
    assert rel < 1e-8, f"线性退化不一致 rel={rel:.3e}"

    # ---- 2) 切线一致性 ----
    nl = PolarShimNLPlate(layers, E=E, nu=nu, n_elem_base_radial=12,
                          n_theta=12, nl=True)
    U = np.zeros(nl.n_dof)
    rng = np.random.default_rng(0)
    U[nl.free] = rng.normal(0.0, 1e-7, int(nl.free.sum()))
    err = nl.tangent_fd_check(U, 1e5)
    print(f"[2] 切线 vs 有限差分: 最大相对误差 = {err:.2e} "
          f"(应 < 1e-5)")
    assert err < 1e-5, f"切线不一致 err={err:.3e}"

    # ---- 3) 大挠度: 刚化 (w 低于线性), 刚度增强因子单调 ----
    nl_big = PolarShimNLPlate(layers, E=E, nu=nu, n_elem_base_radial=20,
                              n_theta=24, nl=True)
    v0 = ShimStackCore([ShimLayer(*t) for t in layers], n_elem_base=400)
    h_an = lambda pp: analytic_edge_deflection(6.0, 10.0, 0.1, pp, E, nu)
    print(f"[3] 大挠度载荷扫描 (单片 Ø10/Ø6 t=0.1):")
    print(f"    {'p(MPa)':>7} {'w_NL(mm)':>10} {'w_lin(mm)':>10} "
          f"{'w/t':>7} {'K_NL/K_lin':>10} {'h/t':>6}")
    prev_ratio = 0.0
    for p_mpa in (0.05, 0.2, 0.5, 1.0, 2.0, 4.0):
        pp = p_mpa * 1e6
        nl_big.solve(pp, n_steps=6, tol=1e-7)
        w_nl = float(abs(nl_big.tip_deflections()[0]))
        w_lin = h_an(pp)
        ratio = w_lin / w_nl
        print(f"    {p_mpa:7.3f} {w_nl * 1e3:10.5f} {w_lin * 1e3:10.5f} "
              f"{w_nl / 1e-4:7.3f} {ratio:10.4f} {w_nl / 1e-4:6.2f}")
        assert w_nl <= w_lin * (1.0 + 1e-6), "大挠度应刚化 (w ≤ 线性)"
        assert ratio >= prev_ratio - 1e-3, "刚度增强因子应单调不减"
        prev_ratio = ratio
    print(f"    最终刚度增强 {prev_ratio:.3f}× (相对单片线性解)")
    assert prev_ratio > 1.05, "大挠度刚化效应不可见 — 检查非线性项"

    # ---- 4) 环向均匀性 (轴对称载荷应 θ 无关) ----
    print(f"[4] 环向均匀性 = {nl_big.ring_uniformity():.2e}")
    assert nl_big.ring_uniformity() < 1e-6
    print("自检: 全部断言通过 ✓")


if __name__ == "__main__":
    _selftest()
