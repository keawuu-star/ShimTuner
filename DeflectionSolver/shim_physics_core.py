# -*- coding: utf-8 -*-
"""
ShimTuner 物理内核 v0 (基础版, 纯 numpy, 无 GUI 依赖)
=====================================================
理论依据: ShimTunerPhysics.md
  * 单阀片: 内孔边缘固支、外缘自由的轴对称环形薄板, 顶面均布压强 p。
    外缘开度 h = Φ(ρ, ν) · p·b⁴ / D ,   D = E·t³ / (12(1−ν²)),  ρ = a/b,
    其中无量纲系数 Φ 由 md 中的线性方程组求解。
  * 阀片堆叠: 等效为"径向阶梯变刚度环形板": 半径 r 处有 k 片阀片覆盖,
    则 D(r) = Σ Dᵢ —— 等径堆叠自动退化为刚度相加; 锥形堆叠在每片外缘
    出现刚度阶梯 (即 md 所述"不能简单相加"), 用轴对称板弯曲环元
    (每节点 2 自由度 w, θ = dw/dr) 沿径向网格化求解 D∇⁴w = p。
    假设: 层间全程接触不分离 (受压 + 小挠度), 仅第1片(贴活塞/迎油面)受油压。

使用约定 (与 md 示例一致):
  * 阀片按"从贴活塞第1片向上"排列, 第 0 片直接受均匀压强 p;
    其余各片只通过层间接触传递载荷。
  * 内孔处被压紧螺栓夹持: w = 0, θ = 0 (夹持半径默认 = 最小内孔半径)。
  * 外缘自由 (M_r = 0, V_r = 0, 有限元中为自然边界条件)。
  * 小挠度线性: 适用条件 h ≪ t; 超出时结果仅供趋势参考 (结果中给出警告接口)。

数值自检:
    python shim_physics_core.py
  打印单阀片 FEM vs 解析解 (md 公式) 对照表、等径堆叠退化检查与锥形堆叠算例。
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

# Sandvik 7C27Mo2 阀片钢 (文档默认材料)
STEEL_7C27MO2_E = 200.0e9    # Pa
STEEL_7C27MO2_NU = 0.30


# ============================================================
# 1. 单片解析解 (ShimTunerPhysics.md 公式, 用于对照核验)
# ============================================================
def analytic_phi(d_in_mm: float, d_out_mm: float, nu: float = STEEL_7C27MO2_NU) -> float:
    """md: 无量纲系数 Φ(a/b, ν) —— 由两个线性方程解出 B, C 后代入."""
    a = (max(d_in_mm, 0.0) * 1e-3) / 2.0
    b = (max(d_out_mm, 0.0) * 1e-3) / 2.0
    if b <= 0.0 or a >= b or a <= 0.0:
        raise ValueError(f"非法内外径: d_in={d_in_mm}, d_out={d_out_mm}")
    rho = a / b
    L = np.log(rho)
    mat = np.array([[2.0 * rho ** 2, 1.0],
                    [2.0 * (1.0 + nu), nu - 1.0]])
    rhs = np.array([(rho ** 2 / 8.0) * (2.0 * L + 1.0) - rho ** 4 / 16.0,
                    (3.0 + nu) / 16.0])
    B, C = np.linalg.solve(mat, rhs)
    return float(B * (1.0 - rho ** 2) - C * L
                 + (rho ** 2 / 8.0) * L + (1.0 - rho ** 4) / 64.0)


def analytic_edge_deflection(d_in_mm: float, d_out_mm: float, t_mm: float,
                             p_pa: float,
                             E: float = STEEL_7C27MO2_E,
                             nu: float = STEEL_7C27MO2_NU) -> float:
    """单片 (固支内缘/自由外缘) 在均布压强 p_pa 下的外缘挠度 h [m]."""
    if t_mm <= 0.0:
        raise ValueError("厚度必须 > 0")
    b = (d_out_mm * 1e-3) / 2.0
    t = t_mm * 1e-3
    D = E * t ** 3 / (12.0 * (1.0 - nu ** 2))
    phi = analytic_phi(d_in_mm, d_out_mm, nu)
    return float(phi * p_pa * b ** 4 / D)


def bending_stiffness_of(t_mm: float, E: float = STEEL_7C27MO2_E,
                         nu: float = STEEL_7C27MO2_NU) -> float:
    """单片弯曲刚度 D = E t³ / (12(1−ν²)) [N·m]."""
    t = max(t_mm, 0.0) * 1e-3
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


# ============================================================
# 2. 阀片堆叠: 阶梯变刚度环形板 环元 FEM 求解器
# ============================================================
@dataclass(frozen=True)
class ShimLayer:
    """一片阀片 (内径/外径/厚度, 单位 mm)."""
    d_in_mm: float
    d_out_mm: float
    t_mm: float

    def validate(self) -> None:
        if self.d_in_mm <= 0.0 or self.d_out_mm <= self.d_in_mm or self.t_mm <= 0.0:
            raise ValueError(f"非法阀片: d_in={self.d_in_mm}, d_out={self.d_out_mm}, t={self.t_mm}")

    @property
    def a_m(self) -> float:   # 内半径
        return self.d_in_mm * 1e-3 / 2.0

    @property
    def b_m(self) -> float:   # 外半径
        return self.d_out_mm * 1e-3 / 2.0


def _hermite(x: np.ndarray, le: float):
    """Cubic Hermite (物理斜率), 返回 (N, dNdx, d2Ndx2) 各 4xG."""
    x2 = x * x
    x3 = x2 * x
    N = np.stack([1.0 - 3.0 * x2 + 2.0 * x3,
                  le * (x3 - 2.0 * x2 + x),
                  3.0 * x2 - 2.0 * x3,
                  le * (x3 - x2)], axis=0)
    dN = np.stack([6.0 * x2 - 6.0 * x,
                   le * (3.0 * x2 - 4.0 * x + 1.0),
                   -6.0 * x2 + 6.0 * x,
                   le * (3.0 * x2 - 2.0 * x)], axis=0)
    d2N = np.stack([12.0 * x - 6.0,
                    le * (6.0 * x - 4.0),
                    -12.0 * x + 6.0,
                    le * (6.0 * x - 2.0)], axis=0)
    return N, dN, d2N


class ShimStackCore:
    """
    阀片堆叠小挠度求解器 (等效阶梯刚度环形板).

    输入: layers 按受载面(贴活塞第1片)向上排列; 等效板只在 layers[0] 的环形面受均布压强.
    几何刚度线性, 因此求解一次单位压强, 任意 p 直接线性放大.

    输出单位: SI (挠度 m, 压强 Pa), 半径数组 m.
    """

    def __init__(self, layers, E: float = STEEL_7C27MO2_E,
                 nu: float = STEEL_7C27MO2_NU,
                 n_elem_base: int = 400,
                 clamp_radius_mm: float | None = None):
        if len(layers) == 0:
            raise ValueError("至少需要一片阀片")
        self.layers = [l if isinstance(l, ShimLayer) else ShimLayer(**l)
                       for l in layers]
        for l in self.layers:
            l.validate()
        self.E = E
        self.nu = nu
        self.D_layer = [bending_stiffness_of(l.t_mm, E, nu) for l in self.layers]

        # ---- 径向网格: 强制节点放在每片内/外缘(刚度阶梯与输出位置) ----
        clamp_r = (clamp_radius_mm if clamp_radius_mm is not None
                   else min(l.d_in_mm for l in self.layers) / 2.0)
        self.clamp_radius_m = clamp_r * 1e-3
        if self.clamp_radius_m < min(l.a_m for l in self.layers) - 1e-12:
            raise ValueError("clamp_radius 小于某阀片内半径会造成无支撑悬环")
        pts = sorted(set([self.clamp_radius_m,
                          *[l.a_m for l in self.layers],
                          *[l.b_m for l in self.layers]]))

        h_max = (pts[-1] - self.clamp_radius_m) / n_elem_base
        nodes = [pts[0]]
        for r0, r1 in zip(pts[:-1], pts[1:]):
            if r1 - r0 < 1e-15:
                continue
            n = max(1, int(np.ceil((r1 - r0) / h_max)) + 1)
            nodes.extend(np.linspace(r0, r1, n)[1:])
        self.r_nodes = np.asarray(nodes)          # (N,) m
        self.n_nodes = len(self.r_nodes)
        self.le = np.diff(self.r_nodes)           # 单元长度

        # 每单元覆盖层数 -> 单元刚度 D_e
        rc = (self.r_nodes[:-1] + self.r_nodes[1:]) / 2.0
        self.D_e = np.zeros(len(rc))
        for li, l in enumerate(self.layers):
            mask = (rc >= l.a_m - 1e-15) & (rc <= l.b_m + 1e-15)
            self.D_e[mask] += self.D_layer[li]
        if np.any(self.D_e <= 0.0):
            raise ValueError("存在无任何阀片覆盖的径向单元 (刚度=0): "
                             "请检查 clamp_radius 与各片内径")

        self._assemble()
        self._solve_unit()

    # ---------------- FEM 组装 ----------------
    def _assemble(self):
        nd = 2 * self.n_nodes
        K = np.zeros((nd, nd))
        xs, wg = np.polynomial.legendre.leggauss(5)
        xs = (xs + 1.0) / 2.0
        wg = wg / 2.0

        for e in range(self.n_nodes - 1):
            r0, r1 = self.r_nodes[e], self.r_nodes[e + 1]
            le = self.le[e]
            N, dN, d2N = _hermite(xs, le)
            d1 = dN / le          # dw/dr  = dN/dx / le
            d2 = d2N / le ** 2    # d²w/dr²
            r = r0 + le * xs
            Ke = np.zeros((4, 4))
            for g in range(5):
                rg = r[g]
                wg_ = wg[g] * le
                o11 = np.outer(d1[:, g], d1[:, g])
                o12 = np.outer(d1[:, g], d2[:, g])
                o22 = np.outer(d2[:, g], d2[:, g])
                Ke += (2.0 * np.pi * self.D_e[e] * wg_) * (
                    rg * o22 + self.nu * (o12 + o12.T) + o11 / rg)
            i0 = 2 * e
            K[i0:i0 + 4, i0:i0 + 4] += Ke

        # 载荷: 第1片(贴活塞/迎油面, 第 0 片) 环形面 [a0, b0] 均布压强(单位压强)
        a0, b0 = self.layers[0].a_m, self.layers[0].b_m
        f = np.zeros(nd)
        for e in range(self.n_nodes - 1):
            r0e, r1e = self.r_nodes[e], self.r_nodes[e + 1]
            if r1e <= a0 + 1e-15 or r0e >= b0 - 1e-15:
                continue
            le = self.le[e]
            N, _, _ = _hermite(xs, le)
            r = r0e + le * xs
            fe = np.zeros(4)
            for g in range(5):
                fe += 2.0 * np.pi * wg[g] * le * N[:, g] * r[g]
            i0 = 2 * e
            f[i0:i0 + 4] += fe

        # 固支: r = clamp_radius 处 w = 0, θ = 0
        self.clamp_node = int(np.argmin(np.abs(self.r_nodes - self.clamp_radius_m)))
        keep = [i for i in range(nd)
                if i not in (2 * self.clamp_node, 2 * self.clamp_node + 1)]
        self._K_red = K[np.ix_(keep, keep)]
        self._f_red = f[keep]
        self._keep = keep

    def _solve_unit(self):
        """单位压强 (1 Pa) 下的位移解."""
        w_red = np.linalg.solve(self._K_red, self._f_red)
        w = np.zeros(2 * self.n_nodes)
        w[self._keep] = w_red
        self.w_unit = w[0::2]                     # 单位压强挠度 [m/Pa]
        self.th_unit = w[1::2]                    # 单位压强转角 [rad/Pa]

    # ---------------- 结果接口 ----------------
    @property
    def b_max_m(self) -> float:
        """堆叠最外缘半径 (全部阀片外半径最大值)."""
        return max(l.b_m for l in self.layers)

    def profile_at(self, p_pa: float):
        """压强 p 下的整条 w(r) 曲线: 返回 (r[m], w[m])."""
        return self.r_nodes, self.w_unit * p_pa

    def edge_deflections(self, p_pa: float):
        """每片阀片外缘(外径处)的挠度 [m], 顺序与 layers 一致."""
        out = np.zeros(len(self.layers))
        for i, l in enumerate(self.layers):
            idx = int(np.argmin(np.abs(self.r_nodes - l.b_m)))
            out[i] = self.w_unit[idx] * p_pa
        return out

    def edge_deflection_curve(self, p_array):
        """挠度-压强曲线: p_array [Pa] -> (len(p), n_layers) 各片外缘挠度[m]."""
        p = np.asarray(p_array, dtype=float)
        tips = np.zeros((len(self.layers), len(p)))
        for i, l in enumerate(self.layers):
            idx = int(np.argmin(np.abs(self.r_nodes - l.b_m)))
            tips[i] = self.w_unit[idx] * p
        return tips

    def small_deflection_ok(self, p_pa: float) -> tuple[bool, float]:
        """检查 h ≪ t (md 适用条件). 返回 (是否满足, 最大 h/t 比)."""
        tips = self.edge_deflections(p_pa)
        ratio = 0.0
        for i, l in enumerate(self.layers):
            idx = int(np.argmin(np.abs(self.r_nodes - l.b_m)))
            ratio = max(ratio, abs(self.w_unit[idx] * p_pa) / (l.t_mm * 1e-3))
        return ratio < 0.2, ratio


# ============================================================
# 3. 数值自检: FEM vs md 解析解
# ============================================================
def _self_test():
    print("=" * 78)
    print("ShimTuner 物理内核 v0 自检 (单阀片 FEM vs md 解析公式)")
    print("=" * 78)
    print(f"{'d_in':>6} {'d_out':>6} {'t(mm)':>7} | {'解析 h(mm)':>11} "
          f"{'FEM h(mm)':>10} {'相对误差':>10}")
    cases = [(6.0, 10.0, 0.10), (6.0, 14.0, 0.10), (6.0, 22.0, 0.15),
             (8.0, 22.0, 0.20), (16.0, 30.0, 0.15)]
    p = 1e6
    worst = 0.0
    for din, dout, t in cases:
        h_an = analytic_edge_deflection(din, dout, t, p)
        core = ShimStackCore([ShimLayer(din, dout, t)])
        h_fem = float(core.edge_deflections(p)[0])
        rel = abs(h_fem - h_an) / h_an
        worst = max(worst, rel)
        print(f"{din:6.1f} {dout:6.1f} {t:7.2f} | {h_an * 1e3:11.5f} "
              f"{h_fem * 1e3:10.5f} {rel:10.2e}")
    print(f"  最大相对误差 = {worst:.2e}  (应远小于 1e-3)\n")

    print("等径堆叠退化检查: 3 片 (6,10,0.1) 应等于单片的 1/3")
    din, dout, t = 6.0, 10.0, 0.1
    h_single = analytic_edge_deflection(din, dout, t, p)
    core_eq = ShimStackCore([ShimLayer(din, dout, t)] * 3)
    h_eq = core_eq.edge_deflections(p)[-1]
    print(f"  单片解析 {h_single * 1e3:.5f} mm | 3 片堆叠 FEM {h_eq * 1e3:.5f} mm "
          f"| 比值 {h_single / h_eq:.4f} (应≈3)\n")

    print("锥形堆叠算例 (md 示例: 第1片 Ø10 ← Ø9 ← Ø8, 全 t=0.1, 内径 6)")
    layers = [ShimLayer(6.0, 10.0, 0.1), ShimLayer(6.0, 9.0, 0.1), ShimLayer(6.0, 8.0, 0.1)]
    core_tap = ShimStackCore(layers)
    h_top_single = analytic_edge_deflection(6.0, 10.0, 0.1, p)
    tips = core_tap.edge_deflections(p)
    ok, ratio = core_tap.small_deflection_ok(p)
    for i, l in enumerate(layers):
        print(f"  第 {i + 1} 片 (Ø{int(l.d_out_mm):2d}) 外缘 h = {tips[i] * 1e3:8.5f} mm")
    print(f"  单片 Ø10 参照             h = {h_top_single * 1e3:8.5f} mm")
    print(f"  堆叠刚度提升因子 = {h_top_single / tips[0]:.3f} (>1 即堆叠变硬)")
    print(f"  小挠度 h/t 最大比值 = {ratio:.3f} (应 < 0.2, 超出仅趋势参考)")

    # 收敛性
    h_c = [ShimStackCore(layers, n_elem_base=n).edge_deflections(p)[0]
           for n in (100, 400, 1200)]
    conv = abs(h_c[2] - h_c[1]) / h_c[1]
    print(f"  网格收敛: n=100:{h_c[0] * 1e3:.6f} n=400:{h_c[1] * 1e3:.6f} "
          f"n=1200:{h_c[2] * 1e3:.6f} mm | 400→1200 相对变化 {conv:.2e}")
    print("=" * 78)


if __name__ == "__main__":
    _self_test()
