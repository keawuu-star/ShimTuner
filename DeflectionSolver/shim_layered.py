# -*- coding: utf-8 -*-
"""
2D 极坐标分层 Mindlin 板 — 逐层中面 + 层间法向罚接触 (μ=0)
============================================================
定位: `shim_fem_plate.PolarShimFEM` / `shim_nl_plate.PolarShimNLPlate`（共中面
      等效阶梯刚度板）的**分层升级版**。网格、单元（Q4 Mindlin, 剪切 1×1 减缩）
      与 `shim_element` 完全共用，唯一区别:
        * 每层有自己的中面位移场 (u,v,w,φx,φy)，不再压成单张等效板;
        * 层序约定：layers[0] = 最上层（默认 load_layer=0，顶面受压）；
          FlowSolver 使用 layers[0]=贴活塞第1片，故反转层序后令 load_layer=最底层。
        * 层间只有**法向罚接触**（可分离、不传切向力 = μ=0）;
        * 每层在自己的内缘 max(clamp, aᵢ) 处固支。

项目决策 (2026-11-02): 不计层间摩擦。阀片浸入油液工作（油膜润滑、摩擦小），
且摩擦引入路径依赖/滞回等大量非线性，收益有限 —— μ=0 即最终物理模型，
不是待补的短板（理由见 ShimTunerPhysics.md「设计决策」）。

为什么等效板会偏硬:
  等效板只有一条 w(r)，等于强制所有层"等挠度 + 等曲率"（比单边接触约束更强）。
  锥形堆叠的外伸段（上层比下层长出的部分）不受下层支撑，各层曲率本可不同，
  分层模型应给出更大的挠度 —— 本模块用来量化这个偏差。

接触几何（厚度偏移隐式处理）:
  各层中面初始间距 = (tᵢ+tᵢ₊₁)/2；取 w 为相对该初始堆叠状态的位移，则
  两中面真实间隙 = w_bottom − w_top（≥0 分离；<0 穿透）。
  —— 不需要 z 坐标或半厚/转角修正。

离散: 接触对 = 相邻层在共同活动径向节点上的 (r,θ) 节点对；罚力 f = k_n·g·A_node，
      活动集按 g<0 判定（只按穿透判据，不按乘子符号，避免乘子噪声）。

自检 (python -X utf8 shim_layered.py):
  * nl=False, L=1 → 与 PolarShimFEM 逐位一致
  * 同径 n 片 → 与 ΣD 等效板一致（罚刚度允许误差）
  * 锥形 → h_layered ≥ h_equivalent，打印 Δ
  * 穿透 / k_n 敏感性 / 环向均匀性 / 网格收敛
"""
from __future__ import annotations

import math
import time

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve, splu

from shim_physics_core import (ShimLayer, ShimStackCore,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)
from shim_stack_mesh import build_polar_mesh, check_mesh_invariants
from shim_element import (NDOF_PER_NODE, GAUSS_2X2, GAUSS_1X1,
                          q4_geo, membrane_B, bending_B, shear_B)

__all__ = ["LayeredStack"]

KSHEAR = 5.0 / 6.0
_TOL_GAP = 1e-12          # 活动集判据间隙容差 [m]


def _triple(x):
    if isinstance(x, dict):
        return (float(x["d_in_mm"]), float(x["d_out_mm"]), float(x["t_mm"]))
    try:
        return (float(x.d_in_mm), float(x.d_out_mm), float(x.t_mm))
    except AttributeError:
        return (float(x[0]), float(x[1]), float(x[2]))


