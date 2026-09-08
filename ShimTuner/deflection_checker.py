# -*- coding: utf-8 -*-
"""
ShimTuner 物理内核数值检验 UI (Tkinter + matplotlib)
====================================================
运行: python deflection_checker.py

功能:
  * 左侧: 编辑阀片堆叠参数 (每行: 内径, 外径, 厚度 [mm]),
         第 1 行 = 受油压的顶片, 往下依次排列 (与 ShimTunerPhysics.md 示例一致)。
  * 右侧上图: 各阀片外缘的 挠度-压强 h(p) 曲线, 灰色虚线为"顶片单片
         解析解"参照 (md 公式)。
  * 右侧下图: p_max 下整条挠度剖面 w(r), 竖虚线标记每片外缘位置。
  * 状态栏: 数值核验摘要 + 小挠度适用性警告 (h < 0.2·t 才满足线性假设)。
"""
from __future__ import annotations

import sys
import os
import time
import traceback

import numpy as np
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shim_physics_core import (ShimStackCore, ShimLayer,
                               analytic_edge_deflection,
                               analytic_phi,
                               STEEL_7C27MO2_E, STEEL_7C27MO2_NU)
from shim_fem_plate import PolarShimFEM

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, \
    NavigationToolbar2Tk
from matplotlib import font_manager, rcParams
from matplotlib.lines import Line2D

# ---- 中文字体 (按可用性挑选, 找不到时 matplotlib 会回退并出现方块) ----
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _name in ("Microsoft YaHei", "SimHei", "KaiTi",
              "Noto Sans CJK SC", "PingFang SC", "WenQuanYi Zen Hei"):
    if _name in _avail:
        rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
        break
rcParams["axes.unicode_minus"] = False

N_ELEM_BASE = 400          # 径向单元数(用于 FEM 网格密度)
P_CURVE_POINTS = 101       # h(p) 曲线取点数


