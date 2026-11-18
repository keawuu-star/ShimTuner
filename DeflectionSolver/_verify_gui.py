# -*- coding: utf-8 -*-
"""GUI 冒烟测试: 三种模式各跑一次, 打印状态栏, 2 秒后自动关闭."""
import sys
import time
import tkinter as tk

sys.path.insert(0, r"D:\Desktop\shim stacker\ShimTuner")
from deflection_checker import ShimStackCheckerApp

root = tk.Tk()
root.withdraw()          # 不显示窗口, 只验证构建与计算路径
t0 = time.perf_counter()
app = ShimStackCheckerApp(root)
print(f"[构建] {time.perf_counter()-t0:.1f}s")


def status():
    return app.txt_status.get("1.0", tk.END).strip()


def run(mode, nr=None, nt=None, pmax=None):
    app.var_solver.set(mode)
    if nr is not None:
        app.var_fem_nr.set(str(nr))
    if nt is not None:
        app.var_fem_nt.set(str(nt))
    if pmax is not None:
        app.var_pmax.set(str(pmax))
    t = time.perf_counter()
    app.recalc()
    txt = status()
    print(f"\n===== 模式 {mode} ({time.perf_counter()-t:.1f}s) =====")
    print(txt[:900])


run("1D")
run("2D", nr=30, nt=24)
run("NL", nr=16, nt=16)
root.destroy()
print("\nGUI 冒烟测试完成")
