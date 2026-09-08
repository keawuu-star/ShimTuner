# -*- coding: utf-8 -*-
"""
1D 轴对称环元径向网格生成模板 (按阀片半径台阶强制布节点)
========================================================
配套技能: .agents/skills/shim-stack-fem-mesh/
          (SKILL.md, references/radial-mesh-1d.md)
理论:     ShimTunerPhysics.md「锥形阀片堆叠」节
基准实现: shim_physics_core.py 的 ShimStackCore —— 该实现已含网格+组装+求解,
          轴对称小挠度任务请优先直接调用它; 本文件用于"只要网格数据"、
          移植改造或教学对照。

约定(与 shim_physics_core.py 完全一致):
  * layers 自上而下排列, layers[0] 顶面直接受阻尼油均布压强 p
  * 输入几何一律 mm; 内部一律 SI(m); 半径 = 直径 / 2 * 1e-3
  * 关键半径 = {clamp} ∪ {各片内半径} ∪ {各片外半径}, 每个都强制成为节点
  * clamp_radius_mm 默认 = min(d_in_mm)/2, clamp 圆处固支 (w=0, θ=0)
  * 相邻关键半径之间覆盖片数 k 恒定; 单元刚度 D_e = Σ D_i (只数单元中点覆盖的片)

输出 dict 契约(供组装/可视化):
  r_nodes_m / le_m / r_mid_m / coverage / D_e / load_mask / bands / ...

自检运行:  python build_mesh_1d.py
"""
from __future__ import annotations

import math

import numpy as np

STEEL_7C27MO2_E = 200.0e9   # Pa, Sandvik 7C27Mo2
STEEL_7C27MO2_NU = 0.30
TOL_M = 1e-12               # 半径去重/比较容差 (m)


# ------------------------------------------------------------
# 1. 基础工具
# ------------------------------------------------------------
def bending_stiffness(t_mm: float, E: float = STEEL_7C27MO2_E,
                      nu: float = STEEL_7C27MO2_NU) -> float:
    """单片弯曲刚度 D = E t^3 / (12(1-nu^2)) [N*m]."""
    t = t_mm * 1e-3
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


def _as_triple(x):
    """把 ShimLayer / dict / (d_in,d_out,t) 统一成 (d_in_mm, d_out_mm, t_mm)."""
    if isinstance(x, dict):
        return (float(x["d_in_mm"]), float(x["d_out_mm"]), float(x["t_mm"]))
    try:                                     # dataclass 或带属性对象
        return (float(x.d_in_mm), float(x.d_out_mm), float(x.t_mm))
    except AttributeError:
        return (float(x[0]), float(x[1]), float(x[2]))


def prepare_layers(layers, E: float, nu: float) -> list[dict]:
    """校验并补全几何/材料: 每个 dict 增加 a_m/b_m/D (SI)."""
    specs = []
    for i, l in enumerate(layers):
        d_in_mm, d_out_mm, t_mm = _as_triple(l)
        if d_in_mm <= 0.0 or d_out_mm <= d_in_mm or t_mm <= 0.0:
            raise ValueError(f"layers[{i}] 非法: d_in={d_in_mm}, d_out={d_out_mm}, t={t_mm}")
        specs.append(dict(d_in_mm=d_in_mm, d_out_mm=d_out_mm, t_mm=t_mm,
                          a_m=d_in_mm * 1e-3 / 2.0, b_m=d_out_mm * 1e-3 / 2.0,
                          D=bending_stiffness(t_mm, E, nu)))
    return specs


# ------------------------------------------------------------
# 2. 关键半径(半径台阶)
# ------------------------------------------------------------
def critical_radii_m(specs: list[dict], clamp_m: float) -> np.ndarray:
    """R_c = sort({clamp} ∪ {a_i} ∪ {b_i}), 去重 (m)."""
    rs = sorted({clamp_m, *[s["a_m"] for s in specs],
                 *[s["b_m"] for s in specs]})
    out = []
    for r in rs:
        if not out or r - out[-1] > TOL_M:
            out.append(r)
    return np.asarray(out, dtype=float)


def _count_elems(width_m: float, h_max: float) -> int:
    """带内单元数: max(1, ceil(width/h)), 但 ratio 恰为整数时对浮点噪声稳健."""
    ratio = width_m / h_max
    r = round(ratio)
    if abs(ratio - r) <= 1e-9 * max(1.0, ratio):   # 落在整数边界(噪声级) -> 取整
        return max(1, int(r))
    return max(1, int(math.ceil(ratio)))