class ShimStackCheckerApp:
    """校验 UI: 输入堆叠 -> 数值核对 -> 绘图."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("ShimTuner 物理内核 v0 — 阀片堆叠 挠度-压强 数值检验")
        root.geometry("1320x820")

        # ---------------- 左侧: 参数面板 ----------------
        ctrl = tk.Frame(root, width=360)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        ctrl.pack_propagate(False)

        tk.Label(ctrl, text="阀片堆叠 (第 1 行 = 受油压顶片, 自上而下)",
                 font=tkfont.Font(weight="bold")).pack(anchor="w")
        tk.Label(ctrl, text="每行: 内径, 外径, 厚度 [mm]",
                 fg="#555").pack(anchor="w")

        rows_f = tk.Frame(ctrl)
        rows_f.pack(fill=tk.BOTH, expand=True, pady=(2, 6))
        self.txt_layers = tk.Text(rows_f, height=9, width=34,
                                  font=("Consolas", 10))
        sb = tk.Scrollbar(rows_f, command=self.txt_layers.yview)
        self.txt_layers.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_layers.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.txt_layers.insert("1.0",
                               "6, 10, 0.1\n6, 9, 0.1\n6, 8, 0.1")

        # 材料/载荷参数
        par = tk.Frame(ctrl)
        par.pack(fill=tk.X, pady=2)
        self.var_e = tk.StringVar(value=str(STEEL_7C27MO2_E / 1e9))
        self.var_nu = tk.StringVar(value=str(STEEL_7C27MO2_NU))
        self.var_pmax = tk.StringVar(value="1.0")
        for lab, var in (("E [GPa]      ", self.var_e),
                         ("ν             ", self.var_nu),
                         ("p_max [MPa]   ", self.var_pmax)):
            r = tk.Frame(par)
            r.pack(fill=tk.X, pady=1)
            tk.Label(r, text=lab, width=13, anchor="w").pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, width=10).pack(side=tk.LEFT)

        # 按钮
        btns = tk.Frame(ctrl)
        btns.pack(fill=tk.X, pady=4)
        tk.Button(btns, text="重算", width=8, command=self.recalc,
                  bg="#cfe8cf").pack(side=tk.LEFT)
        tk.Button(btns, text="单片", width=6,
                  command=lambda: self._preset(["6, 10, 0.1"])).pack(
            side=tk.LEFT, padx=2)
        tk.Button(btns, text="锥形10/9/8", width=9,
                  command=lambda: self._preset(
                      ["6, 10, 0.1", "6, 9, 0.1", "6, 8, 0.1"])).pack(
            side=tk.LEFT, padx=2)
        tk.Button(btns, text="等径×3", width=6,
                  command=lambda: self._preset(
                      ["6, 10, 0.1"] * 3)).pack(side=tk.LEFT, padx=2)
        tk.Button(btns, text="+行", width=4,
                  command=self._append_row).pack(side=tk.LEFT, padx=2)
        tk.Button(btns, text="-行", width=4,
                  command=self._pop_row).pack(side=tk.LEFT, padx=2)

        # 求解器模式: 1D 环元 (v0) / 2D 极坐标 FEM (Mindlin 板)
        sol = tk.LabelFrame(ctrl, text="求解器模式", fg="#333")
        sol.pack(fill=tk.X, pady=(2, 4))
        self.var_solver = tk.StringVar(value="1D")
        tk.Radiobutton(sol, text="1D 环元 (v0)", value="1D",
                       variable=self.var_solver).pack(side=tk.LEFT)
        tk.Radiobutton(sol, text="2D FEM (Mindlin)", value="2D",
                       variable=self.var_solver).pack(side=tk.LEFT)
        fp = tk.Frame(ctrl)
        fp.pack(fill=tk.X, pady=(0, 2))
        tk.Label(fp, text="FEM 径向 n:", width=9, anchor="w").pack(
            side=tk.LEFT)
        self.var_fem_nr = tk.StringVar(value="60")
        tk.Entry(fp, textvariable=self.var_fem_nr, width=6).pack(side=tk.LEFT)
        tk.Label(fp, text="周向 nθ:", width=7, anchor="e").pack(
            side=tk.LEFT)
        self.var_fem_nt = tk.StringVar(value="0")   # 0 = 自动(偶数)
        tk.Entry(fp, textvariable=self.var_fem_nt, width=6).pack(side=tk.LEFT)
        tk.Label(ctrl, text="FEM 参数: 径向 n≥4; nθ=0 自动\n"
                            "(2D 模式重算约 1~4 s, h(p) 线性放大即时)",
                 fg="#888", font=("Microsoft YaHei", 8)).pack(anchor="w")

        # 状态区
        tk.Label(ctrl, text="数值核验 / 状态", fg="#2a5a2a",
                 font=tkfont.Font(weight="bold")).pack(anchor="w", pady=(6, 0))
        self.txt_status = tk.Text(ctrl, height=11, width=42, wrap=tk.WORD,
                                  state=tk.DISABLED, bg="#f6f8f6")
        self.txt_status.pack(fill=tk.BOTH, expand=True)

        # ---------------- 右侧: 绘图区 ----------------
        self.fig = Figure(figsize=(9.4, 7.2), dpi=100)
        self.ax_hp = self.fig.add_subplot(2, 1, 1)   # h(p)
        self.ax_wr = self.fig.add_subplot(2, 1, 2)   # w(r) 剖面
        self.canvas = FigureCanvasTkAgg(self.fig, master=root)
        self.canvas.get_tk_widget().pack(side=tk.RIGHT, fill=tk.BOTH,
                                         expand=True)
        bar = NavigationToolbar2Tk(self.canvas, root)
        bar.update()

        self.recalc()

    # ---------------- 输入处理 ----------------
    def _preset(self, rows):
        self.txt_layers.delete("1.0", tk.END)
        self.txt_layers.insert("1.0", "\n".join(rows))
        self.recalc()

    def _append_row(self):
        self.txt_layers.insert(tk.END, "\n6, 10, 0.1")

    def _pop_row(self):
        lines = [l for l in self.txt_layers.get("1.0", tk.END).splitlines()
                 if l.strip()]
        if lines:
            self.txt_layers.delete("1.0", tk.END)
            self.txt_layers.insert("1.0", "\n".join(lines[:-1]))

    def _parse(self):
        """解析输入 -> (layers, E, nu, p_max). 出错抛出 ValueError(中文信息)."""
        lines = [l for l in self.txt_layers.get("1.0", tk.END).splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        if not lines:
            raise ValueError("请至少输入一片阀片")
        layers = []
        for ln in lines:
            parts = [p.strip() for p in ln.replace("，", ",").split(",")]
            if len(parts) != 3:
                raise ValueError(f"行格式应为 内径,外径,厚度: 「{ln}」")
            din, dout, t = (float(parts[0]), float(parts[1]),
                            float(parts[2]))
            layers.append(ShimLayer(din, dout, t))
        e_gpa = float(self.var_e.get())
        nu = float(self.var_nu.get())
        p_max = float(self.var_pmax.get()) * 1e6
        if e_gpa <= 0 or not (0 <= nu < 0.5) or p_max <= 0:
            raise ValueError("E > 0, 0 ≤ ν < 0.5, p_max > 0")
        return layers, e_gpa * 1e9, nu, p_max

    # ---------------- 计算与绘图 ----------------
    def recalc(self):
        try:
            layers, e_pa, nu, p_max = self._parse()
        except Exception as exc:                        # 输入/几何错误
            self._set_status(f"输入错误: {exc}\n\n{traceback.format_exc(limit=2)}")
            return
        if self.var_solver.get() == "2D":
            self._run_2d(layers, e_pa, nu, p_max)
        else:
            self._run_1d(layers, e_pa, nu, p_max)

    def _run_1d(self, layers, e_pa, nu, p_max):
        """求解器 = 1D 环元 (v0): 原路径, 行为不变."""
        try:
            core = ShimStackCore(layers, E=e_pa, nu=nu, n_elem_base=N_ELEM_BASE)
        except Exception as exc:                        # 几何错误
            self._set_status(f"1D 求解失败: {exc}\n\n"
                             f"{traceback.format_exc(limit=2)}")
            return

        p_mpa = p_max / 1e6
        ps = np.linspace(0.0, p_max, P_CURVE_POINTS)
        tips = core.edge_deflection_curve(ps)          # (n_layers, n_p)

        # 顶片单片解析参照 (md 公式)
        top = layers[0]
        h_an_pmax = analytic_edge_deflection(top.d_in_mm, top.d_out_mm,
                                             top.t_mm, p_max, e_pa, nu)

        # ---------- 上图: h(p) ----------
        ax = self.ax_hp
        ax.cla()
        ax.set_title("阀片外缘 挠度-压强 曲线 h(p)  (1D 环元 v0)")
        ax.set_xlabel("压强 p [MPa]")
        ax.set_ylabel("外缘挠度 h [mm]")
        ax.grid(True, alpha=0.35)
        ax.plot([0.0, p_mpa], [0.0, h_an_pmax * 1e3], "k--", lw=1.4,
                label=f"单片参照(解析) Ø{top.d_out_mm:g}/Ø{top.d_in_mm:g}")
        colors = [f"C{i}" for i in range(len(layers))]
        for i, (tip, l) in enumerate(zip(tips, layers)):
            ax.plot(ps / 1e6, tip * 1e3, color=colors[i], lw=1.8,
                    label=f"第{i + 1}片 Ø{l.d_out_mm:g} 外缘")
        ax.legend(fontsize=8, loc="upper left")

        # ---------- 下图: w(r) 剖面 ----------
        r_prof, w_prof = core.profile_at(p_max)
        ax2 = self.ax_wr
        ax2.cla()
        ax2.set_title(f"挠度剖面 w(r) @ p = {p_mpa:g} MPa")
        ax2.set_xlabel("半径 r [mm]")
        ax2.set_ylabel("挠度 w [mm]")
        ax2.grid(True, alpha=0.35)
        ax2.plot(r_prof * 1e3, w_prof * 1e3, "-", color="#333", lw=1.8)
        handles = [Line2D([], [], color="#333", lw=1.8,
                          label="w(r) (1D v0)")]
        for i, l in enumerate(layers):
            ax2.axvline(l.b_m * 1e3, color=colors[i], ls=":", lw=1.2)
            handles.append(Line2D([], [], color=colors[i], ls=":",
                                  label=f"第{i + 1}片外缘 Ø{l.d_out_mm:g}"))
        ax2.axvline(core.clamp_radius_m * 1e3, color="r", ls="--", lw=1.0)
        handles.append(Line2D([], [], color="r", ls="--",
                              label="固支内缘"))
        ax2.legend(handles=handles, fontsize=7, loc="upper left")
        self.fig.tight_layout()
        self.canvas.draw()

        self._report(core, layers, ps, tips, h_an_pmax, e_pa, nu)

    def _run_2d(self, layers, e_pa, nu, p_max):
        """求解器 = 2D 极坐标 Mindlin 板 FEM; 叠加 1D v0 与解析参照."""
        try:
            nr = int(float(self.var_fem_nr.get()))
            nt = int(float(self.var_fem_nt.get()))
            if nr < 4 or nt < 0 or nt == 1:
                raise ValueError("径向 n ≥ 4; 周向 nθ=0 自动 或 ≥3")
            t0 = time.perf_counter()
            fem = PolarShimFEM(layers, E=e_pa, nu=nu,
                               n_elem_base_radial=nr, n_theta=nt)
            core = ShimStackCore(layers, E=e_pa, nu=nu,
                                 n_elem_base=N_ELEM_BASE)
            t_wall = time.perf_counter() - t0
        except Exception as exc:
            self._set_status(f"2D FEM 求解失败: {exc}\n\n"
                             f"{traceback.format_exc(limit=3)}")
            return

        p_mpa = p_max / 1e6
        ps = np.linspace(0.0, p_max, P_CURVE_POINTS)
        tips_f = fem.tip_deflections_unit()[:, None] * ps[None, :]   # (nl,np)
        tips_1 = core.edge_deflection_curve(ps)                      # 1D v0
        top = layers[0]
        h_an_pmax = analytic_edge_deflection(top.d_in_mm, top.d_out_mm,
                                             top.t_mm, p_max, e_pa, nu)
        colors = [f"C{i}" for i in range(len(layers))]

        # ---------- 上图: h(p) FEM vs 1D ----------
        ax = self.ax_hp
        ax.cla()
        ax.set_title("阀片外缘 h(p): 2D FEM 实线 vs 1D v0 虚线  "
                     "(单片解析参照 = 点线)")
        ax.set_xlabel("压强 p [MPa]")
        ax.set_ylabel("外缘挠度 h [mm]")
        ax.grid(True, alpha=0.35)
        handles = [Line2D([], [], color="k", ls=":", lw=1.6,
                          label=f"单片参照(解析) Ø{top.d_out_mm:g}"
                                f"/Ø{top.d_in_mm:g}")]
        for i, l in enumerate(layers):
            ax.plot(ps / 1e6, tips_f[i] * 1e3, color=colors[i], lw=2.0,
                    label=f"第{i + 1}片 Ø{l.d_out_mm:g} FEM-2D")
            ax.plot(ps / 1e6, tips_1[i] * 1e3, color=colors[i], lw=1.0,
                    ls=(0, (5, 2)), label=f"第{i + 1}片 Ø{l.d_out_mm:g} 1D")
        ax.legend(fontsize=7.5, loc="upper left")

        # ---------- 下图: w(r) 环向平均剖面 ----------
        r_f, wf_u = fem.profile_unit_w()
        r_1, w1 = core.profile_at(p_max)
        ax2 = self.ax_wr
        ax2.cla()
        ax2.set_title(f"挠度剖面 w(r) @ p = {p_mpa:g} MPa  "
                      f"(FEM 环向平均实线 vs 1D 虚线)")
        ax2.set_xlabel("半径 r [mm]")
        ax2.set_ylabel("挠度 w [mm]")
        ax2.grid(True, alpha=0.35)
        ax2.plot(r_f * 1e3, wf_u * p_max * 1e3, "-", color="#333", lw=1.8)
        ax2.plot(r_1 * 1e3, w1 * 1e3, "--", color="#999", lw=1.2)
        handles2 = [Line2D([], [], color="#333", lw=1.8, label="w(r) FEM-2D"),
                    Line2D([], [], color="#999", ls="--", lw=1.2,
                           label="w(r) 1D v0")]
        for i, l in enumerate(layers):
            ax2.axvline(l.b_m * 1e3, color=colors[i], ls=":", lw=1.2)
            handles2.append(Line2D([], [], color=colors[i], ls=":",
                                   label=f"第{i + 1}片外缘 Ø{l.d_out_mm:g}"))
        ax2.axvline(fem.mesh["clamp_radius_mm"], color="r", ls="--", lw=1.0)
        handles2.append(Line2D([], [], color="r", ls="--", label="固支内缘"))
        ax2.legend(handles=handles2, fontsize=7, loc="upper left")
        self.fig.tight_layout()
        self.canvas.draw()

        self._report_2d(fem, core, layers, ps, tips_f, tips_1,
                        h_an_pmax, e_pa, nu, t_wall)

    # ---------------- 数值核验报告 ----------------
    def _report(self, core, layers, ps, tips, h_an_pmax, e_pa, nu):
        p_max = ps[-1]
        p_mpa = p_max / 1e6
        top = layers[0]
        h_top = tips[0, -1]                     # 堆叠顶片外缘挠度 @ p_max
        lines = [f"FEM 网格: {core.n_nodes} 节点, {core.n_nodes - 1} 单元, "
                 f"固支半径 {core.clamp_radius_m * 1e3:.3f} mm",
                 f"顶片单片解析 (md): φ = {analytic_phi(top.d_in_mm, top.d_out_mm, nu):.6g}, "
                 f"h = {h_an_pmax * 1e3:.5f} mm @ {p_mpa:g} MPa",
                 f"堆叠顶片 FEM: h = {h_top * 1e3:.5f} mm"
                 + (f"  (FEM/解析 = {h_top / h_an_pmax:.6f})"
                    if len(layers) == 1 else ""),
                 f"堆叠刚度提升(相对单片): {h_an_pmax / h_top:.3f} ×"]
        for i, (tip, l) in enumerate(zip(tips, layers)):
            ht = tip[-1] / (l.t_mm * 1e-3)
            mark = "  ← 超出 h<0.2t!" if ht > 0.2 else ""
            lines.append(f"  第{i + 1}片 Ø{l.d_out_mm:g}: "
                         f"h = {tip[-1] * 1e3:.5f} mm, h/t = {ht:.3f}{mark}")
        ok, ratio = core.small_deflection_ok(p_max)
        if not ok:
            lines.append(f"[警告] 最大 h/t = {ratio:.2f} > 0.2: "
                         f"超出线性小挠度适用区, 曲线仅供趋势参考")
        lines.append("单片 FEM/解析 验证见 shim_physics_core.py 自检"
                     "(相对误差 < 1e-5)")
        self._set_status("\n".join(lines))

    def _report_2d(self, fem, core, layers, ps, tips_f, tips_1,
                   h_an_pmax, e_pa, nu, t_wall):
        """2D FEM 模式状态栏: 网格统计/耗时/守恒/与 1D 对比/适用性警告."""
        p_max = ps[-1]
        p_mpa = p_max / 1e6
        top = layers[0]
        a0, b0 = top.a_m, top.b_m
        area = np.pi * (b0 ** 2 - a0 ** 2)
        rel_en = abs(fem.E_int - fem.E_ext) / fem.E_ext
        rel_f = abs(fem.area_face - area) / area
        lines = [
            f"[2D FEM Mindlin] 网格 {fem.mesh['n_cell_r']} 径向带 × "
            f"nθ={fem.mesh['n_theta']} → {fem.mesh['n_elem']} 单元, "
            f"{fem.n_nodes} 节点, 自由 DOF {int(fem.free.sum())}",
            f"组装 {fem.t_assemble * 1e3:.0f} ms + 求解 "
            f"{fem.t_solve * 1e3:.0f} ms (共 {t_wall * 1e3:.0f} ms; "
            f"单位压强解, h(p) 线性放大)",
            f"单片解析参照 (md): φ = "
            f"{analytic_phi(top.d_in_mm, top.d_out_mm, nu):.6g}, "
            f"h = {h_an_pmax * 1e3:.5f} mm @ {p_mpa:g} MPa",
            f"顶片外缘 @ {p_mpa:g} MPa: FEM-2D h = {tips_f[0, -1] * 1e3:.5f} "
            f"mm | 1D-v0 h = {tips_1[0, -1] * 1e3:.5f} mm | "
            f"相对差 {(tips_f[0, -1] - tips_1[0, -1]) / tips_1[0, -1]:+.2e} "
            f"(FEM 含 Mindlin 剪切, 略软)",
            f"守恒: 环向均匀性 {fem.ring_uniformity():.1e} | "
            f"能量 {rel_en:.1e} | 总压力 {rel_f:.1e}"]
        ratio_max = 0.0
        for i, l in enumerate(layers):
            ht = abs(tips_f[i, -1]) / (l.t_mm * 1e-3)
            ratio_max = max(ratio_max, ht)
            mark = "  ← 超出 h<0.2t!" if ht > 0.2 else ""
            rel1 = (tips_f[i, -1] - tips_1[i, -1]) / tips_1[i, -1]
            lines.append(f"  第{i + 1}片 Ø{l.d_out_mm:g}: "
                         f"h = {tips_f[i, -1] * 1e3:.5f} mm, "
                         f"h/t = {ht:.3f}, vs-1D {rel1:+.2e}{mark}")
        if ratio_max > 0.2:
            lines.append(f"[警告] 最大 h/t = {ratio_max:.2f} > 0.2: "
                         f"超出线性小挠度适用区, 曲线仅供趋势参考")
        lines.append("注: Mindlin 剪切修正 (~1.2(t/(b−a))²) 使 FEM-2D 略软于 "
                     "1D/Kirchhoff; 大挠度非线性见后续迭代")
        self._set_status("\n".join(lines))

    def _set_status(self, text: str):
        self.txt_status.configure(state=tk.NORMAL)
        self.txt_status.delete("1.0", tk.END)
        self.txt_status.insert("1.0", text)
        self.txt_status.configure(state=tk.DISABLED)


def main():
    root = tk.Tk()
    ShimStackCheckerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
