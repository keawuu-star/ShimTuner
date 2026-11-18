# -*- coding: utf-8 -*-
"""
阀片堆叠 2D 极坐标梯形(扇形)网格生成器 (Shim Stack Polar Mesh)
==============================================================
配套技能: .agents/skills/shim-stack-fem-mesh/
          (SKILL.md, references/polar-mesh-2d.md, scripts/build_mesh_2d.py)
理论依据: ShimTunerPhysics.md「锥形阀片堆叠」: "按半径差值将阀片在径向切分,
          并按角度将阀片均匀切分得到梯形元素; 在径向阀片台阶处切分阀片,
          在圆周上按角度切分"。

用途边界(技能核心规则):
  * 只有 非轴对称载荷/几何(偏心孔、周向变化油压、开口环、缺口等)才需要 2D
    网格; 轴对称任务请用 1D 环元(radial-mesh-1d.md / shim_physics_core.py),
    节点数相差 ~nθ 倍, 便宜得多。
  * 本模块 = 技能模板 scripts/build_mesh_2d.py 的项目级可 import 落点:
    契约与模板逐键一致(改模板时须同步本文件并跑自检)。
  * 网格只负责几何与属性(D_e/覆盖数/载荷面); 单元公式/材料如何进入单元矩阵
    由求解器决定, 不在网格层"等效厚度"。

约定(与 shim_physics_core.py 完全一致):
  * layers 自上而下排列, layers[0] 顶面直接受阻尼油均布压强 p
  * 输入几何一律 mm; 内部一律 SI(m); 半径 = 直径 / 2 * 1e-3
  * 关键半径 R_c = sorted({clamp} ∪ {a_i} ∪ {b_i}) 强制成为径向节点
    (去重/比较容差 1e-12 m; 台阶不在节点上会抹平刚度阶梯)
  * 相邻关键半径之间覆盖片数 k 恒定; 单元(条带)刚度 D_e = Σ D_i,
    D_i = E·t_i³/(12(1−ν²))  —— 只数径向单元中点被覆盖的阀片
  * clamp_radius_mm 默认 = min(d_in_mm)/2; clamp 圆整圈固支; 外缘自由

拓扑(polar-mesh-2d.md 规范):
  * 径向节点 r[0..n_r−1]; 周向全环 nθ 等分, dθ = 2π/nθ, 节点 θ_j = j·dθ
  * 全局节点号 r 优先: global = i_r·nθ + j; 节点总数 N = n_r·nθ
  * 单元号 e = e_r·nθ + j (e_r 径向单元序, 按带顺序); 总数 M = n_cell_r·nθ
  * 四边形逆时针 [n00, n01, n11, n10]; 周向 (j+1) % nθ 回绕闭合(无接缝)

输出 dict 契约(单元级数组长度 M; 径向条带级数组长度 n_cell_r):
  kind / layers / E / nu / n_elem_base_radial / n_theta / dtheta /
  clamp_radius_mm / r_c_mm / r_nodes_m / theta_nodes /
  n_r / n_nodes / n_cell_r / n_elem / clamp_node / bands / b_max_m
  条带级: r0_c / r1_c / rm_c / dr_c / cov_c / De_c / face_c /
          area_c / rbar_c / arc_out_c
  单元级: conn (M,4) / coverage_e / D_e_e / face_e / area_e /
          rbar_e / thbar_e

自检运行:  python -X utf8 shim_stack_mesh.py   (Windows 控制台中文保护)
基准回归:  python -X utf8 shim_physics_core.py  (本模块不改动 v0)
"""
from __future__ import annotations

import math
from typing import Sequence, Union

import numpy as np

from shim_physics_core import (ShimLayer, bending_stiffness_of,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)

TOL_M = 1e-12               # 半径去重/比较容差 (m)

# 每片几何可接受的三种表达: (d_in_mm, d_out_mm, t_mm) / dict / ShimLayer
LayerSpec = Union[tuple, list, dict, ShimLayer]

__all__ = ["build_polar_mesh", "node_xy", "check_mesh_invariants",
           "print_summary", "TOL_M"]


# ------------------------------------------------------------
# 1. 基础工具
# ------------------------------------------------------------
def _as_triple(x: LayerSpec) -> tuple[float, float, float]:
    """把 ShimLayer / dict / (d_in, d_out, t) 统一成 (d_in_mm, d_out_mm, t_mm)."""
    if isinstance(x, dict):
        return (float(x["d_in_mm"]), float(x["d_out_mm"]), float(x["t_mm"]))
    try:                                     # dataclass 或带属性对象
        return (float(x.d_in_mm), float(x.d_out_mm), float(x.t_mm))
    except AttributeError:
        return (float(x[0]), float(x[1]), float(x[2]))