# ------------------------------------------------------------
# 3. 主入口: 生成 1D 径向网格
# ------------------------------------------------------------
def build_mesh_1d(layers_mm, n_elem_base: int = 400,
                  clamp_radius_mm: float | None = None,
                  E: float = STEEL_7C27MO2_E,
                  nu: float = STEEL_7C27MO2_NU) -> dict:
    """
    按半径台阶生成轴对称环元径向网格.

    layers_mm       : [(d_in_mm, d_out_mm, t_mm), ...], 第 0 片为受油压顶片
    n_elem_base     : 目标径向单元总数(近似, 每带按 ceil 取整)
    clamp_radius_mm : 夹持半径, 默认 min(d_in_mm)/2
    返回 dict, 见文件头"输出 dict 契约".
    """
    if int(n_elem_base) < 1:
        raise ValueError("n_elem_base 必须 >= 1")
    specs = prepare_layers(layers_mm, E, nu)
    clamp_mm = (clamp_radius_mm if clamp_radius_mm is not None
                else min(s["d_in_mm"] for s in specs) / 2.0)
    clamp_m = clamp_mm * 1e-3

    # ---- 几何前置校验 (与 shim_physics_core.py 一致) ----
    if clamp_m < min(s["a_m"] for s in specs) - TOL_M:
        raise ValueError("clamp_radius 小于某阀片内半径会造成无支撑悬环")
    for s in specs:
        if s["b_m"] < clamp_m - TOL_M:
            raise ValueError("某片外半径 <= clamp, 整片在夹持内, 不参与变形")

    r_c = critical_radii_m(specs, clamp_m)
    r_max = float(r_c[-1])
    h_max = (r_max - clamp_m) / int(n_elem_base)

    # ---- 逐带细分: 网格域从最小关键半径(通常=clamp)开始, 每带端点共享 ----
    nodes, bands = [float(r_c[0])], []
    for r0, r1 in zip(r_c[:-1], r_c[1:]):
        n = _count_elems(r1 - r0, h_max)
        seg = np.linspace(r0, r1, n + 1)
        nodes.extend(seg[1:].tolist())
        bands.append(dict(r0_m=r0, r1_m=r1, n_elem=n))
    r_nodes = np.asarray(nodes, dtype=float)
    clamp_node = int(np.argmin(np.abs(r_nodes - clamp_m)))  # clamp 圆固支节点
    if abs(r_nodes[clamp_node] - clamp_m) > 1e-9:
        raise RuntimeError("clamp 半径未成为节点——网格化有 bug")

    # ---- 单元覆盖数 k 与刚度 D_e ----
    r_mid = 0.5 * (r_nodes[:-1] + r_nodes[1:])
    coverage = np.zeros(len(r_mid), dtype=int)
    D_e = np.zeros(len(r_mid))
    D_list = [s["D"] for s in specs]
    for s, D in zip(specs, D_list):
        m = (r_mid >= s["a_m"] - TOL_M) & (r_mid <= s["b_m"] + TOL_M)
        coverage[m] += 1
        D_e[m] += D
    if np.any(coverage <= 0):
        raise ValueError("存在无任何阀片覆盖的单元 (刚度=0): 请检查 clamp 与各片内径")
    for s in specs:                      # 每片都必须真正覆盖到网格
        m = (r_mid >= s["a_m"] - TOL_M) & (r_mid <= s["b_m"] + TOL_M)
        if not np.any(m):
            raise ValueError(f"阀片 d_in={s['d_in_mm']}, d_out={s['d_out_mm']} 未覆盖任何单元")

    # ---- 载荷面: 顶片环形面 [a0, b0] ----
    a0, b0 = specs[0]["a_m"], specs[0]["b_m"]
    load_mask = (r_mid >= a0 - TOL_M) & (r_mid <= b0 + TOL_M)

    # ---- 逐带汇总 ----
    c0 = 0
    for b in bands:
        k = coverage[c0]
        de = D_e[c0]
        if not (np.all(coverage[c0:c0 + b["n_elem"]] == k)
                and np.allclose(D_e[c0:c0 + b["n_elem"]], de)):
            raise RuntimeError("同一环带内单元属性不一致——网格化有 bug")
        b.update(coverage=int(k), D_e=float(de),
                 D_over_D1=float(de / D_list[0]))
        c0 += b["n_elem"]

    return dict(kind="radial-1d", layers=specs, E=E, nu=nu,
                n_elem_base=int(n_elem_base),
                clamp_radius_mm=clamp_mm,
                r_c_mm=r_c * 1e3,
                r_nodes_m=r_nodes, le_m=np.diff(r_nodes),
                r_mid_m=r_mid, coverage=coverage, D_e=D_e,
                load_mask=load_mask,
                bands=bands,
                clamp_node=clamp_node,
                n_nodes=len(r_nodes), n_elem=len(r_mid),
                b_max_m=r_max)


