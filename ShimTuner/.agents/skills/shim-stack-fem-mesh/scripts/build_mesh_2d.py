# -*- coding: utf-8 -*-
"""
2D 极坐标 r-θ 梯形(扇形)网格生成模板 (按阀片半径台阶切分 × 角度均匀切分)
=========================================================================
配套技能: .agents/skills/shim-stack-fem-mesh/
          (SKILL.md, references/polar-mesh-2d.md)
理论:     ShimTunerPhysics.md「锥形阀片堆叠」节: "按半径差值将阀片在径向切分,
          并按角度将阀片均匀切分得到梯形元素; 在径向阀片台阶处切分阀片,
          在圆周上按角度切分"

用途: 非轴对称载荷/几何(偏心孔、周向变化油压等)才需要 2D 网格;
      轴对称任务请用 1D 环元(径向-mesh-1d.md / shim_physics_core.py)。

拓扑约定(与 references/polar-mesh-2d.md 一致):
  * 径向: 关键半径 R_c = {clamp} ∪ {a_i} ∪ {b_i} 强制成为节点; 带内均匀细分
  * 周向: 全环 nθ 等分, dθ = 2π/nθ, 连通性用 (j+1) % nθ 回绕闭合
  * 节点编号 r 优先: global = i_r * nθ + j ; 节点总数 = n_r * nθ
  * 单元编号 e = e_r * nθ + j (e_r 为径向单元序号, 按带顺序)
  * 单元四边形逆时针: [n00, n01, n11, n10] = 梯形/扇形等参四边形

输出 dict 契约(单元级数组长度 M = n_cell_r * nθ):
  conn / coverage_e / D_e_e / area_e / r_c_e / theta_c_e / face_e / ...
  + 径向"单元条带"级数组 (长度 n_cell_r, 每个被 nθ 个扇区单元共享):
    r0_c / r1_c / rm_c / dr_c / coverage_c / D_e_c / face_c / arc_outer_c

自检运行:  python build_mesh_2d.py
"""
from __future__ import annotations

import math

import numpy as np

STEEL_7C27MO2_E = 200.0e9   # Pa, Sandvik 7C27Mo2
STEEL_7C27MO2_NU = 0.30
TOL_M = 1e-12               # 半径去重/比较容差 (m)


def bending_stiffness(t_mm: float, E: float = STEEL_7C27MO2_E,
                      nu: float = STEEL_7C27MO2_NU) -> float:
    t = t_mm * 1e-3
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


def _as_triple(x):
    if isinstance(x, dict):
        return (float(x["d_in_mm"]), float(x["d_out_mm"]), float(x["t_mm"]))
    try:
        return (float(x.d_in_mm), float(x.d_out_mm), float(x.t_mm))
    except AttributeError:
        return (float(x[0]), float(x[1]), float(x[2]))


def _prepare_layers(layers, E, nu):
    specs = []
    for i, l in enumerate(layers):
        d_in_mm, d_out_mm, t_mm = _as_triple(l)
        if d_in_mm <= 0.0 or d_out_mm <= d_in_mm or t_mm <= 0.0:
            raise ValueError(f"layers[{i}] 非法: d_in={d_in_mm}, d_out={d_out_mm}, t={t_mm}")
        specs.append(dict(d_in_mm=d_in_mm, d_out_mm=d_out_mm, t_mm=t_mm,
                          a_m=d_in_mm * 1e-3 / 2.0, b_m=d_out_mm * 1e-3 / 2.0,
                          D=bending_stiffness(t_mm, E, nu)))
    return specs


def _critical_radii_m(specs, clamp_m):
    rs = sorted({clamp_m, *[s["a_m"] for s in specs],
                 *[s["b_m"] for s in specs]})
    out = []
    for r in rs:
        if not out or r - out[-1] > TOL_M:
            out.append(r)
    return np.asarray(out, dtype=float)