def _prepare_layers(layers: Sequence[LayerSpec],
                    E: float, nu: float) -> list[dict]:
    """校验几何并补全 SI 量: 每个 spec 增加 a_m / b_m / D."""
    specs = []
    for i, l in enumerate(layers):
        d_in_mm, d_out_mm, t_mm = _as_triple(l)
        if d_in_mm <= 0.0 or d_out_mm <= d_in_mm or t_mm <= 0.0:
            raise ValueError(f"layers[{i}] 非法: d_in={d_in_mm}, "
                             f"d_out={d_out_mm}, t={t_mm}")
        specs.append(dict(d_in_mm=d_in_mm, d_out_mm=d_out_mm, t_mm=t_mm,
                          a_m=d_in_mm * 1e-3 / 2.0, b_m=d_out_mm * 1e-3 / 2.0,
                          D=bending_stiffness_of(t_mm, E, nu)))
    return specs


def _critical_radii_m(specs: list[dict], clamp_m: float) -> np.ndarray:
    """R_c = sort({clamp} ∪ {a_i} ∪ {b_i}), 容差去重 (m)."""
    rs = sorted({clamp_m, *[s["a_m"] for s in specs],
                 *[s["b_m"] for s in specs]})
    out = []
    for r in rs:
        if not out or r - out[-1] > TOL_M:
            out.append(r)
    return np.asarray(out, dtype=float)


def _count_elems(width_m: float, h_max: float) -> int:
    """带内径向单元数: max(1, ceil(width/h_max));
    ratio 恰为整数时浮点噪声可能把 100 算成 100.000…01 → 101,
    故用 1e-9 相对容差就近取整, 保证 h 落在目标尺寸整数倍时带数稳定."""
    ratio = width_m / h_max
    r = round(ratio)
    if abs(ratio - r) <= 1e-9 * max(1.0, ratio):   # 落在整数边界(噪声级)
        return max(1, int(r))
    return max(1, int(math.ceil(ratio)))


