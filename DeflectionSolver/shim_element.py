# -*- coding: utf-8 -*-
"""
Q4 等参单元共享核 (2D 极坐标 Mindlin 板 / von Kármán 板)
=========================================================
定位: `shim_nl_plate.PolarShimNLPlate`（共中面等效板）与
      `shim_layered.LayeredStack`（分层板）共用的**同一套**形函数、几何映射
      与 B 矩阵。两者必须用同一套单元公式，对比才有意义。

约定:
  * 单元 4 节点, 局部节点顺序与 `shim_stack_mesh` 的 conn 一致
    (n00, n01, n11, n10 —— 极坐标映射下为顺时针, 故一律用 |det J|)。
  * 每节点 5 个自由度 (u, v, w, φx, φy), 局部 DOF 长度 = 20。
  * 膜/弯用 2×2 Gauss; 横向剪切用 1×1 减缩 (避免剪切锁死)。
  * vK 的 ½ 因子只出现在应变的 B_eps 中; 残差/切线用的 B_jac 不带 ½
    (见 `shim_nl_plate` 的历史踩坑记录)。
"""
from __future__ import annotations

import math

import numpy as np

NDOF_PER_NODE = 5          # u, v, w, phix, phiy

_Q4_XI = np.array([-1.0, 1.0, 1.0, -1.0])
_Q4_ETA = np.array([-1.0, -1.0, 1.0, 1.0])
_GP = 1.0 / math.sqrt(3.0)
GAUSS_2X2 = ((-_GP, -_GP), (_GP, -_GP), (_GP, _GP), (-_GP, _GP))
GAUSS_1X1 = ((0.0, 0.0),)

__all__ = ["NDOF_PER_NODE", "GAUSS_2X2", "GAUSS_1X1",
           "q4_shape", "q4_geo", "membrane_B", "bending_B", "shear_B"]


def q4_shape(xi: float, eta: float):
    """Q4 形函数与对参考坐标的导数 (各 4,)."""
    N = 0.25 * (1.0 + xi * _Q4_XI) * (1.0 + eta * _Q4_ETA)
    dNxi = 0.25 * _Q4_XI * (1.0 + eta * _Q4_ETA)
    dNeta = 0.25 * _Q4_ETA * (1.0 + xi * _Q4_XI)
    return N, dNxi, dNeta


def q4_geo(x: np.ndarray, y: np.ndarray, pts=GAUSS_2X2):
    """单元几何映射.

    x, y : (M, 4) 各单元 4 个节点的直角坐标
    返回 [(N, dNx, dNy, |det J|), ...], 每个元素形状 (4,), (M,4), (M,4), (M,)
    """
    out = []
    for xi, eta in pts:
        N, dNxi, dNeta = q4_shape(xi, eta)
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
        out.append((N, dNx, dNy, np.abs(jd)))
    return out


def membrane_B(dNx, dNy, nl: bool = False, wx=None, wy=None,
               ncomp: int = NDOF_PER_NODE):
    """膜 B 矩阵.

    返回 (B_eps, B_jac):
      * nl=False: 两者都是线性膜应变 B (副本);
      * nl=True : B_eps 的 w 列带 ½ (εxx = u,x + ½(w,x)² …),
                  B_jac 的 w 列不带 ½ (残差/切线用的 ∂ε/∂u)。
    """
    M = dNx.shape[0]
    n = 4 * ncomp
    B = np.zeros((M, 3, n))
    B[:, 0, 0::ncomp] = dNx
    B[:, 1, 1::ncomp] = dNy
    B[:, 2, 0::ncomp] = dNy
    B[:, 2, 1::ncomp] = dNx
    if not nl:
        return B.copy(), B.copy()

    wcol_x = wx[:, None] * dNx
    wcol_y = wy[:, None] * dNy
    wcol_xy = wy[:, None] * dNx + wx[:, None] * dNy
    B_eps = B.copy()
    B_jac = B.copy()
    B_eps[:, 0, 2::ncomp] = 0.5 * wcol_x
    B_eps[:, 1, 2::ncomp] = 0.5 * wcol_y
    B_eps[:, 2, 2::ncomp] = 0.5 * wcol_xy
    B_jac[:, 0, 2::ncomp] = wcol_x
    B_jac[:, 1, 2::ncomp] = wcol_y
    B_jac[:, 2, 2::ncomp] = wcol_xy
    return B_eps, B_jac


def bending_B(dNx, dNy, ncomp: int = NDOF_PER_NODE):
    """弯曲 B 矩阵 (曲率 κ = (φx,x, φy,y, φx,y+φy,x))."""
    M = dNx.shape[0]
    n = 4 * ncomp
    Bb = np.zeros((M, 3, n))
    Bb[:, 0, 3::ncomp] = dNx
    Bb[:, 1, 4::ncomp] = dNy
    Bb[:, 2, 3::ncomp] = dNy
    Bb[:, 2, 4::ncomp] = dNx
    return Bb


def shear_B(N, dNx, dNy, ncomp: int = NDOF_PER_NODE):
    """横向剪切 B 矩阵 (γ = (w,x + φx, w,y + φy)), 配 1×1 减缩积分."""
    M = dNx.shape[0]
    n = 4 * ncomp
    Bs = np.zeros((M, 2, n))
    Bs[:, 0, 2::ncomp] = dNx
    Bs[:, 0, 3::ncomp] = N
    Bs[:, 1, 2::ncomp] = dNy
    Bs[:, 1, 4::ncomp] = N
    return Bs