def _count_elems(width_m: float, h_max: float) -> int:
    """带内径向单元数: max(1, ceil(width/h)), ratio 恰为整数时对浮点噪声稳健."""
    ratio = width_m / h_max
    r = round(ratio)
    if abs(ratio - r) <= 1e-9 * max(1.0, ratio):   # 落在整数边界(噪声级) -> 取整
        return max(1, int(r))
    return max(1, int(math.ceil(ratio)))


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def build_polar_mesh(layers_mm, n_elem_base_radial: int = 400,
                     n_theta: int = 120,
                     clamp_radius_mm: float | None = None,
                     E: float = STEEL_7C27MO2_E,
                     nu: float = STEEL_7C27MO2_NU) -> dict:
    """
    按半径台阶生成 2D 极坐标梯形网格.

    layers_mm         : [(d_in_mm, d_out_mm, t_mm), ...], 第 0 片为受油压顶片
    n_elem_base_radial: 目标径向单元总数(近似; 与 1D 模板同一语义)
    n_theta           : 周向等分段数(建议偶数; 计算量 = 径向单元数 × n_theta)
    clamp_radius_mm   : 夹持半径, 默认 min(d_in_mm)/2
    """
    if int(n_elem_base_radial) < 1:
        raise ValueError("n_elem_base_radial 必须 >= 1")
    if int(n_theta) < 3:
        raise ValueError("n_theta 必须 >= 3")
    n_theta = int(n_theta)
    specs = _prepare_layers(layers_mm, E, nu)
    clamp_mm = (clamp_radius_mm if clamp_radius_mm is not None
                else min(s["d_in_mm"] for s in specs) / 2.0)
    clamp_m = clamp_mm * 1e-3

    # ---- 几何前置校验 (与 shim_physics_core.py 一致) ----
    if clamp_m < min(s["a_m"] for s in specs) - TOL_M:
        raise ValueError("clamp_radius 小于某片内半径会造成无支撑悬环")
    for s in specs:
        if s["b_m"] < clamp_m - TOL_M:
            raise ValueError("某片外半径 <= clamp, 整片在夹持内, 不参与变形")

    r_c = _critical_radii_m(specs, clamp_m)
    r_max = float(r_c[-1])
    h_max = (r_max - clamp_m) / int(n_elem_base_radial)

    # ---- 径向: 关键半径 + 带内细分 (与 1D 模板同一套规则; 域从最小关键半径起) ----
    nodes, bands = [float(r_c[0])], []
    for r0, r1 in zip(r_c[:-1], r_c[1:]):
        n = _count_elems(r1 - r0, h_max)
        nodes.extend(np.linspace(r0, r1, n + 1)[1:].tolist())
        bands.append(dict(r0_m=r0, r1_m=r1, n_rad=n))
    r_nodes = np.asarray(nodes, dtype=float)
    n_r = len(r_nodes)
    n_cell_r = n_r - 1
    clamp_node = int(np.argmin(np.abs(r_nodes - clamp_m)))  # clamp 圆固支节点
    if abs(r_nodes[clamp_node] - clamp_m) > 1e-9:
        raise RuntimeError("clamp 半径未成为节点——网格化有 bug")

    # ---- 径向条带属性: 覆盖数 / D_e / 载荷面 ----
    rm_c = 0.5 * (r_nodes[:-1] + r_nodes[1:])          # 条带中点 (n_cell_r,)
    r0_c, r1_c = r_nodes[:-1], r_nodes[1:]
    dr_c = r1_c - r0_c
    cov_c = np.zeros(n_cell_r, dtype=int)
    De_c = np.zeros(n_cell_r)
    for s, D in zip(specs, [s["D"] for s in specs]):
        m = (rm_c >= s["a_m"] - TOL_M) & (rm_c <= s["b_m"] + TOL_M)
        cov_c[m] += 1
        De_c[m] += D
    if np.any(cov_c <= 0):
        raise ValueError("存在无任何阀片覆盖的径向条带 (刚度=0)")
    a0, b0 = specs[0]["a_m"], specs[0]["b_m"]
    face_c = (rm_c >= a0 - TOL_M) & (rm_c <= b0 + TOL_M)
    if not np.any(face_c):
        raise ValueError("顶片载荷面未覆盖任何径向条带")

    # ---- 周向: 全环均匀 ----
    dtheta = 2.0 * math.pi / n_theta
    th_nodes = np.arange(n_theta) * dtheta

    # ---- 单元表: e = e_r * n_theta + j, 回绕闭合 ----
    M = n_cell_r * n_theta
    j = np.arange(n_theta)
    jp = (j + 1) % n_theta
    conn = np.empty((M, 4), dtype=np.int64)
    for er in range(n_cell_r):
        e0 = er * n_theta
        conn[e0:e0 + n_theta, 0] = er * n_theta + j
        conn[e0:e0 + n_theta, 1] = er * n_theta + jp
        conn[e0:e0 + n_theta, 2] = (er + 1) * n_theta + jp
        conn[e0:e0 + n_theta, 3] = (er + 1) * n_theta + j

    # ---- 单元级数组 (由条带平铺) ----
    cov_e = np.repeat(cov_c, n_theta)
    De_e = np.repeat(De_c, n_theta)
    face_e = np.repeat(face_c, n_theta)
    # 梯形精确几何 (闭式, 不需要积分)
    area_c = 0.5 * dtheta * (r1_c ** 2 - r0_c ** 2)     # 每条带一个扇区面积
    rbar_c = (2.0 / 3.0) * (r1_c ** 3 - r0_c ** 3) / (r1_c ** 2 - r0_c ** 2)
    arc_out_c = r1_c * dtheta                            # 条带外缘弧长
    area_e = np.repeat(area_c, n_theta)
    rbar_e = np.repeat(rbar_c, n_theta)
    thbar_e = np.tile(th_nodes + 0.5 * dtheta, n_cell_r)

    # ---- 逐带汇总 (用于打印与断言) ----
    c0 = 0
    D1 = specs[0]["D"]
    for b in bands:
        n = b["n_rad"]
        if not (np.all(cov_c[c0:c0 + n] == cov_c[c0])
                and np.allclose(De_c[c0:c0 + n], De_c[c0])):
            raise RuntimeError("同一环带内属性不一致——网格化有 bug")
        b.update(coverage=int(cov_c[c0]), D_e=float(De_c[c0]),
                 D_over_D1=float(De_c[c0] / D1))
        c0 += n

    mesh = dict(kind="polar-2d", layers=specs, E=E, nu=nu,
                n_elem_base_radial=int(n_elem_base_radial),
                n_theta=n_theta, dtheta=dtheta,
                clamp_radius_mm=clamp_mm,
                r_c_mm=r_c * 1e3,
                r_nodes_m=r_nodes, theta_nodes=th_nodes,
                n_r=n_r, n_nodes=n_r * n_theta,
                bands=bands, n_cell_r=n_cell_r, n_elem=M,
                clamp_node=clamp_node,
                # 条带级 (n_cell_r,)
                r0_c=r0_c, r1_c=r1_c, rm_c=rm_c, dr_c=dr_c,
                cov_c=cov_c, De_c=De_c, face_c=face_c,
                area_c=area_c, rbar_c=rbar_c, arc_out_c=arc_out_c,
                # 单元级 (M,)
                conn=conn, coverage_e=cov_e, D_e_e=De_e, face_e=face_e,
                area_e=area_e, rbar_e=rbar_e, thbar_e=thbar_e,
                b_max_m=r_max)
    return mesh


