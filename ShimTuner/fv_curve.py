# -*- coding: utf-8 -*-
"""
ShimTuner 总入口：活塞杆速度 → 阻尼力（闭环）
==============================================
调用 **FlowSolver**（流道 → 压力场）与 **DeflectionSolver**（压力 → 挠度场），
在给定阀片堆叠与活塞几何下逐点迭代 Q → w → P → w′。

流量由活塞杆运动唯一决定：
    Q_valve = (A_p − A_rod)·v     通过活塞阀（本求解器输入）
    Q_rod   = A_rod·v             杆侵入油缸的体积速度

用法:
    python -X utf8 fv_curve.py                          # v–F（压缩，默认 6 点）
    python -X utf8 fv_curve.py --direction both          # 压缩 + 回弹（同一堆叠）
    python -X utf8 fv_curve.py --direction both --rebound-layers "18,42,0.2;18,36,0.15"
    python -X utf8 fv_curve.py --x-axis q                # 改画 Q–F
    python -X utf8 fv_curve.py --nl --csv out.csv --plot out.png

依赖：仓库根 `.venv`（numpy/scipy）；`--plot` 另需 matplotlib。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_FLOW = os.path.abspath(os.path.join(_HERE, "..", "FlowSolver"))
for _p in (_FLOW,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import channel_flow as cf      # noqa: E402  (FlowSolver)
import coupling as cp          # noqa: E402  (FlowSolver，内部调用 DeflectionSolver)

DEFAULT_LAYERS = "18,42,0.25;18,36,0.20;18,30,0.20"
LABELS = {"compression": "压缩", "rebound": "回弹"}


def parse_layers(text: str) -> list[tuple[float, float, float]]:
    layers = []
    for chunk in str(text).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.replace("，", ",").split(",")]
        if len(parts) != 3:
            raise SystemExit(f"堆叠格式应为 '内径,外径,厚度;...': 「{chunk}」")
        layers.append(tuple(float(p) for p in parts))
    if not layers:
        raise SystemExit("至少需要一片阀片")
    return layers


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ShimTuner 杆速–阻尼力闭环")
    ap.add_argument("--layers", default=DEFAULT_LAYERS,
                    help="压缩（或单侧）阀片堆叠 '内径,外径,厚度;...'（mm）")
    ap.add_argument("--rebound-layers", default=None,
                    help="回弹阀片堆叠；缺省时与 --layers 相同")
    ap.add_argument("--direction", choices=["comp", "reb", "both"],
                    default="comp", help="压缩 / 回弹 / 双向（默认 comp）")
    ap.add_argument("--x-axis", choices=["v", "q"], default="v",
                    help="曲线横轴：杆速 v 或活塞阀流量 Q（默认 v）")
    # 活塞
    ap.add_argument("--d-piston", type=float, default=46.0)
    ap.add_argument("--d-rod", type=float, default=18.0)
    ap.add_argument("--ports", type=int, default=6)
    ap.add_argument("--port-w", type=float, default=8.0,
                    help="窗口周向宽度（弧长）mm")
    ap.add_argument("--port-radial", type=float, default=4.0,
                    help="窗口径向宽度 mm")
    ap.add_argument("--port-r", type=float, default=16.0, help="窗口中心半径 mm")
    ap.add_argument("--clamp-diameter", type=float, default=None,
                    help="夹持直径 mm（优先于旧的 --seat 半径）")
    ap.add_argument("--seat", type=float, default=None,
                    help="[旧] 夹持半径 mm（等价于 --clamp-diameter 2×）")
    ap.add_argument("--deck", type=float, default=0.10)
    ap.add_argument("--shim-outer-diameter", type=float, default=None,
                    help="最大阀片直径 mm（优先于旧的 --shim-outer 半径）")
    ap.add_argument("--shim-outer", type=float, default=None,
                    help="[旧] 最大阀片半径 mm（等价于直径/2）")
    # 定常旁通孔（常通孔，固定几何、与迭代无关）
    ap.add_argument("--bypass-d", type=float, default=0.0,
                    help="常通孔直径 mm（0 = 无）")
    ap.add_argument("--bypass-n", type=int, default=0, help="常通孔个数")
    ap.add_argument("--bypass-cd", type=float, default=0.62,
                    help="常通孔流量系数")
    # 油液
    ap.add_argument("--rho", type=float, default=850.0)
    ap.add_argument("--cst", type=float, default=15.0)
    ap.add_argument("--back", type=float, default=0.0, help="背压 MPa")
    # 工况/迭代
    ap.add_argument("--v-min", type=float, default=0.02)
    ap.add_argument("--v-max", type=float, default=0.50)
    ap.add_argument("--points", type=int, default=6)
    ap.add_argument("--nr", type=int, default=16, help="径向单元")
    ap.add_argument("--nt", type=int, default=12, help="周向分段")
    ap.add_argument("--alpha", type=float, default=0.5)
    ap.add_argument("--tol", type=float, default=2.0, help="残差容差 µm")
    ap.add_argument("--max-iter", type=int, default=25)
    ap.add_argument("--nl", action="store_true", help="分层大挠度 vK")
    ap.add_argument("--nl-tol", type=float, default=1e-6,
                    help="vK Newton 相对容差（1e-4 可提速约 25%%）")
    ap.add_argument("--window-load", "--sector-load", dest="window_load",
                    action="store_true",
                    help="窗口分布载荷 v2（平滑周向调制+圆形射流斑, nθ≥48, 较慢）")
    ap.add_argument("--window-contrast", type=float, default=None,
                    help="周向调制对比度 c（W=1+c·cos(Nθ)，默认 1−覆盖率）")
    ap.add_argument("--no-sector", action="store_true",
                    help="关闭扇区降维（默认 N 重周期载荷只解 1/N 扇区）")
    ap.add_argument("--cd", type=float, default=0.70)
    ap.add_argument("--cf", type=float, default=0.30)
    # 输出
    ap.add_argument("--csv", default=None, help="写出 CSV 路径")
    ap.add_argument("--plot", default=None, help="写出 PNG 路径（需 matplotlib）")
    args = ap.parse_args(argv)

    clamp_d = (args.clamp_diameter if args.clamp_diameter is not None
               else 2.0 * (args.seat if args.seat is not None else 9.0))
    outer_d = (args.shim_outer_diameter if args.shim_outer_diameter is not None
               else 2.0 * (args.shim_outer if args.shim_outer is not None else 21.0))
    piston = cf.PistonSpec(
        d_piston_mm=args.d_piston, d_rod_mm=args.d_rod, n_ports=args.ports,
        port_width_mm=args.port_w, port_radial_mm=args.port_radial,
        port_radius_mm=args.port_r,
        seat_radius_mm=clamp_d / 2.0, deck_height_mm=args.deck,
        shim_outer_mm=outer_d / 2.0,
        bypass_d_mm=args.bypass_d, bypass_n=args.bypass_n,
        bypass_Cd=args.bypass_cd)
    fluid = cf.FluidSpec(rho=args.rho, cSt=args.cst, p_back_MPa=args.back)
    spec = cp.IterationSpec(alpha=args.alpha, tol_um=args.tol,
                            max_iter=args.max_iter, nl=args.nl,
                            n_radial=args.nr, n_theta=args.nt,
                            C_d=args.cd, C_f=args.cf,
                            window_load=args.window_load,
                            window_contrast=args.window_contrast,
                            sector_reduction=not args.no_sector)

    comp_layers = reb_layers = None
    if args.direction in ("comp", "both"):
        comp_layers = parse_layers(args.layers)
    if args.direction in ("reb", "both"):
        reb_layers = parse_layers(args.rebound_layers or args.layers)

    kin = cf.rod_flows(args.v_max, piston)
    A_p, A_rod, A_eff = kin["A_p_m2"], kin["A_rod_m2"], kin["A_eff_m2"]

    print("=" * 92)
    print("ShimTuner · 活塞杆速度 → 阻尼力   (FlowSolver ⇄ DeflectionSolver)")
    print("=" * 92)
    print(f"活塞: Ø{piston.d_piston_mm:g}/Ø{piston.d_rod_mm:g} mm → "
          f"A_p={A_p*1e6:.1f}, A_rod={A_rod*1e6:.1f}, A_eff={A_eff*1e6:.1f} mm²")
    print(f"流量: Q_valve=(A_p−A_rod)·v = {kin['Q_valve_m3s']*1e6:.1f} mL/s @ "
          f"v={args.v_max:g}; Q_rod=A_rod·v = {kin['Q_rod_m3s']*1e6:.1f} mL/s")
    if comp_layers:
        print(f"压缩堆叠: {len(comp_layers)} 片 {comp_layers}")
    if reb_layers:
        print(f"回弹堆叠: {len(reb_layers)} 片 {reb_layers}")
    A_b = piston.area_bypass_m2()
    if A_b > 0:
        print(f"常通孔: {piston.bypass_n}×Ø{piston.bypass_d_mm:g} mm, "
              f"A_b={A_b*1e6:.3f} mm², C_d={piston.bypass_Cd:g} "
              f"(固定几何, 与主阀并联)")
    print(f"油液: ρ={fluid.rho:g}, ν={fluid.cSt:g} cSt, 背压 "
          f"{fluid.p_back_MPa:g} MPa")
    print(f"迭代: α={spec.alpha:g}, tol={spec.tol_um:g} µm, "
          f"网格 {spec.n_radial}×{spec.n_theta_eff()}, "
          f"{'vK' if spec.nl else '线性'}")
    if spec.window_load:
        _pf, cov, contrast = cp.window_profile(piston, spec.window_contrast)
        print(f"载荷: 窗口分布（平滑）· N={piston.n_ports} · "
              f"对比度 c={contrast:.2f} · 覆盖率 {cov*100:.1f}% · "
              f"射流 {piston.n_ports} 个圆形高斯斑（较慢，建议单点/少点）")
    print("-" * 100)
    print(f"{'行程':>4} {'v(m/s)':>8} {'F(N)':>10} {'Q_valve':>9} "
          f"{'Q_byp':>8} {'Q_rod':>8} {'Δp(MPa)':>9} {'w_tip':>8} "
          f"{'h_port':>8} {'it':>3} {'res':>7}")

    vs = [args.v_min + (args.v_max - args.v_min) * i / max(1, args.points - 1)
          for i in range(args.points)]
    out = cp.rod_curve(vs, piston, fluid, comp_layers, spec, reb_layers)
    branches = out["branches"]
    all_pts = []
    for name in ("compression", "rebound"):
        if name not in branches:
            continue
        pts = branches[name]["points"]
        all_pts.extend(pts)
        for p in pts:
            print(f"{LABELS[name]:>4} {p['v_mps']:8.3f} {p['F_N']:10.1f} "
                  f"{p['Q_valve_m3s']*1e6:9.1f} {p['Q_bypass_m3s']*1e6:8.1f} "
                  f"{p['Q_rod_m3s']*1e6:8.1f} "
                  f"{p['dp_MPa']:9.4f} {p['w_tip_um']:8.1f} "
                  f"{p['h_port_um']:8.1f} {p['iterations']:3d} "
                  f"{p['residual_um']:7.3f}")

    n_ok = sum(b["n_converged"] for b in branches.values())
    print("-" * 100)
    print(f"汇总: {n_ok}/{len(all_pts)} 点收敛 · 总耗时 "
          f"{out['elapsed_ms']/1000:.1f} s")
    for name, b in branches.items():
        fs = [p["F_N"] for p in b["points"]]
        print(f"  {LABELS[name]}: F {min(fs):.1f} → {max(fs):.1f} N")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["direction", "v_m_s", "Q_valve_mL_s",
                             "Q_main_mL_s", "Q_bypass_mL_s",
                             "Q_rod_mL_s", "F_N", "dp_MPa", "w_tip_um",
                             "h_port_um", "p_jet_MPa", "A_th_mm2",
                             "A_bypass_mm2", "iterations", "residual_um",
                             "converged"])
            for p in all_pts:
                writer.writerow([p["direction"], f"{p['v_mps']:.6f}",
                                 f"{p['Q_valve_m3s']*1e6:.6f}",
                                 f"{p['Q_main_m3s']*1e6:.6f}",
                                 f"{p['Q_bypass_m3s']*1e6:.6f}",
                                 f"{p['Q_rod_m3s']*1e6:.6f}",
                                 f"{p['F_N']:.4f}", f"{p['dp_MPa']:.6f}",
                                 f"{p['w_tip_um']:.4f}", f"{p['h_port_um']:.4f}",
                                 f"{p['p_jet_MPa']:.6f}", f"{p['A_th_mm2']:.6f}",
                                 f"{p['A_bypass_mm2']:.6f}",
                                 p["iterations"], f"{p['residual_um']:.6f}",
                                 int(p["converged"])])
        print(f"CSV 已写出: {args.csv}")

    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                               "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=110)
            styles = {"compression": ("#0ea5e9", "-"), "rebound": ("#f59e0b", "--")}
            for name, b in branches.items():
                pts = b["points"]
                x = [p["v_mps"] for p in pts] if args.x_axis == "v" \
                    else [p["Q_valve_m3s"] * 1e6 for p in pts]
                ax.plot(x, [p["F_N"] for p in pts], "o", ls=styles[name][1],
                        color=styles[name][0], label=LABELS[name])
            ax.set_xlabel("杆速 v [m/s]" if args.x_axis == "v"
                          else "活塞阀流量 Q [mL/s]")
            ax.set_ylabel("阻尼力 F [N]")
            ax.set_title("ShimTuner 闭环 " +
                         ("v–F" if args.x_axis == "v" else "Q–F") + " 曲线")
            ax.grid(alpha=0.3)
            ax.legend()
            fig.tight_layout()
            fig.savefig(args.plot)
            print(f"图已写出: {args.plot}")
        except Exception as exc:                       # noqa: BLE001
            print(f"绘图失败（忽略）: {exc}")

    return 0 if n_ok == len(all_pts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
