# -*- coding: utf-8 -*-
"""
FlowSolver 图形界面 — 定常流道-压力场 (v1)
==========================================
运行: python -X utf8 flow_checker.py

左侧：活塞几何 / 油液 / 系数 / 挠度场 / 工况
右侧：上图 h(r) 与 p(r)（双 y 轴）；下图 F(v) 曲线
状态栏：阻尼力、压降、截流面积、流速、射流滞止压、Re、损失分解、告警

数值全部来自 channel_flow 的同一次求解，界面不做二次换算。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import traceback

import numpy as np
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channel_flow as cf  # noqa: E402

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, \
    NavigationToolbar2Tk
from matplotlib import font_manager, rcParams

# ---- 深色主题（与 DeflectionSolver 桌面版一致）----
BG = "#060a12"
PANEL = "#0d1626"
FG = "#cbd5e1"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"
ACCENT2 = "#22d3ee"
WARN = "#fbbf24"
DANGER = "#f87171"
ENTRY_BG = "#111c2e"

for _name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
    if _name in {f.name for f in font_manager.fontManager.ttflist}:
        rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
        break
rcParams["axes.unicode_minus"] = False


class FlowCheckerApp:
    """定常流道-压力场工作台."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("FlowSolver v1 — 定常流道-压力场")
        root.geometry("1340x840")
        root.configure(bg=BG)
        self._after_id = None
        self._w_field = None            # 导入的 (r_mm, w_mm)
        self.last = None

        self._build_style()
        self._build_layout()
        self._schedule(0)

    # ---------------- 样式 ----------------
    def _build_style(self):
        self.f_label = ("Microsoft YaHei", 9)
        self.f_small = ("Microsoft YaHei", 8)
        self.f_title = tkfont.Font(family="Microsoft YaHei", size=10,
                                   weight="bold")
        self.f_mono = ("Consolas", 9)

    # ---------------- 布局 ----------------
    def _build_layout(self):
        left = tk.Frame(self.root, bg=BG, width=380)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        left.pack_propagate(False)

        right = tk.Frame(self.root, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10),
                   pady=10)

        self._build_inputs(left)

        self.fig = Figure(figsize=(8.6, 6.6), dpi=100, facecolor=PANEL)
        self.ax_hp = self.fig.add_subplot(2, 1, 1)
        self.ax_hp2 = self.ax_hp.twinx()
        self.ax_fv = self.fig.add_subplot(2, 1, 2)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        NavigationToolbar2Tk(self.canvas, right).update()

        self.txt_status = tk.Text(right, height=10, bg=PANEL, fg=FG,
                                  font=self.f_mono, relief=tk.FLAT,
                                  wrap=tk.WORD)
        self.txt_status.pack(fill=tk.X, pady=(6, 0))

    def _panel(self, parent, title):
        box = tk.LabelFrame(parent, text=title, bg=BG, fg=ACCENT,
                            font=self.f_title, bd=1, relief=tk.GROOVE,
                            labelanchor="nw")
        box.pack(fill=tk.X, pady=(0, 6))
        return box

    def _row(self, parent, label, var, width=9):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill=tk.X, padx=4, pady=1)
        tk.Label(row, text=label, bg=BG, fg=FG, font=self.f_label,
                 width=14, anchor="w").pack(side=tk.LEFT)
        ent = tk.Entry(row, textvariable=var, width=width, bg=ENTRY_BG,
                       fg="#e2e8f0", insertbackground=FG, relief=tk.FLAT,
                       font=self.f_label)
        ent.pack(side=tk.LEFT, padx=(0, 4))
        var.trace_add("write", lambda *_: self._schedule())
        return ent

    def _build_inputs(self, parent):
        # ---- 活塞 ----
        p = self._panel(parent, "活塞数据")
        self.v = {}
        defaults = dict(d_piston="46", d_rod="18", n_ports="6",
                        port_width="8", port_radial="4", port_radius="16",
                        clamp_diameter="18", deck="0.10",
                        shim_outer_diameter="42",
                        bypass_d="0", bypass_n="0", bypass_Cd="0.62",
                        rho="850", cSt="15", p_back="0", p_vapor="0",
                        T_C="40", KV40="0", KV100="0", VI="0",
                        C_d="0.70", C_f="0.30",
                        lift="0.15", power="2.0",
                        v_max="0.5", n_points="21", v_point="0.25")
        for key, val in defaults.items():
            self.v[key] = tk.StringVar(value=val)
        for key, lab in (("d_piston", "活塞直径 [mm]"), ("d_rod", "杆直径 [mm]"),
                         ("n_ports", "窗口数"),
                         ("port_width", "窗口周向宽度 [mm]"),
                         ("port_radial", "窗口径向宽度 [mm]"),
                         ("port_radius", "窗口中心半径 [mm]"),
                         ("clamp_diameter", "夹持直径 [mm]"),
                         ("deck", "初始间隙 [mm]"),
                         ("shim_outer_diameter", "最大阀片直径 [mm]"),
                         ("bypass_d", "常通孔 Ø [mm]"),
                         ("bypass_n", "常通孔个数")):
            self._row(p, lab, self.v[key])

        # ---- 流体 ----
        f = self._panel(parent, "油液")
        for key, lab in (("rho", "密度 [kg/m³]"), ("cSt", "黏度(手动) [cSt]"),
                         ("p_back", "背压 [MPa]"), ("p_vapor", "蒸气压 [kPa]"),
                         ("T_C", "工作温度 [°C]"), ("KV40", "KV40 [cSt]"),
                         ("KV100", "KV100 [cSt]"), ("VI", "VI")):
            self._row(f, lab, self.v[key])

        # ---- 系数 ----
        c = self._panel(parent, "系数")
        for key, lab in (("C_d", "截流 C_d"), ("C_f", "射流 C_f"),
                         ("bypass_Cd", "常通孔 C_d")):
            self._row(c, lab, self.v[key])

        # ---- 挠度场 ----
        g = self._panel(parent, "挠度场 w(r)")
        self.var_mode = tk.StringVar(value="power_law")
        r1 = tk.Frame(g, bg=BG)
        r1.pack(fill=tk.X, padx=4)
        tk.Radiobutton(r1, text="幂律", value="power_law", bg=BG, fg=FG,
                       selectcolor=PANEL, activebackground=BG,
                       variable=self.var_mode, command=self._schedule).pack(
            side=tk.LEFT)
        tk.Radiobutton(r1, text="导入数组", value="array", bg=BG, fg=FG,
                       selectcolor=PANEL, activebackground=BG,
                       variable=self.var_mode, command=self._schedule).pack(
            side=tk.LEFT)
        self._row(g, "外缘抬升 [mm]", self.v["lift"])
        self._row(g, "幂次", self.v["power"])
        tk.Button(g, text="导入 CSV/JSON (r,w)", bg=PANEL, fg=ACCENT2,
                  relief=tk.FLAT, command=self._import_field).pack(
            fill=tk.X, padx=4, pady=(3, 2))
        self.lbl_field = tk.Label(g, text="（未导入数组）", bg=BG, fg=MUTED,
                                  font=self.f_small)
        self.lbl_field.pack(anchor="w", padx=6)

        # ---- 工况 ----
        o = self._panel(parent, "工况")
        for key, lab in (("v_max", "v_max [m/s]"), ("n_points", "曲线点数"),
                         ("v_point", "当前点 v [m/s]")):
            self._row(o, lab, self.v[key])

        btns = tk.Frame(parent, bg=BG)
        btns.pack(fill=tk.X, pady=4)
        tk.Button(btns, text="重算", bg="#12324a", fg=ACCENT, relief=tk.FLAT,
                  font=self.f_title, command=self.recalc).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        tk.Button(btns, text="默认", bg=PANEL, fg=MUTED, relief=tk.FLAT,
                  command=self._reset).pack(side=tk.LEFT, padx=2)

    # ---------------- 输入处理 ----------------
    def _reset(self):
        defaults = dict(d_piston="46", d_rod="18", n_ports="6",
                        port_width="8", port_radial="4", port_radius="16",
                        clamp_diameter="18", deck="0.10",
                        shim_outer_diameter="42", rho="850", cSt="15",
                        p_back="0", p_vapor="0", C_d="0.70", C_f="0.30",
                        lift="0.15", power="2.0", v_max="0.5",
                        n_points="21", v_point="0.25")
        for k, val in defaults.items():
            self.v[k].set(val)
        self.var_mode.set("power_law")
        self._w_field = None
        self.lbl_field.config(text="（未导入数组）")

    def _import_field(self):
        path = filedialog.askopenfilename(
            title="导入挠度场", filetypes=[("CSV/JSON", "*.csv *.json"),
                                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                data = json.load(open(path, "r", encoding="utf-8"))
                r = np.asarray(data["r_mm"], float)
                w = np.asarray(data["w_mm"], float)
            else:
                r, w = [], []
                with open(path, "r", encoding="utf-8-sig") as fh:
                    for row in csv.reader(fh):
                        if not row or row[0].strip().startswith("#"):
                            continue
                        r.append(float(row[0]))
                        w.append(float(row[1]))
                r, w = np.asarray(r, float), np.asarray(w, float)
            if r.size < 2 or r.size != w.size:
                raise ValueError("需要至少 2 行且 r/w 等长")
            self._w_field = (r, w)
            self.var_mode.set("array")
            self.lbl_field.config(
                text=f"已导入 {r.size} 点: r {r.min():.2f}–{r.max():.2f} mm",
                fg=ACCENT2)
            self.recalc()
        except Exception as exc:                      # noqa: BLE001
            self.lbl_field.config(text=f"导入失败: {exc}", fg=DANGER)

    def _num(self, key):
        return float(self.v[key].get())

    def _build_case(self):
        """读取界面 → (PistonSpec, FluidSpec, gap)."""
        piston = cf.PistonSpec(
            d_piston_mm=self._num("d_piston"), d_rod_mm=self._num("d_rod"),
            n_ports=int(float(self.v["n_ports"].get())),
            port_width_mm=self._num("port_width"),
            port_radial_mm=self._num("port_radial"),
            port_radius_mm=self._num("port_radius"),
            seat_radius_mm=self._num("clamp_diameter") / 2.0,
            deck_height_mm=self._num("deck"),
            shim_outer_mm=self._num("shim_outer_diameter") / 2.0,
            bypass_d_mm=self._num("bypass_d"),
            bypass_n=int(float(self.v["bypass_n"].get())),
            bypass_Cd=self._num("bypass_Cd"))
        piston.validate()
        fluid = cf.FluidSpec(rho=self._num("rho"), cSt=self._num("cSt"),
                             T_C=self._num("T_C"),
                             KV40=self._num("KV40"),
                             KV100=self._num("KV100"),
                             VI=self._num("VI"),
                             p_back_MPa=self._num("p_back"),
                             p_vapor_kPa=self._num("p_vapor"))
        fluid.validate()
        if self.var_mode.get() == "array" and self._w_field is not None:
            r, w = self._w_field
            gap = cf.ArrayGap(r * 1e-3, w * 1e-3, h_deck_m=piston.h_deck_m())
        else:
            gap = cf.PowerLawGap(lift_m=self._num("lift") * 1e-3,
                                 r_seat_m=piston.r_seat_m(),
                                 r_outer_m=piston.r_outer_m(),
                                 power=self._num("power"),
                                 h_deck_m=piston.h_deck_m())
        return piston, fluid, gap

    # ---------------- 计算 ----------------
    def _schedule(self, delay=300):
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:                          # noqa: BLE001
                pass
        self._after_id = self.root.after(delay, self.recalc)

    def recalc(self):
        self._after_id = None
        try:
            piston, fluid, gap = self._build_case()
            cd = self._num("C_d")
            cf_ = self._num("C_f")
            v_max = self._num("v_max")
            npts = max(2, int(float(self.v["n_points"].get())))
            v_point = min(self._num("v_point"), v_max)
            A_eff = piston.area_effective_m2()
            t0 = time.perf_counter()
            res = cf.solve_split(v_point * A_eff, piston, fluid, gap,
                                 C_d=cd, C_f=cf_)
            vs = np.linspace(0.0, v_max, npts)
            cur = cf.solve_curve(vs, piston, fluid, gap, split=True,
                                 C_d=cd, C_f=cf_)
            dt = (time.perf_counter() - t0) * 1e3
            self.last = res
            self._plot(res, cur, v_point, piston)
            self._report(res, piston, dt, v_point)
        except Exception as exc:                       # noqa: BLE001
            self._status(f"输入/求解错误: {exc}\n\n"
                         f"{traceback.format_exc(limit=2)}")

    # ---------------- 绘图 ----------------
    def _style_axis(self, ax, xlabel, ylabel, color):
        ax.set_facecolor(PANEL)
        ax.set_xlabel(xlabel, color=FG, fontsize=9)
        ax.set_ylabel(ylabel, color=color, fontsize=9)
        ax.tick_params(colors=MUTED, labelsize=8)
        for s in ax.spines.values():
            s.set_color("#1e293b")
        ax.grid(True, color="#1e293b", alpha=0.6, lw=0.6)

    def _plot(self, res, cur, v_point, piston):
        ax = self.ax_hp
        ax2 = self.ax_hp2
        ax.clear()
        ax2.clear()
        r_mm = res.r_m * 1e3
        ax.plot(r_mm, res.h_m * 1e3, color=ACCENT2, lw=2.0, label="间隙 h(r)")
        ax2.plot(r_mm, res.p_pa / 1e6, color=ACCENT, lw=2.0, ls="--",
                 label="压力 p(r)")
        ax2.scatter([piston.port_radius_mm], [res.p_jet / 1e6], s=26,
                    color=WARN, zorder=5,
                    label=f"射流滞止 p_jet={res.p_jet/1e6:.3f} MPa")
        for x, lab, col in ((piston.seat_radius_mm, "夹持", MUTED),
                            (piston.port_radius_mm, "窗口", WARN),
                            (piston.shim_outer_mm, "最大阀片", ACCENT2)):
            ax.axvline(x, color=col, ls=":", lw=1.0)
            ax.text(x, ax.get_ylim()[1] if ax.get_ylim()[1] else 0, lab,
                    color=col, fontsize=7, rotation=90, va="top", ha="right")
        self._style_axis(ax, "半径 r [mm]", "间隙 h [mm]", ACCENT2)
        self._style_axis(ax2, "", "压力 p [MPa]", ACCENT)
        ax2.set_ylabel("压力 p [MPa]", color=ACCENT, fontsize=9)
        ax.set_title("阀片-活塞面间隙与流道压力场", color=FG, fontsize=10)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7,
                  facecolor=PANEL, edgecolor="#1e293b", labelcolor=FG)

        fx = self.ax_fv
        fx.clear()
        fx.plot(cur["v_mps"], cur["F_N"], color=ACCENT, lw=2.0, label="F(v)")
        fx.plot(cur["v_mps"], cur["F_jet_N"], color=WARN, lw=1.2, ls="--",
                label="射流分力 F_jet")
        fx.scatter([v_point], [res.F_damper], s=30, color=ACCENT2, zorder=5,
                   label=f"当前点 {res.F_damper:.1f} N")
        self._style_axis(fx, "活塞速度 v [m/s]", "阻尼力 F [N]", ACCENT)
        fx.set_title("阻尼力曲线 F(v)", color=FG, fontsize=10)
        fx.legend(loc="upper left", fontsize=7, facecolor=PANEL,
                  edgecolor="#1e293b", labelcolor=FG)
        self.fig.tight_layout()
        self.canvas.draw()

    # ---------------- 报告 ----------------
    def _report(self, res, piston, dt_ms, v_point):
        total = max(res.dp_total, 1e-12)
        lines = [
            f"【当前点】v = {v_point:.3f} m/s   Q_total = "
            f"{res.Q_total_m3s*1e6:.2f} mL/s   Q_main = {res.Q_m3s*1e6:.2f} mL/s",
            f"  阻尼力 F = {res.F_damper:.1f} N    总压降 Δp = "
            f"{res.dp_total/1e6:.4f} MPa    上游 p_up = {res.p_up/1e6:.4f} MPa",
            f"  截流面积 A_th = {res.A_th*1e6:.2f} mm²  (h_port = "
            f"{res.h_port*1e6:.1f} µm)   截流流速 v_port = {res.v_port:.1f} m/s",
            f"  射流滞止压 p_jet = {res.p_jet/1e6:.4f} MPa   射流力 F_jet = "
            f"{res.F_jet:.2f} N   阀片受载 F_shim = {res.F_shim:.1f} N",
            f"  损失分解: 截流 {res.dp_curtain/total*100:.1f}% | 黏性 "
            f"{res.dp_viscous/total*100:.1f}% | 惯性 {res.dp_inertia/total*100:.1f}%"
            f"   Re = {res.Re:.0f}",
            f"  A_eff = {piston.area_effective_m2()*1e6:.1f} mm² | "
            f"网格 {res.r_m.size} 点 | 计时 {dt_ms:.0f} ms",
        ]
        if res.A_bypass_m2 > 0:
            lines.insert(3, f"  常通孔: n={piston.bypass_n}×Ø"
                            f"{piston.bypass_d_mm:g} mm, A_b = "
                            f"{res.A_bypass_m2*1e6:.3f} mm², Q_bypass = "
                            f"{res.Q_bypass_m3s*1e6:.2f} mL/s "
                            f"({res.Q_bypass_m3s/max(res.Q_total_m3s,1e-18)*100:.1f}%)")
        lines += [f"[警告] {w}" for w in res.warnings]
        lines.append("注: v1 轴对称/不可压/定常/层流；挠度场为输入，闭环迭代见 "
                     "couple_demo.py。")
        self._status("\n".join(lines))

    def _status(self, text: str):
        self.txt_status.configure(state=tk.NORMAL)
        self.txt_status.delete("1.0", tk.END)
        self.txt_status.insert("1.0", text)
        self.txt_status.configure(state=tk.DISABLED)


def main() -> int:
    root = tk.Tk()
    FlowCheckerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
