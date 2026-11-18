# -*- coding: utf-8 -*-
"""生成《物理模型与F-v曲线分析》报告的数据与图:
   汇报/fv_linear_vs_vk.csv
   汇报/fig_fv_linear_vs_vk.png

用法: python -X utf8 汇报/_gen_report_data.py
"""
from __future__ import annotations

import csv
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "FlowSolver"))

import channel_flow as cf     # noqa: E402
import coupling as cp         # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

LAYERS = [(18.0, 42.0, 0.25), (18.0, 36.0, 0.20), (18.0, 30.0, 0.20)]
VS = [0.05, 0.1, 0.2, 0.4, 0.6, 1.0, 1.5, 2.0, 2.5, 3.0]


def main() -> int:
    piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
    data = {}
    for nl in (False, True):
        spec = cp.IterationSpec(nl=nl, n_radial=16, n_theta=12,
                                max_iter=40, alpha=0.5, tol_um=2.0)
        data[nl] = cp.fv_curve(VS, piston, fluid, LAYERS, spec)["points"]

    csv_path = os.path.join(HERE, "fv_linear_vs_vk.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["model", "v_m_s", "F_N", "dp_MPa", "h_port_um",
                         "w_tip_um", "iterations", "residual_um", "converged"])
        for nl in (False, True):
            name = "vK" if nl else "linear"
            for p in data[nl]:
                writer.writerow([name, p["v_mps"], f"{p['F_N']:.3f}",
                                 f"{p['dp_MPa']:.5f}", f"{p['h_port_um']:.3f}",
                                 f"{p['w_tip_um']:.3f}", p["iterations"],
                                 f"{p['residual_um']:.4f}",
                                 int(p["converged"])])

    fig, axes = plt.subplots(1, 3, figsize=(14.2, 4.2), dpi=130)
    ax = axes[0]
    for nl, color, label in ((False, "#0ea5e9", "线性板 (nl=False)"),
                             (True, "#ef4444", "von Kármán (nl=True)")):
        ax.plot([p["v_mps"] for p in data[nl]],
                [p["F_N"] for p in data[nl]], "o-", color=color, lw=2, ms=4,
                label=label)
    ax.set_xlabel("杆速 v [m/s]")
    ax.set_ylabel("阻尼力 F [N]")
    ax.set_title("F(v)：线性 vs vK")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    ax = axes[1]
    for nl, color, label in ((False, "#0ea5e9", "线性板"),
                             (True, "#ef4444", "vK")):
        ax.plot([p["v_mps"] for p in data[nl]],
                [p["h_port_um"] for p in data[nl]], "o-", color=color, lw=2,
                ms=4, label=label)
    ax.set_xlabel("杆速 v [m/s]")
    ax.set_ylabel("窗口处间隙 h_port [µm]")
    ax.set_title("阀片开度 h_port(v)")
    ax.set_yscale("log")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    ax = axes[2]
    for nl, color, label in ((False, "#0ea5e9", "线性板"),
                             (True, "#ef4444", "vK")):
        xs = np.array([p["v_mps"] for p in data[nl]])
        ys = np.array([p["F_N"] for p in data[nl]])
        ax.plot(xs, np.gradient(ys, xs) / 1000.0, "o-", color=color, lw=2,
                ms=4, label=label)
    ax.set_xlabel("杆速 v [m/s]")
    ax.set_ylabel("局部斜率 dF/dv [kN·s/m]")
    ax.set_title("切线刚度：digressive vs progressive")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    fig.suptitle("默认 3 片阀片堆叠：线性板给出 digressive，"
                 "vK 膜刚化给出 progressive", fontsize=11)
    fig.tight_layout()
    png_path = os.path.join(HERE, "fig_fv_linear_vs_vk.png")
    fig.savefig(png_path, facecolor="white")

    for nl in (False, True):
        pts = data[nl]
        f0, f1 = pts[0]["F_N"], pts[-1]["F_N"]
        expo = math.log(f1 / f0) / math.log(VS[-1] / VS[0])
        print(f"nl={nl}: F(0.05)={f0:.1f} N, F(3)={f1:.1f} N, "
              f"指数≈{expo:.2f}, h_port@3={pts[-1]['h_port_um']:.0f} µm, "
              f"w_tip@3={pts[-1]['w_tip_um']:.0f} µm")
    print("已写出:", csv_path)
    print("已写出:", png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