# ------------------------------------------------------------
# 4. 网格自检(几何不变量, 全部 ✓ 才算合格)
# ------------------------------------------------------------
def check_mesh_invariants(mesh: dict) -> list[str]:
    probs: list[str] = []
    r = mesh["r_nodes_m"]

    # 单调递增
    if np.any(np.diff(r) <= 0.0):
        probs.append("节点半径必须严格递增")
    # 关键半径都在节点上
    for rc in mesh["r_c_mm"] * 1e-3:
        if np.min(np.abs(r - rc)) > 1e-9:
            probs.append(f"关键半径 {rc * 1e3:.6f} mm 不在节点坐标中")
    # 覆盖与刚度
    if np.any(mesh["coverage"] < 1) or np.any(mesh["D_e"] <= 0.0):
        probs.append("存在覆盖数 < 1 或刚度 <= 0 的单元")
    # 单元/节点计数守恒
    if mesh["n_elem"] != sum(b["n_elem"] for b in mesh["bands"]) or \
            mesh["n_nodes"] != mesh["n_elem"] + 1:
        probs.append("单元/节点计数与逐带细分不一致")
    # 载荷面非空
    if not np.any(mesh["load_mask"]):
        probs.append("顶片载荷面没有覆盖任何单元")
    return probs


def print_summary(mesh: dict) -> None:
    print("-" * 70)
    print("1D 径向网格 (按半径台阶)")
    print("-" * 70)
    print(f"夹持半径 clamp = {mesh['clamp_radius_mm']:.4f} mm | "
          f"最外缘 b_max = {mesh['b_max_m'] * 1e3:.4f} mm | "
          f"n_elem_base = {mesh['n_elem_base']}")
    print(f"{'环带 r (mm)':>18} {'单元数':>6} {'覆盖k':>4} "
          f"{'D_e/D₁':>8}  带内宽度(mm)")
    for b in mesh["bands"]:
        print(f"{b['r0_m'] * 1e3:8.4f} ~ {b['r1_m'] * 1e3:7.4f} "
              f"{b['n_elem']:>8d} {b['coverage']:>6d} {b['D_over_D1']:>10.4f} "
              f"{(b['r1_m'] - b['r0_m']) * 1e3:12.6f}")
    print(f"总计: {mesh['n_elem']} 单元, {mesh['n_nodes']} 节点")
    probs = check_mesh_invariants(mesh)
    for p in probs:
        print("  ✗ " + p)
    if not probs:
        print("  网格自检: 全部 ✓")


# ------------------------------------------------------------
# 5. 自检
# ------------------------------------------------------------
def _selftest() -> None:
    print("=" * 70)
    print("build_mesh_1d 自检 (md 示例: Ø10/Ø9/Ø8 锥形堆叠, 内径6, t=0.1)")
    print("=" * 70)
    layers = [(6.0, 10.0, 0.1), (6.0, 9.0, 0.1), (6.0, 8.0, 0.1)]
    mesh = build_mesh_1d(layers, n_elem_base=400)
    print_summary(mesh)
    # 锁定关键行为: 带宽 1.0/0.5/0.5 mm -> 200/100/100, 覆盖 3/2/1
    counts = [b["n_elem"] for b in mesh["bands"]]
    covs = [b["coverage"] for b in mesh["bands"]]
    assert counts == [200, 100, 100], counts
    assert covs == [3, 2, 1], covs
    assert math.isclose(mesh["bands"][0]["D_over_D1"], 3.0)
    assert not check_mesh_invariants(mesh)

    # 单片: 整域覆盖 1, 单元数 = n_elem_base
    m1 = build_mesh_1d([(6.0, 10.0, 0.1)], n_elem_base=50)
    assert m1["n_elem"] == 50 and m1["bands"][0]["coverage"] == 1
    assert math.isclose(m1["bands"][0]["D_over_D1"], 1.0)

    # 等径 3 片堆叠: 刚度 ×3 (挠度应为单片 1/3)
    m3 = build_mesh_1d([(6.0, 10.0, 0.1)] * 3, n_elem_base=400)
    assert m3["bands"][0]["coverage"] == 3
    assert np.isclose(m3["bands"][0]["D_over_D1"], 3.0)

    # 第二算例: 活塞类大直径阀片, 自定义夹持半径 (clamp=5mm 在最小内孔 4mm 外侧,
    # 与 shim_physics_core 语义一致: 网格从 4mm 起, clamp 圆 5mm 处固支)
    m2 = build_mesh_1d([(8.0, 22.0, 0.20), (8.0, 20.0, 0.15)],
                       n_elem_base=300, clamp_radius_mm=5.0)
    assert m2["clamp_radius_mm"] == 5.0
    assert [b["coverage"] for b in m2["bands"]] == [2, 2, 1]
    assert [b["n_elem"] for b in m2["bands"]] == [50, 250, 50]
    assert m2["clamp_node"] == 50          # clamp 圆 = 内圈 50 单元后的节点
    assert not check_mesh_invariants(m2)
    print(f"第二算例(Ø22/Ø20 堆叠, clamp=5mm): "
          f"{m2['n_elem']} 单元, 覆盖序列 "
          f"{[b['coverage'] for b in m2['bands']]}")
    print("自检: 全部断言通过 ✓")


if __name__ == "__main__":
    _selftest()