# ------------------------------------------------------------
# 2. 主入口: 生成 2D 极坐标 r-θ 梯形网格
# ------------------------------------------------------------
def build_polar_mesh(layers_mm: Sequence[LayerSpec],
                     n_elem_base_radial: int = 400,
                     n_theta: int = 120,
                     clamp_radius_mm: float | None = None,
                     E: float = STEEL_7C27MO2_E,
                     nu: float = STEEL_7C27MO2_NU,
                     sector: int = 1) -> dict:
    """
    按"半径台阶切分 × 角度等分"生成阀片堆叠的 2D 极坐标梯形网格.

    layers_mm          : 阀片列表, 自上而下; layers[0] 为受油压顶片.
                         每片 (d_in_mm, d_out_mm, t_mm), 或 dict / ShimLayer
    n_elem_base_radial : 目标径向单元总数(近似; 每带 ceil 取整, 与 1D 同语义)
    n_theta            : 周向等分段数(建议取偶数; 计算量 = 径向单元数 × n_theta)
    clamp_radius_mm    : 夹持(压紧螺栓)半径, 默认 min(d_in_mm)/2

    返回 dict, 契约见模块 docstring. 生成后先跑 check_mesh_invariants,
    并检查 print_summary 打印的长宽比提示再交给求解器.
    """
    if int(n_elem_base_radial) < 1:
        raise ValueError("n_elem_base_radial 必须 >= 1")
    if int(n_theta) < 3:
        raise ValueError("n_theta 必须 >= 3")
    n_theta = int(n_theta)
    sector = int(sector)
    if sector < 1:
        raise ValueError("sector（周期扇区数）必须 >= 1")
    specs = _prepare_layers(layers_mm, E, nu)
    clamp_mm = (clamp_radius_mm if clamp_radius_mm is not None
                else min(s["d_in_mm"] for s in specs) / 2.0)
    clamp_m = clamp_mm * 1e-3

    # ---- 几何前置校验 (与 shim_physics_core.py 一致) ----
    if clamp_m < min(s["a_m"] for s in specs) - TOL_M:
        raise ValueError("clamp_radius 小于某阀片内半径会造成无支撑悬环")
    for s in specs:
        if s["b_m"] < clamp_m - TOL_M:
            raise ValueError("某片外半径 <= clamp, 整片在夹持内, 不参与变形")

    r_c = _critical_radii_m(specs, clamp_m)
    r_max = float(r_c[-1])
    h_max = (r_max - clamp_m) / int(n_elem_base_radial)

    # ---- 径向: 关键半径 + 带内细分 (域从最小关键半径起, 与 1D 模板同一套) ----
    nodes, bands = [float(r_c[0])], []
    for r0, r1 in zip(r_c[:-1], r_c[1:]):
        n = _count_elems(r1 - r0, h_max)
        seg = np.linspace(r0, r1, n + 1)
        nodes.extend(seg[1:].tolist())       # 端点与邻带共享(只保留内点)
        bands.append(dict(r0_m=r0, r1_m=r1, n_rad=n))
    r_nodes = np.asarray(nodes, dtype=float)
    n_r = len(r_nodes)
    n_cell_r = n_r - 1
    clamp_node = int(np.argmin(np.abs(r_nodes - clamp_m)))  # clamp 圆固支节点
    if abs(r_nodes[clamp_node] - clamp_m) > 1e-9:
        raise RuntimeError("clamp 半径未成为节点——网格化有 bug")

    # ---- 径向条带属性: 覆盖数 k / 刚度 D_e / 受压面标记 ----
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
        raise ValueError("存在无任何阀片覆盖的径向条带 (刚度=0): "
                         "请检查 clamp 与各片内径")
    # 载荷面: 顶片环形面 [a0, b0] 上的条带 (供载荷装配)
    a0, b0 = specs[0]["a_m"], specs[0]["b_m"]
    face_c = (rm_c >= a0 - TOL_M) & (rm_c <= b0 + TOL_M)
    if not np.any(face_c):
        raise ValueError("顶片载荷面未覆盖任何径向条带")

    # ---- 周向: 全环（sector=1）或 1/N 扇区（sector=N，周期性边界）----
    # sector=1: n_theta 个节点/环，连通性 (j+1)%nθ 回绕，闭合整环；
    # sector>1: n_theta+1 个节点/环（两端重复一列作周期边界），不设回绕，
    #           由求解器把 θ=span 一列 DOF 绑定到 θ=0 一列（周期约束）。
    dtheta = 2.0 * math.pi / (n_theta * sector)
    periodic = sector > 1
    n_theta_nodes = n_theta + 1 if periodic else n_theta
    th_nodes = np.arange(n_theta_nodes) * dtheta

    # ---- 单元表: e = e_r * n_theta + j ----
    M = n_cell_r * n_theta
    j = np.arange(n_theta)
    conn = np.empty((M, 4), dtype=np.int64)
    for er in range(n_cell_r):
        e0 = er * n_theta
        base = er * n_theta_nodes
        nxt = (er + 1) * n_theta_nodes
        if periodic:
            conn[e0:e0 + n_theta, 0] = base + j
            conn[e0:e0 + n_theta, 1] = base + j + 1
            conn[e0:e0 + n_theta, 2] = nxt + j + 1
            conn[e0:e0 + n_theta, 3] = nxt + j
        else:
            jp = (j + 1) % n_theta
            conn[e0:e0 + n_theta, 0] = base + j
            conn[e0:e0 + n_theta, 1] = base + jp
            conn[e0:e0 + n_theta, 2] = nxt + jp
            conn[e0:e0 + n_theta, 3] = nxt + j

    # ---- 单元级数组 (由条带平铺) ----
    cov_e = np.repeat(cov_c, n_theta)
    De_e = np.repeat(De_c, n_theta)
    face_e = np.repeat(face_c, n_theta)
    # 梯形(扇形)精确几何, 闭式表达 (不需数值积分)
    area_c = 0.5 * dtheta * (r1_c ** 2 - r0_c ** 2)     # 单扇区面积
    rbar_c = (2.0 / 3.0) * (r1_c ** 3 - r0_c ** 3) \
        / (r1_c ** 2 - r0_c ** 2)                       # 形心半径
    arc_out_c = r1_c * dtheta                            # 外缘弧长
    area_e = np.repeat(area_c, n_theta)
    rbar_e = np.repeat(rbar_c, n_theta)
    thbar_e = np.tile(th_nodes[:n_theta] + 0.5 * dtheta, n_cell_r)

    # ---- 逐带汇总 (打印/断言用; 覆盖与刚度在带内必须恒定) ----
    c0 = 0
    D1 = specs[0]["D"]
    for b in bands:
        n = b["n_rad"]
        if not (np.all(cov_c[c0:c0 + n] == cov_c[c0])
                and np.allclose(De_c[c0:c0 + n], De_c[c0])):
            raise RuntimeError("同一环带内单元属性不一致——网格化有 bug")
        b.update(coverage=int(cov_c[c0]), D_e=float(De_c[c0]),
                 D_over_D1=float(De_c[c0] / D1))
        c0 += n

    return dict(kind="polar-2d", layers=specs, E=E, nu=nu,
                n_elem_base_radial=int(n_elem_base_radial),
                n_theta=n_theta, n_theta_nodes=n_theta_nodes,
                dtheta=dtheta, sector=sector, periodic=periodic,
                theta_span=dtheta * n_theta,
                clamp_radius_mm=clamp_mm,
                r_c_mm=r_c * 1e3,
                r_nodes_m=r_nodes, theta_nodes=th_nodes,
                n_r=n_r, n_nodes=n_r * n_theta_nodes,
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


def node_xy(mesh: dict) -> np.ndarray:
    """节点直角坐标 (N, 2), 平铺顺序与 global = i_r*nθ + j 一致."""
    th = mesh["theta_nodes"]
    x = mesh["r_nodes_m"][:, None] * np.cos(th)[None, :]
    y = mesh["r_nodes_m"][:, None] * np.sin(th)[None, :]
    return np.stack([x.ravel(), y.ravel()], axis=1)


# ------------------------------------------------------------
# 3. 网格自检(几何不变量, 技能验证清单条目 1/2/6 的机械检查)
# ------------------------------------------------------------
def check_mesh_invariants(mesh: dict) -> list[str]:
    """返回问题列表; 空列表 = 全部通过."""
    probs: list[str] = []
    r = mesh["r_nodes_m"]
    # 径向单调
    if np.any(np.diff(r) <= 0.0):
        probs.append("节点半径必须严格递增")
    # 关键半径都是节点
    for rc in mesh["r_c_mm"] * 1e-3:
        if np.min(np.abs(r - rc)) > 1e-9:
            probs.append(f"关键半径 {rc * 1e3:.6f} mm 不在节点坐标中")
    # 条带覆盖/刚度
    if np.any(mesh["cov_c"] < 1) or np.any(mesh["De_c"] <= 0.0):
        probs.append("存在覆盖数 < 1 或刚度 <= 0 的径向条带")
    # 载荷面非空
    if not np.any(mesh["face_c"]):
        probs.append("顶片载荷面没有覆盖任何单元")
    # 连通性越界
    M = mesh["n_elem"]
    conn = mesh["conn"]
    if conn.shape != (M, 4) or int(conn.max()) >= mesh["n_nodes"] \
            or int(conn.min()) < 0:
        probs.append("单元连通性越界")
    # 几何守恒: ΣA = (θ_span/2)·(b_max² − 域内缘²); 全环时 θ_span=2π
    a_sum = float(mesh["area_e"].sum())
    r_min = float(r[0])
    theta_span = mesh.get("theta_span", 2.0 * math.pi)
    expect = 0.5 * theta_span * (mesh["b_max_m"] ** 2 - r_min ** 2)
    if not math.isclose(a_sum, expect, rel_tol=1e-9):
        probs.append(f"面积守恒失败: ΣA={a_sum:.9e} vs πΔr²={expect:.9e}")
    # 计数守恒: M = Σ带内径向单元 × nθ
    if M != sum(b["n_rad"] for b in mesh["bands"]) * mesh["n_theta"]:
        probs.append("单元计数与 径向×周向 细分不一致")
    return probs


def print_summary(mesh: dict) -> None:
    """打印网格概览 + 自检结果 + 长宽比提示(技能: 务必检查后再装配)."""
    print("-" * 70)
    print("2D 极坐标梯形网格 (半径台阶 × 角度等分)")
    print("-" * 70)
    sec = int(mesh.get("sector", 1))
    print(f"clamp = {mesh['clamp_radius_mm']:.4f} mm | "
          f"b_max = {mesh['b_max_m'] * 1e3:.4f} mm | "
          f"径向基数 {mesh['n_elem_base_radial']} | nθ = {mesh['n_theta']}"
          + (f" | 周期扇区 1/{sec}" if sec > 1 else ""))
    print(f"{'环带 r (mm)':>18} {'径向':>5} {'θ向':>5} {'单元数':>8} "
          f"{'覆盖k':>4} {'D_e/D₁':>8} {'外缘长宽比':>9}")
    for b in mesh["bands"]:
        arc_outer = b["r1_m"] * mesh["dtheta"]
        dr_cell = (b["r1_m"] - b["r0_m"]) / b["n_rad"]
        print(f"{b['r0_m'] * 1e3:8.4f} ~ {b['r1_m'] * 1e3:7.4f} "
              f"{b['n_rad']:>6d} {mesh['n_theta']:>6d} "
              f"{b['n_rad'] * mesh['n_theta']:>9d} {b['coverage']:>6d} "
              f"{b['D_over_D1']:>10.4f} {arc_outer / dr_cell:>10.2f}")
    print(f"总计: {mesh['n_elem']} 单元, {mesh['n_nodes']} 节点")
    worst = max((b["r1_m"] * mesh["dtheta"]) /
                ((b["r1_m"] - b["r0_m"]) / b["n_rad"])
                for b in mesh["bands"])
    if worst > 10.0:
        print(f"  ⚠ 最外圈长宽比 ≈ {worst:.1f} (远离 1): 2D 场景应放宽径向细分 "
              f"或增大 nθ, 见 references/polar-mesh-2d.md 长宽比纠正示例")
    probs = check_mesh_invariants(mesh)
    for p in probs:
        print("  ✗ " + p)
    if not probs:
        print("  网格自检: 全部 ✓")


# ------------------------------------------------------------
# 4. 自检 (技能验证清单: 几何不变量/计数/守恒/回绕)
# ------------------------------------------------------------
def _selftest() -> None:
    print("=" * 70)
    print("shim_stack_mesh 自检 (md 示例: Ø10/Ø9/Ø8 锥形堆叠, 内径6, t=0.1)")
    print("=" * 70)
    layers = [(6.0, 10.0, 0.1), (6.0, 9.0, 0.1), (6.0, 8.0, 0.1)]
    mesh = build_polar_mesh(layers, n_elem_base_radial=400, n_theta=120)
    print_summary(mesh)
    # 锁定关键行为: 环带 1.0/0.5/0.5 mm -> 径向 200/100/100, 覆盖 3/2/1
    counts = [b["n_rad"] for b in mesh["bands"]]
    covs = [b["coverage"] for b in mesh["bands"]]
    assert counts == [200, 100, 100], counts
    assert covs == [3, 2, 1], covs
    assert mesh["n_elem"] == 48000          # 400 × 120
    assert mesh["n_nodes"] == 401 * 120     # 401 × 120
    assert not check_mesh_invariants(mesh)
    # 回绕闭合: 无越界, 最大节点号 = N−1
    assert int(mesh["conn"].max()) == mesh["n_nodes"] - 1
    # 面积守恒(已含于不变量), 抽查首条带第 1 个扇形单元的闭式面积
    A1 = mesh["area_c"][0]
    expect = mesh["dtheta"] / 2 * (mesh["r1_c"][0] ** 2 - mesh["r0_c"][0] ** 2)
    assert math.isclose(A1, expect, rel_tol=1e-12)

    # 等径 3 片: 全带覆盖 3, 计数 = 径向 × nθ
    m3 = build_polar_mesh([(6.0, 10.0, 0.1)] * 3, n_elem_base_radial=200,
                          n_theta=48)
    assert all(b["coverage"] == 3 for b in m3["bands"])
    assert m3["n_elem"] == 200 * 48
    assert not check_mesh_invariants(m3)

    # 第二算例: 活塞类大直径阀片 + 自定义夹持半径 (clamp=5mm 在最小内孔 4mm
    # 外侧; 网格从 4mm 起建, clamp 圆 5mm 处为固支圈 —— 同 1D/基准实现语义)
    m2 = build_polar_mesh([(8.0, 22.0, 0.20), (8.0, 20.0, 0.15)],
                          n_elem_base_radial=300, n_theta=96,
                          clamp_radius_mm=5.0)
    assert [b["coverage"] for b in m2["bands"]] == [2, 2, 1]
    assert [b["n_rad"] for b in m2["bands"]] == [50, 250, 50]
    assert m2["clamp_node"] == 50           # clamp 圆 = 内圈 50 单元后的节点
    assert not check_mesh_invariants(m2)
    print(f"第二算例(Ø22/Ø20 堆叠, clamp=5mm, nθ=96): {m2['n_elem']} 单元, "
          f"覆盖序列 {[b['coverage'] for b in m2['bands']]}")

    # 输入兼容性: ShimLayer / dict 混用 (技能规则: 与 ShimStackCore 同输入)
    from shim_physics_core import ShimLayer
    m4 = build_polar_mesh([ShimLayer(6.0, 10.0, 0.1),
                           dict(d_in_mm=6.0, d_out_mm=9.0, t_mm=0.1),
                           (6.0, 8.0, 0.1)],
                          n_elem_base_radial=200, n_theta=36)
    assert [b["coverage"] for b in m4["bands"]] == [3, 2, 1]
    assert not check_mesh_invariants(m4)
    print(f"输入兼容性(ShimLayer/dict/tuple 混用): {m4['n_elem']} 单元 ✓")
    print("自检: 全部断言通过 ✓")


if __name__ == "__main__":
    _selftest()