def node_xy(mesh: dict):
    """节点直角坐标 (N,2), 平铺顺序与 global = i_r*nθ + j 一致."""
    th = mesh["theta_nodes"]
    x = mesh["r_nodes_m"][:, None] * np.cos(th)[None, :]
    y = mesh["r_nodes_m"][:, None] * np.sin(th)[None, :]
    return np.stack([x.ravel(), y.ravel()], axis=1)


# ------------------------------------------------------------
# 网格自检(几何不变量)
# ------------------------------------------------------------
def check_mesh_invariants(mesh: dict) -> list[str]:
    probs: list[str] = []
    r = mesh["r_nodes_m"]
    if np.any(np.diff(r) <= 0.0):
        probs.append("节点半径必须严格递增")
    for rc in mesh["r_c_mm"] * 1e-3:
        if np.min(np.abs(r - rc)) > 1e-9:
            probs.append(f"关键半径 {rc * 1e3:.6f} mm 不在节点坐标中")
    if np.any(mesh["cov_c"] < 1) or np.any(mesh["De_c"] <= 0.0):
        probs.append("存在覆盖数 < 1 或刚度 <= 0 的径向条带")
    if not np.any(mesh["face_c"]):
        probs.append("顶片载荷面没有覆盖任何单元")
    M = mesh["n_elem"]
    conn = mesh["conn"]
    if conn.shape != (M, 4) or int(conn.max()) >= mesh["n_nodes"] \
            or int(conn.min()) < 0:
        probs.append("单元连通性越界")
    # 几何守恒: Σ 面积 = π(b_max² − 域内缘²), 域内缘 = 最小关键半径(默认=clamp)
    a_sum = float(mesh["area_e"].sum())
    r_min = float(mesh["r_nodes_m"][0])
    expect = math.pi * (mesh["b_max_m"] ** 2 - r_min ** 2)
    if not math.isclose(a_sum, expect, rel_tol=1e-9):
        probs.append(f"面积守恒失败: ΣA={a_sum:.9e} vs πΔr²={expect:.9e}")
    # 计数守恒
    if M != sum(b["n_rad"] for b in mesh["bands"]) * mesh["n_theta"]:
        probs.append("单元计数与 径向×周向 细分不一致")
    return probs