class LayeredStack:
    """分层 Mindlin 板: 每层独立中面 + 相邻层法向罚接触."""

    def __init__(self, layers, E: float = STEEL_7C27MO2_E,
                 nu: float = STEEL_7C27MO2_NU,
                 n_elem_base_radial: int = 40, n_theta: int = 48,
                 clamp_radius_mm: float | None = None,
                 k_n: float = 1e15, max_outer_iter: int = 20,
                 nl: bool = False, follower: bool = False,
                 sector: int = 1, load_layer: int = 0):
        self.layers = [l if isinstance(l, ShimLayer) else ShimLayer(*_triple(l))
                       for l in layers]
        if not self.layers:
            raise ValueError("至少需要一片阀片")
        for l in self.layers:
            l.validate()
        self.E, self.nu = E, nu
        self.k_n = float(k_n)
        self.max_outer_iter = int(max_outer_iter)
        self.nl = bool(nl)
        self.follower = bool(follower)
        # 受压层：默认 0（最上层，经典"顶片受压"用法）；
        # FlowSolver 用 layers[0]=贴活塞第1片的约定，会反转后令 load_layer=最底层。
        self.load_layer = int(load_layer)

        clamp_mm = (clamp_radius_mm if clamp_radius_mm is not None
                    else min(l.d_in_mm for l in self.layers) / 2.0)
        self.clamp_m = clamp_mm * 1e-3
        self.mesh = build_polar_mesh(self.layers, E=E, nu=nu,
                                     n_elem_base_radial=n_elem_base_radial,
                                     n_theta=n_theta,
                                     clamp_radius_mm=clamp_mm,
                                     sector=sector)
        probs = check_mesh_invariants(self.mesh)
        if probs:
            raise RuntimeError("网格不变量失败: " + "; ".join(probs))

        self.n_theta = self.mesh["n_theta"]
        self.n_theta_nodes = self.mesh.get("n_theta_nodes", self.n_theta)
        self.sector = int(self.mesh.get("sector", 1))
        self.periodic = bool(self.mesh.get("periodic", False))
        self.n_r = self.mesh["n_r"]
        self.n_nodes = self.mesh["n_nodes"]
        self.M = self.mesh["n_elem"]
        self.conn = self.mesh["conn"]
        self.r_nodes = self.mesh["r_nodes_m"]
        self.xy = self._node_xy()
        self.x = self.xy[self.conn, 0]
        self.y = self.xy[self.conn, 1]
        self.gb = q4_geo(self.x, self.y, GAUSS_2X2)
        self.gs = q4_geo(self.x, self.y, GAUSS_1X1)[0]
        self.er_map = np.arange(self.M) // self.n_theta

        if not (0 <= self.load_layer < len(self.layers)):
            raise ValueError("load_layer 超出层数范围")
        self._build_layers()
        self._build_contact()
        self._build_load()

    # ---------------- 几何 ----------------
    def _node_xy(self):
        th = self.mesh["theta_nodes"]
        r = self.r_nodes
        return np.stack([(r[:, None] * np.cos(th)[None, :]).ravel(),
                         (r[:, None] * np.sin(th)[None, :]).ravel()], axis=1)

    def _build_layers(self):
        L = len(self.layers)
        self.loc = []
        self.offset = [0]
        self.nodes_i, self.elem_i = [], []
        self.D_i, self.S_i, self.A_i = [], [], []
        G = self.E / (2.0 * (1.0 + self.nu))
        for l in self.layers:
            r_in = max(self.clamp_m, l.a_m)
            mask_r = (self.r_nodes >= r_in - 1e-12) & (self.r_nodes <= l.b_m + 1e-12)
            idx_r = np.where(mask_r)[0]
            if idx_r.size < 2:
                raise ValueError(f"层 Ø{l.d_out_mm:g} 在 clamp 之外没有可用节点")
            nodes = (idx_r[:, None] * self.n_theta_nodes
                     + np.arange(self.n_theta_nodes)[None, :]).ravel()
            self.nodes_i.append(nodes)
            loc = np.full(self.n_nodes, -1, dtype=np.int64)
            loc[nodes] = np.arange(nodes.size)
            self.loc.append(loc)
            self.offset.append(self.offset[-1] + NDOF_PER_NODE * nodes.size)
            # 单元: 径向带中点落在 [r_in, b_i]
            rm = self.mesh["rm_c"]
            keep_er = np.where((rm >= r_in - 1e-12) & (rm <= l.b_m + 1e-12))[0]
            e = np.concatenate([er * self.n_theta + np.arange(self.n_theta)
                                for er in keep_er]) if keep_er.size else np.array([], dtype=int)
            self.elem_i.append(e)
            t = l.t_mm * 1e-3
            self.D_i.append(self.E * t ** 3 / (12.0 * (1.0 - self.nu ** 2)))
            self.S_i.append(KSHEAR * G * t)
            self.A_i.append(self.E * t / (1.0 - self.nu ** 2))
        self.n_dof = int(self.offset[-1])

        # 每层局部 DOF 表 (n_nodes,5) → 全局 dof; 非活动为 -1
        self.ldof = []
        for i in range(L):
            base = self.offset[i]
            ld = NDOF_PER_NODE * self.loc[i][:, None] + np.arange(NDOF_PER_NODE)
            ld[self.loc[i] < 0] = -1
            ld = np.where(ld >= 0, ld + base, -1)
            self.ldof.append(ld)
        # 固支: 每层内缘径向节点 (第一行活动节点) 全部 5 DOF
        self.clamped = []
        for i, l in enumerate(self.layers):
            r_in = max(self.clamp_m, l.a_m)
            ir = int(np.argmin(np.abs(self.r_nodes - r_in)))
            nds = ir * self.n_theta_nodes + np.arange(self.n_theta_nodes)
            d = self.ldof[i][nds].ravel()
            self.clamped.append(d[d >= 0])
        self.clamped = np.concatenate(self.clamped) if self.clamped else np.array([], dtype=int)
        self.free = np.ones(self.n_dof, dtype=bool)
        self.free[self.clamped] = False
        # dof_rep: 装配内力/载荷用的自由度索引（约束=-1）
        self.dof_rep = np.arange(self.n_dof, dtype=np.int64)
        self.dof_rep[self.clamped] = -1
        self.rmap = np.full(self.n_dof, -1, dtype=np.int64)
        self.rmap[self.free] = np.arange(int(self.free.sum()))
        # ---- 周期扇区：Cartesian DOF 的旋转周期约束 u_s = R(span)·u_m ----
        # N 重周期场: u(r,θ+2π/N) = R(2π/N)·u(r,θ)（u,v 与 φx,φy 旋转，w 不变）
        self._P = None
        self._P_T = None
        self._ring_s = np.zeros((0, NDOF_PER_NODE), dtype=np.int64)
        self._ring_m = np.zeros((0, NDOF_PER_NODE), dtype=np.int64)
        self._pc, self._ps = 1.0, 0.0
        if self.periodic:
            span = 2.0 * math.pi / float(self.sector)
            self._pc, self._ps = math.cos(span), math.sin(span)
            s_ids, m_ids = [], []
            for i in range(L):
                arr = self.nodes_i[i].reshape(-1, self.n_theta_nodes)
                ms = self.ldof[i][arr[:, 0], :]
                ss = self.ldof[i][arr[:, -1], :]
                ok = (ms >= 0).all(axis=1) & (ss >= 0).all(axis=1)
                if np.any(ok):
                    s_ids.append(ss[ok])
                    m_ids.append(ms[ok])
            if s_ids:
                s_all = np.concatenate(s_ids, axis=0)
                m_all = np.concatenate(m_ids, axis=0)
                okr = ((self.rmap[s_all] >= 0).all(axis=1)
                       & (self.rmap[m_all] >= 0).all(axis=1))
                self._ring_s = s_all[okr]
                self._ring_m = m_all[okr]
            nf = int(self.free.sum())
            slave_red = self.rmap[np.unique(self._ring_s)]
            qmask = np.ones(nf, dtype=bool)
            qmask[slave_red] = False
            qidx = np.full(nf, -1, dtype=np.int64)
            qidx[qmask] = np.arange(int(qmask.sum()))
            c, s = self._pc, self._ps
            rows, cols, vals = [], [], []
            for ss, ms in zip(self._ring_s, self._ring_m):
                r_u, r_v, r_w, r_px, r_py = self.rmap[ss]
                q_u, q_v = qidx[self.rmap[ms[0]]], qidx[self.rmap[ms[1]]]
                q_w = qidx[self.rmap[ms[2]]]
                q_px, q_py = qidx[self.rmap[ms[3]]], qidx[self.rmap[ms[4]]]
                rows += [r_u, r_u, r_v, r_v]
                cols += [q_u, q_v, q_u, q_v]
                vals += [c, -s, s, c]
                rows.append(r_w); cols.append(q_w); vals.append(1.0)
                rows += [r_px, r_px, r_py, r_py]
                cols += [q_px, q_py, q_px, q_py]
                vals += [c, -s, s, c]
            # 主/内部自由度：恒等行（每个 q 都被使用）
            m_red = np.where(qmask)[0]
            rows.extend(m_red.tolist())
            cols.extend(qidx[m_red].tolist())
            vals.extend([1.0] * m_red.size)
            self._P = sp.coo_matrix((vals, (rows, cols)),
                                    shape=(nf, int(qmask.sum()))).tocsc()
            self._P_T = self._P.T.tocsr()

    def _sync_periodic(self, U):
        """把 θ=span 从节点置为 θ=0 主节点的旋转像（周期约束）."""
        if not self.periodic or self._ring_s.size == 0:
            return U
        ss, mm = self._ring_s, self._ring_m
        c, s = self._pc, self._ps
        U[ss[:, 0]] = c * U[mm[:, 0]] - s * U[mm[:, 1]]
        U[ss[:, 1]] = s * U[mm[:, 0]] + c * U[mm[:, 1]]
        U[ss[:, 2]] = U[mm[:, 2]]
        U[ss[:, 3]] = c * U[mm[:, 3]] - s * U[mm[:, 4]]
        U[ss[:, 4]] = s * U[mm[:, 3]] + c * U[mm[:, 4]]
        return U

    # ---------------- 接触 ----------------
    def _build_contact(self):
        # 节点 tributary 面积 (用于罚刚度加权)
        area_node = np.zeros(self.n_nodes)
        np.add.at(area_node, self.conn.ravel(),
                  np.repeat(self.mesh["area_e"] / 4.0, 4))
        self.area_node = area_node
        ct, cb, w, ar = [], [], [], []
        for i in range(len(self.layers) - 1):
            a, b = self.loc[i], self.loc[i + 1]
            both = np.where((a >= 0) & (b >= 0))[0]
            # 排除固支节点
            for i2 in (i, i + 1):
                r_in = max(self.clamp_m, self.layers[i2].a_m)
                ir = int(np.argmin(np.abs(self.r_nodes - r_in)))
                both = both[both // self.n_theta_nodes != ir]
            for n in both:
                ct.append(self.ldof[i][n, 2])
                cb.append(self.ldof[i + 1][n, 2])
                w.append(self.k_n * area_node[n])
                ar.append(area_node[n])
        self.ct = np.asarray(ct, dtype=np.int64)
        self.cb = np.asarray(cb, dtype=np.int64)
        self.cw = np.asarray(w, dtype=float)
        self.carea = np.asarray(ar, dtype=float)
        self.n_pairs = len(self.ct)

        # ---- 线性求解缓存（nl=False 时弹性刚度与载荷/接触状态无关）----
        self._K_elastic = None      # 缓存的弹性刚度 CSC
        self._lu = None             # 当前 active set 的 LU 分解
        self._lu_key = None         # active set 的键（packbits）
        self._active_guess = None   # 上一次求解的接触活动集（热启动）
        self._prep = None           # 非线性装配的常值单元数据缓存

    # ---------------- 载荷 ----------------
    def _load_face_mask(self) -> np.ndarray:
        """load_layer 的单元载荷面掩膜（按该层径向覆盖，而非固定顶片）."""
        li = self.load_layer
        layer = self.layers[li]
        r_in = max(self.clamp_m, layer.a_m)
        band = ((self.mesh["rm_c"] >= r_in - 1e-12)
                & (self.mesh["rm_c"] <= layer.b_m + 1e-12))
        face_c = np.asarray(band, dtype=bool)
        face_e = np.repeat(face_c, self.n_theta)
        mask = np.zeros(self.M, dtype=bool)
        mask[self.elem_i[li]] = True
        mask &= face_e
        return mask

    def _build_load(self):
        keep = self._load_face_mask()
        li = self.load_layer
        top_conn = self.conn[keep]
        top_w = self.ldof[li][top_conn][:, :, 2]          # (Mt,4)
        f_unit = np.zeros(self.n_dof)
        for (N, dNx, dNy, ajd) in self.gb:
            fe = (ajd[:, None] * N)
            fe[~keep] = 0.0
            rep_w = self.dof_rep[top_w]
            ok = rep_w >= 0
            np.add.at(f_unit, rep_w[ok], fe[keep][ok])
        self.f_unit = f_unit

    def set_pressure(self, pressure):
        """把顶片载荷替换为任意分布 p(r, θ) [Pa]（迭代耦合用）.

        pressure(r_m, theta_rad) 返回标量或与 r 同形的数组；载荷按
        ∫ N·p dA 装配。设置后用 ``solve(1.0)`` 求解（此时 f_unit 已是 N）。
        传负压即等效于反向（例如从阀片底面把阀片顶开）。
        """
        keep = self._load_face_mask()
        li = self.load_layer
        rbar = self.mesh["rbar_e"][keep]
        thbar = self.mesh["thbar_e"][keep]
        p_e = np.asarray(pressure(rbar, thbar), dtype=float)
        if p_e.ndim == 0:
            p_e = np.full(rbar.shape, float(p_e))
        if p_e.shape != rbar.shape:
            raise ValueError("pressure() 返回值形状必须与 r 一致")
        top_w = self.ldof[li][self.conn[keep]][:, :, 2]
        f = np.zeros(self.n_dof)
        for (N, dNx, dNy, ajd) in self.gb:
            fe = ajd[keep][:, None] * N[None, :] * p_e[:, None]
            rep_w = self.dof_rep[top_w]
            ok = rep_w >= 0
            np.add.at(f, rep_w[ok], fe[ok])
        self.f_unit = f
        return f

    # ---------------- 组装 ----------------
    def assemble(self, U, p, want_K=True, active=None):
        if active is None:
            active = self.active
        if self.nl:
            return self._assemble_nl(U, p, want_K, active)
        nd = self.n_dof
        f_int = np.zeros(nd)
        rows, cols, vals = [], [], []
        Amat_base = np.array([[1.0, self.nu, 0.0],
                              [self.nu, 1.0, 0.0],
                              [0.0, 0.0, (1.0 - self.nu) / 2.0]])
        for i in range(len(self.layers)):
            e = self.elem_i[i]
            if e.size == 0:
                continue
            conn = self.conn[e]
            ld = self.ldof[i][conn].reshape(e.size, 4 * NDOF_PER_NODE)
            ue = U[ld]
            A = self.A_i[i]
            D = self.D_i[i]
            S = self.S_i[i]
            f_e = np.zeros((e.size, 4 * NDOF_PER_NODE))
            Ke = np.zeros((e.size, 4 * NDOF_PER_NODE, 4 * NDOF_PER_NODE)) if want_K else None
            for (N, dNx_, dNy_, ajd_) in self.gb:
                dNx, dNy, ajd = dNx_[e], dNy_[e], ajd_[e]
                w_e = ue[:, 2::5]
                wx = np.einsum("ma,ma->m", dNx, w_e)
                wy = np.einsum("ma,ma->m", dNy, w_e)
                B_eps, B_jac = membrane_B(dNx, dNy, self.nl, wx, wy)
                Bb = bending_B(dNx, dNy)
                eps = np.einsum("mij,mj->mi", B_eps, ue)
                Nmem = np.einsum("ij,mj->mi", Amat_base, eps) * A
                kap = np.einsum("mij,mj->mi", Bb, ue)
                Mom = np.einsum("ij,mj->mi", Amat_base, kap) * D
                BjT = np.transpose(B_jac, (0, 2, 1))
                BbT = np.transpose(Bb, (0, 2, 1))
                f_e += (np.einsum("mij,mj->mi", BjT, Nmem)
                        + np.einsum("mij,mj->mi", BbT, Mom)) * ajd[:, None]
                if want_K:
                    Ke += ajd[:, None, None] * (
                        A * np.einsum("mij,jk,mkl->mil", BjT, Amat_base, B_jac)
                        + D * np.einsum("mij,jk,mkl->mil", BbT, Amat_base, Bb))
                    if self.nl:
                        G = np.zeros((e.size, 2, 4 * NDOF_PER_NODE))
                        G[:, 0, 2::5] = dNx
                        G[:, 1, 2::5] = dNy
                        GT = np.transpose(G, (0, 2, 1))
                        Nsig = np.zeros((e.size, 2, 2))
                        Nsig[:, 0, 0] = Nmem[:, 0]
                        Nsig[:, 1, 1] = Nmem[:, 1]
                        Nsig[:, 0, 1] = Nsig[:, 1, 0] = Nmem[:, 2]
                        Ke += ajd[:, None, None] * np.einsum(
                            "mij,mjk,mkl->mil", GT, Nsig, G)
            # 剪切 (1×1)
            N0, dNx0, dNy0, ajd0 = self.gs
            N0 = N0; ajd0 = ajd0[e]
            Bs = shear_B(N0, dNx0[e], dNy0[e])
            BsT = np.transpose(Bs, (0, 2, 1))
            gam = np.einsum("mij,mj->mi", Bs, ue)
            Q = S * gam
            f_e += 4.0 * ajd0[:, None] * np.einsum("mij,mj->mi", BsT, Q)
            if want_K:
                Ke += (4.0 * ajd0[:, None, None] * S
                       * np.einsum("mij,mjk->mik", BsT, Bs))
            rep = self.dof_rep[ld].ravel()
            ok = rep >= 0
            np.add.at(f_int, rep[ok], f_e.ravel()[ok])
            if want_K:
                idx = self.rmap[ld]
                keep = (idx[:, :, None] >= 0) & (idx[:, None, :] >= 0)
                I = np.broadcast_to(idx[:, :, None], Ke.shape)[keep]
                J = np.broadcast_to(idx[:, None, :], Ke.shape)[keep]
                rows.append(I); cols.append(J); vals.append(Ke[keep])

        f_ext = p * self.f_unit
        # 接触内力 (罚): f = k_n·(w_top − w_bottom) 作用在两层上
        act = np.where(active)[0]
        f_c = np.zeros(nd)
        if act.size:
            gg = U[self.ct[act]] - U[self.cb[act]]
            rt = self.dof_rep[self.ct[act]]
            rb = self.dof_rep[self.cb[act]]
            np.add.at(f_c, rt, (self.cw[act] * gg))
            np.add.at(f_c, rb, (-self.cw[act] * gg))
        r = (f_int + f_c - f_ext)[self.free]

        if not want_K:
            return r, None, f_int + f_c, f_ext

        # 接触刚度 (罚, 活动对)
        if act.size:
            kk = self.cw[act]
            a_t = self.rmap[self.ct[act]]
            a_b = self.rmap[self.cb[act]]
            rows.append(np.concatenate([a_t, a_t, a_b, a_b]))
            cols.append(np.concatenate([a_t, a_b, a_t, a_b]))
            vals.append(np.concatenate([kk, -kk, -kk, kk]))
        nf = int(self.free.sum())
        K = sp.coo_matrix((np.concatenate(vals),
                           (np.concatenate(rows), np.concatenate(cols))),
                          shape=(nf, nf)).tocsc()
        return r, K, f_int, f_ext

    # ---- 非线性(vK)装配加速：常值单元数据只算一次 ----
    def _prep_nl(self):
        """预计算 NL 装配中不随 U 变化的量：弯曲+剪切 Ke、索引、Gauss 几何."""
        if self._prep is not None:
            return self._prep
        Amat = np.array([[1.0, self.nu, 0.0],
                         [self.nu, 1.0, 0.0],
                         [0.0, 0.0, (1.0 - self.nu) / 2.0]])
        ncomp = NDOF_PER_NODE
        nn = 4 * ncomp
        prep = []
        for i in range(len(self.layers)):
            e = self.elem_i[i]
            if e.size == 0:
                prep.append(None)
                continue
            conn = self.conn[e]
            ld = self.ldof[i][conn].reshape(e.size, nn)
            idx = self.rmap[ld]
            keep = (idx[:, :, None] >= 0) & (idx[:, None, :] >= 0)
            I = np.broadcast_to(idx[:, :, None], (e.size, nn, nn))[keep]
            J = np.broadcast_to(idx[:, None, :], (e.size, nn, nn))[keep]
            A, D, S = self.A_i[i], self.D_i[i], self.S_i[i]
            Ke_bs = np.zeros((e.size, nn, nn))
            gauss = []
            for (_Ng, dNx_, dNy_, ajd_) in self.gb:
                dNx = dNx_[e]
                dNy = dNy_[e]
                ajd = ajd_[e]
                Bb = bending_B(dNx, dNy)
                BbT = np.transpose(Bb, (0, 2, 1))
                Ke_bs += ajd[:, None, None] * D * np.einsum(
                    "mij,jk,mkl->mil", BbT, Amat, Bb)
                gauss.append((dNx, dNy, ajd))
            N0, dNx0_, dNy0_, ajd0_ = self.gs
            Bs = shear_B(N0, dNx0_[e], dNy0_[e])
            BsT = np.transpose(Bs, (0, 2, 1))
            Ke_bs += (4.0 * ajd0_[e][:, None, None] * S
                      * np.einsum("mij,mjk->mik", BsT, Bs))
            prep.append(dict(ld=ld, keep=keep, I=I, J=J, gauss=gauss,
                             Ke_bs=Ke_bs, A=A))
        self._prep = prep
        return prep

    def _assemble_nl(self, U, p, want_K=True, active=None):
        if active is None:
            active = self.active
        Amat = np.array([[1.0, self.nu, 0.0],
                         [self.nu, 1.0, 0.0],
                         [0.0, 0.0, (1.0 - self.nu) / 2.0]])
        ncomp = NDOF_PER_NODE
        nn = 4 * ncomp
        nd = self.n_dof
        f_int = np.zeros(nd)
        rows, cols, vals = [], [], []
        for pr in self._prep_nl():
            if pr is None:
                continue
            ue = U[pr["ld"]]
            f_e = np.einsum("mij,mj->mi", pr["Ke_bs"], ue)   # 弯曲+剪切
            A = pr["A"]
            Ke_add = None
            for (dNx, dNy, ajd) in pr["gauss"]:
                w_e = ue[:, 2::ncomp]
                wx = np.einsum("ma,ma->m", dNx, w_e)
                wy = np.einsum("ma,ma->m", dNy, w_e)
                B_eps, B_jac = membrane_B(dNx, dNy, True, wx, wy)
                eps = np.einsum("mij,mj->mi", B_eps, ue)
                Nmem = np.einsum("ij,mj->mi", Amat, eps) * A
                BjT = np.transpose(B_jac, (0, 2, 1))
                f_e += np.einsum("mij,mj->mi", BjT, Nmem) * ajd[:, None]
                if want_K:
                    G = np.zeros((dNx.shape[0], 2, nn))
                    G[:, 0, 2::ncomp] = dNx
                    G[:, 1, 2::ncomp] = dNy
                    GT = np.transpose(G, (0, 2, 1))
                    Nsig = np.zeros((dNx.shape[0], 2, 2))
                    Nsig[:, 0, 0] = Nmem[:, 0]
                    Nsig[:, 1, 1] = Nmem[:, 1]
                    Nsig[:, 0, 1] = Nsig[:, 1, 0] = Nmem[:, 2]
                    Kmem = (A * np.einsum("mij,jk,mkl->mil",
                                          BjT, Amat, B_jac)
                            + np.einsum("mij,mjk,mkl->mil", GT, Nsig, G))
                    term = ajd[:, None, None] * Kmem
                    Ke_add = term if Ke_add is None else Ke_add + term
            rep = self.dof_rep[pr["ld"]].ravel()
            ok = rep >= 0
            np.add.at(f_int, rep[ok], f_e.ravel()[ok])
            if want_K:
                Ke = pr["Ke_bs"] if Ke_add is None else pr["Ke_bs"] + Ke_add
                rows.append(pr["I"])
                cols.append(pr["J"])
                vals.append(Ke[pr["keep"]])

        f_ext = p * self.f_unit
        act = np.where(active)[0]
        f_c = np.zeros(nd)
        if act.size:
            gg = U[self.ct[act]] - U[self.cb[act]]
            rt = self.dof_rep[self.ct[act]]
            rb = self.dof_rep[self.cb[act]]
            np.add.at(f_c, rt, (self.cw[act] * gg))
            np.add.at(f_c, rb, (-self.cw[act] * gg))
        r = (f_int + f_c - f_ext)[self.free]
        if not want_K:
            return r, None, f_int + f_c, f_ext
        if act.size:
            kk = self.cw[act]
            a_t = self.rmap[self.ct[act]]
            a_b = self.rmap[self.cb[act]]
            rows.append(np.concatenate([a_t, a_t, a_b, a_b]))
            cols.append(np.concatenate([a_t, a_b, a_t, a_b]))
            vals.append(np.concatenate([kk, -kk, -kk, kk]))
        nf = int(self.free.sum())
        K = sp.coo_matrix((np.concatenate(vals),
                           (np.concatenate(rows), np.concatenate(cols))),
                          shape=(nf, nf)).tocsc()
        return r, K, f_int, f_ext

    # ---------------- 求解 ----------------
    def solve(self, p_target: float, n_steps: int = 6, tol: float = 1e-6,
              max_iter: int = 30, U0=None):
        if not self.nl:
            return self._solve_linear(p_target)
        return self._solve_nonlinear(p_target, n_steps, tol, max_iter, U0)

    # ---- 线性求解加速：弹性刚度只装配一次，接触罚项按活动集叠加 ----
    def _elastic_K(self):
        """缓存的弹性刚度（CSC, 自由 DOF 空间）；nl=False 时与载荷无关."""
        if self._K_elastic is None:
            empty = np.zeros(self.n_pairs, dtype=bool)
            _, K, _, _ = self.assemble(np.zeros(self.n_dof), 0.0, True, empty)
            self._K_elastic = K.tocsc()
        return self._K_elastic

    def _contact_K(self, active):
        """当前活动接触对的罚刚度（自由 DOF 空间，CSC）；无接触返回 None."""
        act = np.where(active)[0]
        if act.size == 0:
            return None
        a_t = self.rmap[self.ct[act]]
        a_b = self.rmap[self.cb[act]]
        ok = (a_t >= 0) & (a_b >= 0)
        if not np.any(ok):
            return None
        a_t, a_b = a_t[ok], a_b[ok]
        kk = self.cw[act][ok]
        nf = int(self.free.sum())
        rows = np.concatenate([a_t, a_t, a_b, a_b])
        cols = np.concatenate([a_t, a_b, a_t, a_b])
        vals = np.concatenate([kk, -kk, -kk, kk])
        return sp.coo_matrix((vals, (rows, cols)),
                             shape=(nf, nf)).tocsc()

    def _solve_linear(self, p: float):
        f = p * self.f_unit
        if self.n_pairs:
            if (self._active_guess is not None
                    and self._active_guess.shape == (self.n_pairs,)):
                active = self._active_guess.copy()      # 热启动：上次活动集
            else:
                active = np.ones(self.n_pairs, dtype=bool)
        else:
            active = np.zeros(0, dtype=bool)
        u = np.zeros(self.n_dof)
        K_el = self._elastic_K()
        f_red = f[self.free]
        if self._P is not None:
            f_red = self._P_T @ f_red
        it = -1
        for it in range(self.max_outer_iter):
            key = (np.packbits(active).tobytes() if active.size else b"")
            if self._lu is None or self._lu_key != key:
                Kc = self._contact_K(active)
                K = K_el if Kc is None else (K_el + Kc).tocsc()
                if self._P is not None:
                    K = (self._P_T @ K @ self._P).tocsc()
                self._lu = splu(K)
                self._lu_key = key
            uq = self._lu.solve(f_red)
            u = np.zeros(self.n_dof)
            u[self.free] = (self._P @ uq) if self._P is not None else uq
            self._sync_periodic(u)
            if self.n_pairs == 0:
                break
            g = u[self.cb] - u[self.ct]
            new = g <= _TOL_GAP
            if np.array_equal(new, active):
                break
            active = new
        self._active_guess = active.copy()
        self.u = u
        self.active = active
        self.p_last = float(p)
        g = (u[self.cb] - u[self.ct]) if self.n_pairs else np.zeros(0)
        self._gap = g
        self.info = dict(n_outer=it + 1, n_active=int(active.sum()),
                         n_pairs=self.n_pairs,
                         penetration_max=float(max(0.0, -g.min()) if g.size else 0.0))
        return self.info

    def _newton(self, U, p, active, max_iter=30, tol=1e-6):
        # 每次 Newton 迭代只需一次装配（同时得到残差与切线 K），
        # 不再重复装配同一状态的 K。
        r, K, _, f_ext = self.assemble(U, p, True, active)
        ref = float(np.linalg.norm(f_ext[self.free]))
        if self._P is not None:
            ref = float(np.linalg.norm(self._P_T @ f_ext[self.free]))
            r = self._P_T @ r
            K = (self._P_T @ K @ self._P).tocsc()
        if ref == 0.0:
            return True, U, 0
        for it in range(1, max_iter + 1):
            rn = float(np.linalg.norm(r))
            if rn <= tol * ref:
                return True, U, it - 1
            try:
                du = spsolve(K, -r)
            except Exception:
                return False, U, it
            if not np.all(np.isfinite(du)):
                return False, U, it
            U = U.copy()
            if self._P is not None:
                U[self.free] += self._P @ du
            else:
                U[self.free] += du
            self._sync_periodic(U)
            r, K, _, _ = self.assemble(U, p, True, active)
            if self._P is not None:
                r = self._P_T @ r
                K = (self._P_T @ K @ self._P).tocsc()
            if (not np.isfinite(np.linalg.norm(r))
                    or np.linalg.norm(r) > 1e10 * max(rn, ref)):
                return False, U, it
        return False, U, max_iter

    def _solve_nonlinear(self, p_target, n_steps=6, tol=1e-6, max_iter=30,
                         U0=None):
        # U0：上一载荷/上一迭代的位移场（热启动），可显著减少载荷步数与 Newton 次数
        U = (np.zeros(self.n_dof) if U0 is None
             else np.array(U0, dtype=float, copy=True))
        self._sync_periodic(U)
        if self.n_pairs:
            if (self._active_guess is not None
                    and self._active_guess.shape == (self.n_pairs,)):
                active = self._active_guess.copy()      # 接触活动集热启动
            else:
                active = np.ones(self.n_pairs, dtype=bool)
        else:
            active = np.zeros(0, dtype=bool)
        p_cur = 0.0
        step = p_target / max(1, n_steps)
        n_newton = 0
        n_steps_done = 0
        while p_cur < p_target - 1e-12 * abs(p_target):
            step = min(step, p_target - p_cur)
            ok, U_new, it = self._newton(U, p_cur + step, active,
                                         max_iter, tol)
            if ok:
                # 接触活动集更新 (每个载荷步外层)
                for _ in range(self.max_outer_iter):
                    g = (U_new[self.cb] - U_new[self.ct]) if self.n_pairs else np.zeros(0)
                    new = (g <= _TOL_GAP) if self.n_pairs else active
                    if np.array_equal(new, active):
                        break
                    active = new
                    ok2, U_new, it2 = self._newton(U_new, p_cur + step, active,
                                                   max_iter, tol)
                    if not ok2:
                        ok = False
                        break
                if not ok:
                    step *= 0.5
                    if step < 1e-9 * max(abs(p_target), 1.0):
                        raise RuntimeError(
                            f"载荷步进停滞于 p={p_cur/1e6:.4f} MPa")
                    continue
                U, p_cur = U_new, p_cur + step
                n_newton += it
                n_steps_done += 1
                step = min(step * 1.6, (p_target / max(1, n_steps)) * 4)
            else:
                step *= 0.5
                if step < 1e-9 * max(abs(p_target), 1.0):
                    raise RuntimeError(
                        f"载荷步进停滞于 p={p_cur/1e6:.4f} MPa")
        self.u = U
        self.active = active
        self._active_guess = active.copy()
        self.p_last = float(p_target)
        g = (U[self.cb] - U[self.ct]) if self.n_pairs else np.zeros(0)
        self._gap = g
        self.info = dict(n_outer=n_steps_done, n_active=int(active.sum()),
                         n_pairs=self.n_pairs, n_newton=n_newton,
                         penetration_max=float(max(0.0, -g.min()) if g.size else 0.0))
        return self.info

    def curve(self, ps, n_steps=4, tol=1e-6):
        ps = np.asarray(ps, dtype=float)
        out = np.zeros((len(self.layers), len(ps)))
        for k, p in enumerate(ps):
            self.solve(float(p), n_steps=n_steps, tol=tol)
            out[:, k] = self.tip_deflections()
        return out

    def tangent_fd_check(self, U, p, eps=1e-9):
        active = self.active
        r, _, _, _ = self.assemble(U, p, False, active)
        _, K, _, _ = self.assemble(U, p, True, active)
        rng = np.random.default_rng(0)
        du = np.zeros(self.n_dof)
        du[self.free] = rng.normal(0.0, eps, int(self.free.sum()))
        r2, _, _, _ = self.assemble(U + du, p, False, active)
        fd = (r2 - r)          # Δr 已对应 K·du, 不再除以 eps
        kd = K @ du[self.free]
        return float(np.max(np.abs(fd - kd)) / max(np.max(np.abs(kd)), 1e-30))

    # ---------------- 结果 ----------------
    def w_matrix(self, i: int):
        loc = self.loc[i]
        act = loc >= 0
        w = np.zeros(self.n_nodes)
        w[act] = self.u[self.ldof[i][act, 2]]
        return w.reshape(self.n_r, self.n_theta_nodes)[:, :self.n_theta]

    def tip_deflections(self):
        out = []
        for i, l in enumerate(self.layers):
            ir = int(np.argmin(np.abs(self.r_nodes - l.b_m)))
            wm = self.w_matrix(i)
            out.append(float(wm[ir].mean()))
        return np.asarray(out)

    def profile(self, i: int, p: float | None = None):
        if p is not None:
            self.solve(p)
        w = self.w_matrix(i).mean(axis=1)
        mask = (self.loc[i] >= 0).reshape(self.n_r, self.n_theta_nodes).any(axis=1)
        return self.r_nodes[mask], w[mask]

    def contact_state(self):
        """接触状态: gap<0 为穿透; pressure/force 以压为正 (与物理符号一致)."""
        q = -self.k_n * self._gap            # 穿透时 gap<0 → 压为正
        return dict(ct=self.ct, cb=self.cb, gap=self._gap, active=self.active,
                    pressure=q, force=q * self.carea,
                    area=self.carea)


# ============================================================
def _selftest():
    E, nu = STEEL_7C27MO2_E, STEEL_7C27MO2_NU
    p = 1e6
    from shim_fem_plate import PolarShimFEM

    print("=" * 78)
    print("shim_layered 自检 (2D 分层 Mindlin vs 等效板)")
    print("=" * 78)

    one = [ShimLayer(6, 10, 0.1)]
    ref = PolarShimFEM(one, E=E, nu=nu, n_elem_base_radial=30, n_theta=24)
    h_ref = float(ref.tip_deflections_unit()[0] * p)
    lr = LayeredStack(one, E=E, nu=nu, n_elem_base_radial=30, n_theta=24)
    lr.solve(p)
    h1 = lr.tip_deflections()[0]
    rel = abs(h1 - h_ref) / h_ref
    print(f"[1] L=1: 分层 {h1*1e3:.8f} | 等效 {h_ref*1e3:.8f} | rel={rel:.2e}")
    assert rel < 1e-8

    for n in (2, 3):
        layers = [ShimLayer(6, 10, 0.1)] * n
        eq = PolarShimFEM(layers, E=E, nu=nu, n_elem_base_radial=30, n_theta=24)
        h_eq = float(eq.tip_deflections_unit()[-1] * p)
        lrs = LayeredStack(layers, E=E, nu=nu, n_elem_base_radial=30, n_theta=24)
        lrs.solve(p)
        h = lrs.tip_deflections()[-1]
        print(f"[2] 同径×{n}: 分层 {h*1e3:.6f} | ΣD {h_eq*1e3:.6f} | "
              f"rel={abs(h-h_eq)/h_eq:.2e} | 穿透 {lrs.info['penetration_max']*1e6:.3f} µm")
        assert abs(h - h_eq) / h_eq < 1e-2

    tap = [ShimLayer(6, 10, 0.1), ShimLayer(6, 9, 0.1), ShimLayer(6, 8, 0.1)]
    eq = PolarShimFEM(tap, E=E, nu=nu, n_elem_base_radial=30, n_theta=24)
    h_eq = float(eq.tip_deflections_unit()[0] * p)
    lrs = LayeredStack(tap, E=E, nu=nu, n_elem_base_radial=30, n_theta=24)
    lrs.solve(p)
    tips = lrs.tip_deflections()
    print(f"[3] 锥形 10/9/8 @1MPa: 等效 {h_eq*1e3:.5f} | 分层 "
          f"{tips[0]*1e3:.5f}/{tips[1]*1e3:.5f}/{tips[2]*1e3:.5f} mm | "
          f"Δ={((tips[0]-h_eq)/h_eq)*100:+.2f}% | "
          f"接触 {int(lrs.active.sum())}/{lrs.n_pairs}")
    print(f"    穿透 {lrs.info['penetration_max']*1e6:.3f} µm")

    # [4] k_n 平台 + 穿透缩放 (线性, 20×24)
    print("[4] k_n 平台与穿透缩放 (锥形顶片, 20×24):")
    hs, pens = [], []
    for kn in (1e14, 1e15, 1e16):
        lk = LayeredStack(tap, E=E, nu=nu, n_elem_base_radial=20,
                          n_theta=24, k_n=kn)
        lk.solve(p)
        hs.append(lk.tip_deflections()[0])
        pens.append(lk.info["penetration_max"])
        print(f"    k_n={kn:.0e}: h={hs[-1]*1e3:.6f} mm | 穿透 "
              f"{pens[-1]*1e6:.4f} µm")
    dratio = pens[-2] / max(pens[-1], 1e-30)
    print(f"    k_n×10 → 穿透比 {dratio:.2f} (应≈10)")
    assert 5.0 < dratio < 20.0
    plateau = abs(hs[-1] - hs[-2]) / hs[-1]
    print(f"    1e15→1e16 相对变化 {plateau:.2e} (应 < 5e-3)")
    assert plateau < 5e-3

    # [5] 线性: 静力平衡 + 环向均匀性
    print("[5] 线性静力平衡 / 环向均匀性 (20×24):")
    lr6 = LayeredStack(tap, E=E, nu=nu, n_elem_base_radial=20, n_theta=24)
    lr6.solve(p)
    r_bal, _, _, f_ext = lr6.assemble(lr6.u, p, False)
    rel_r = float(np.linalg.norm(r_bal) / np.linalg.norm(f_ext[lr6.free]))
    w0 = lr6.w_matrix(0)
    ring_dev = float(np.max(np.abs(w0 - w0.mean(axis=1, keepdims=True)))
                     / np.abs(w0).max())
    print(f"    |r|/|f| = {rel_r:.2e} | 环向偏差 = {ring_dev:.2e}")
    assert rel_r < 1e-8
    assert ring_dev < 1e-6

    # [6] 网格无关性: 分层−等效板的偏差 Δ 应稳定
    print("[6] 分层−等效板 偏差 Δ 的网格无关性:")
    deltas = []
    for nr, nt in ((20, 24), (20, 48), (40, 48)):
        eq_m = PolarShimFEM(tap, E=E, nu=nu, n_elem_base_radial=nr, n_theta=nt)
        h_eq_m = float(eq_m.tip_deflections_unit()[0] * p)
        lr_m = LayeredStack(tap, E=E, nu=nu, n_elem_base_radial=nr, n_theta=nt)
        lr_m.solve(p)
        d = (lr_m.tip_deflections()[0] - h_eq_m) / h_eq_m
        deltas.append(d)
        print(f"    {nr}x{nt}: 等效 {h_eq_m*1e3:.5f} | 分层 "
              f"{lr_m.tip_deflections()[0]*1e3:.5f} | Δ={d*100:+.2f}%")
    spread = (max(deltas) - min(deltas)) * 100
    print(f"    Δ 极差 {spread:.2f} pp (应 < 0.5 pp)")
    assert spread < 0.5

    # [7] 大挠度: 小载荷退化 + 切线一致性 + 刚化
    print("[7] 大挠度 (nl=True):")
    nr, nt = 16, 12
    lin0 = LayeredStack(tap, E=E, nu=nu, n_elem_base_radial=nr, n_theta=nt)
    lin0.solve(1e5)
    lrn = LayeredStack(tap, E=E, nu=nu, n_elem_base_radial=nr, n_theta=nt, nl=True)
    lrn.solve(1e5, n_steps=3)
    h_nl = lrn.tip_deflections()[0]
    h_li = lin0.tip_deflections()[0]
    print(f"    小载荷 (0.1 MPa) NL/线性 = {h_nl/h_li:.6f} (应≈1)")
    assert abs(h_nl / h_li - 1.0) < 5e-3
    err = lrn.tangent_fd_check(lrn.u, 1e6)
    print(f"    切线 vs 有限差分: {err:.2e} (应 < 1e-5)")
    assert err < 1e-5
    for pp in (0.5e6, 1e6, 2e6):
        lrn.solve(pp, n_steps=4)
        w = lrn.tip_deflections()[0]
        print(f"    p={pp/1e6:.2f} MPa: w={w*1e3:.5f} mm | w/t={w/1e-4:.2f} | "
              f"接触 {int(lrn.active.sum())}/{lrn.n_pairs}")
    print("自检完成")


if __name__ == "__main__":
    _selftest()