def print_summary(mesh: dict) -> None:
    print("-" * 70)
    print("2D 极坐标梯形网格 (半径台阶 × 角度等分)")
    print("-" * 70)
    print(f"clamp = {mesh['clamp_radius_mm']:.4f} mm | "
          f"b_max = {mesh['b_max_m'] * 1e3:.4f} mm | "
          f"径向基数 {mesh['n_elem_base_radial']} | nθ = {mesh['n_theta']}")
    print(f"{'环带 r (mm)':>18} {'径向':>5} {'θ向':>5} {'单元数':>8} "
          f"{'覆盖k':>4} {'D_e/D₁':>8} {'外缘长宽比':>9}")
    for b in mesh["bands"]:
        r_outer = b["r1_m"]
        arc_outer = r_outer * mesh["dtheta"]
        dr_cell = (b["r1_m"] - b["r0_m"]) / b["n_rad"]
        print(f"{b['r0_m'] * 1e3:8.4f} ~ {b['r1_m'] * 1e3:7.4f} "
              f"{b['n_rad']:>6d} {mesh['n_theta']:>6d} "
              f"{b['n_rad'] * mesh['n_theta']:>9d} {b['coverage']:>6d} "
              f"{b['D_over_D1']:>10.4f} {arc_outer / dr_cell:>10.2f}")
    print(f"总计: {mesh['n_elem']} 单元, {mesh['n_nodes']} 节点")
    # 长宽比提示
    worst = max((b["r1_m"] * mesh["dtheta"]) /
                ((b["r1_m"] - b["r0_m"]) / b["n_rad"]) for b in mesh["bands"])
    if worst > 10.0:
        print(f"  ⚠ 最外圈长宽比 ≈ {worst:.1f} (远离 1): 2D 场景应放宽径向细分 "
              f"或增大 nθ, 见 references/polar-mesh-2d.md 长宽比纠正示例")
    probs = check_mesh_invariants(mesh)
    for p in probs:
        print("  ✗ " + p)
    if not probs:
        print("  网格自检: 全部 ✓")


def _selftest() -> None:
    print("=" * 70)
    print("build_mesh_2d 自检 (md 示例: Ø10/Ø9/Ø8, 内径6, t=0.1)")
    print("=" * 70)
    layers = [(6.0, 10.0, 0.1), (6.0, 9.0, 0.1), (6.0, 8.0, 0.1)]
    mesh = build_polar_mesh(layers, n_elem_base_radial=400, n_theta=120)
    print_summary(mesh)
    counts = [b["n_rad"] for b in mesh["bands"]]
    covs = [b["coverage"] for b in mesh["bands"]]
    assert counts == [200, 100, 100], counts          # 与 1D 径向一致
    assert covs == [3, 2, 1], covs
    assert mesh["n_elem"] == 48000
    assert mesh["n_nodes"] == 401 * 120
    assert not check_mesh_invariants(mesh)
    # 回绕闭合: 接缝两侧单元共享节点, 无越界
    assert int(mesh["conn"].max()) == mesh["n_nodes"] - 1
    # 面积守恒(已含于 check_mesh_invariants), 抽查首条带内第 1 个扇形单元
    A1 = mesh["area_c"][0]
    expect = mesh["dtheta"] / 2 * (mesh["r1_c"][0] ** 2 - mesh["r0_c"][0] ** 2)
    assert math.isclose(A1, expect, rel_tol=1e-12)

    # 等径 3 片 → 全环带覆盖 3
    m3 = build_polar_mesh([(6.0, 10.0, 0.1)] * 3, n_elem_base_radial=200,
                          n_theta=48)
    assert all(b["coverage"] == 3 for b in m3["bands"])
    assert m3["n_elem"] == 200 * 48
    assert not check_mesh_invariants(m3)

    # 薄环扇形: 关键半径 4 个; clamp=5mm 在最小内孔 4mm 外侧(同 1D 语义)
    m2 = build_polar_mesh([(8.0, 22.0, 0.20), (8.0, 20.0, 0.15)],
                          n_elem_base_radial=300, n_theta=96,
                          clamp_radius_mm=5.0)
    assert [b["coverage"] for b in m2["bands"]] == [2, 2, 1]
    assert [b["n_rad"] for b in m2["bands"]] == [50, 250, 50]
    assert m2["clamp_node"] == 50
    assert not check_mesh_invariants(m2)
    print(f"第二算例(Ø22/Ø20, clamp=5mm, nθ=96): {m2['n_elem']} 单元, "
          f"覆盖序列 {[b['coverage'] for b in m2['bands']]}")
    print("自检: 全部断言通过 ✓")


if __name__ == "__main__":
    _selftest()
