# Shim Stacker · 阀片阻尼器数值仿真

阀片式减震器的两段式数值求解：**压力场 → 挠度场**（结构）与
**挠度场 + 流道 → 压力场**（流体），最终由定点迭代器耦合。
理论推导见 [`ShimTuner/ShimTunerPhysics.md`](ShimTuner/ShimTunerPhysics.md)。

```
Q ─▶ 流道几何 h=w+h_deck ─▶ 压力场 P(r) ─▶ 阀片挠度 w'(r) ─▶ 收敛? ─▶ F
        ▲                                    │
        └──────────── 修正 w_{k+1} ◀─────────┘
```

## 目录

| 目录 | 内容 | 入口 |
|---|---|---|
| [`DeflectionSolver/`](DeflectionSolver/) | 压力场→挠度场：1D 环元 / 2D Mindlin / 2D vK / 2D 分层接触，Tk GUI + FastAPI + Electron 桌面版 | `deflection_checker.py`、`run-desktop.bat` |
| [`FlowSolver/`](FlowSolver/) | 挠度场+流道→压力场：截流面积 + 射流效应 + 径向间隙流；Tk GUI + FastAPI + **Electron 桌面版（含耦合迭代模式）** | `flow_checker.py`、`run-desktop.bat` |
| [`ShimTuner/`](ShimTuner/) | 物理文档、`.agents` 网格技能、遗留脚本 | `ShimTunerPhysics.md` |
| [`Dyno_data_and_what_it_tells_us_about_shim_tuning/`](Dyno_data_and_what_it_tells_us_about_shim_tuning/) | ThumperTalk 帖转存的技术参考（shim factor / tsf / crossover / 预载） | `ShimTuning_Dyno数据技术参考.md` |

## 快速开始

```powershell
# 结构端（挠度）
cd DeflectionSolver
python -X utf8 shim_layered.py        # 分层接触自检
python -X utf8 deflection_checker.py  # Tk GUI
run-desktop.bat                       # Electron 桌面版（Deflection Studio）

# 流道端（压力）
cd ..\FlowSolver
python -X utf8 channel_flow.py        # 物理自检
python -X utf8 flow_checker.py        # Tk GUI
python -X utf8 couple_demo.py         # Q→w→P→w′ 定点迭代演示
run-desktop.bat                       # Electron 桌面版（单点 / 耦合迭代 / Q–F / v–F 杆速）

# 数值后端（可选）
python -m backend --port 8761

# 总入口：杆速 → 阻尼力（调用两个 solver）
cd ..
python -X utf8 ShimTuner\fv_curve.py --direction both --csv fv_curve.csv
python -X utf8 ShimTuner\fv_curve.py --x-axis q --points 6     # 改看 Q–F
python -X utf8 ShimTuner\fv_curve.py --bypass-d 1.0 --bypass-n 2 # 常通孔旁通
```

依赖：`numpy` / `scipy`；GUI 另需 `matplotlib`；后端需 `fastapi` / `uvicorn`。
仓库根 `.venv` 已安装全部依赖（`python -m pip install -r DeflectionSolver/requirements-ui.txt`）。

## 数值核验

```powershell
# 结构端
cd DeflectionSolver
python -X utf8 shim_physics_core.py     # 1D vs 解析 (~1e-7)
python -X utf8 shim_fem_plate.py        # 2D vs 1D/解析
python -X utf8 shim_nl_plate.py         # vK 切线/网格收敛
python -X utf8 shim_layered.py          # 分层接触验收门
python -m unittest discover -s tests -v # 后端回归 9 项

# 流道端
cd ..\FlowSolver
python -X utf8 channel_flow.py          # 8 组解析/极限/收敛断言
python -m unittest discover -s tests -v # 后端回归 15 项
```

## 版本状态

* 结构端：四种求解器可用，分层接触（μ=0）已验收；摩擦按项目决策不做。
* 流道端：v1 定常模型（截流面积 + 射流 + 黏性/惯性 + **定常旁通孔**），轴对称、不可压、层流；
  空化/湍流/choked flow 未建。
* 桌面端：两套 Electron 工作台均可运行——DeflectionSolver（挠度）与
  FlowSolver（流道-压力场：单点 / 耦合迭代 / Q–F 曲线 / **v–F 杆速**）。
* Q–F 曲线：`ShimTuner/fv_curve.py`（总入口）或后台 `/api/fv-curve` / 桌面端「Q–F 曲线」模式，
  逐点闭环迭代（热启动）；默认 6 点 F ≈ 48→423 N，6/6 收敛，约 30 s。
* **v–F 杆速**：流量由杆侵入油缸的体积速度决定（`Q_valve=(A_p−A_rod)·v`，
  `Q_rod=A_rod·v`）；后台 `/api/rod-curve` / 桌面端「v–F 杆速」/ CLI `--direction both`
  可同时给压缩、回弹两条分支（各自阀片堆叠）。默认压缩 6 点 F ≈ 48→423 N；
  示例双分支（压缩 3 片 / 回弹 2 片薄堆叠）v≤0.25 m/s：压缩 280 N、回弹 184 N。
* 窗口分布载荷（可选，v2）：`iterate.window_load`（后端/桌面端复选框/CLI
  `--window-load`）用平滑周向调制 W(θ)=1+c·cos(Nθ) + N 个高斯射流冲击斑叠加，
  窗间压力与射流均保留（无阶跃、不删除）；nθ 自动 ≥48，并自动做**扇区降维**
  （1/N 扇区 + 旋转周期约束，精确等价）：vK + 窗口分布 183.6→3.0 s/点（≈60×）。
* 端到端演示：`FlowSolver/backend /api/iterate` 调用 DeflectionSolver 的分层模型，
  默认 3 片堆叠、v=0.15 m/s 迭代收敛，F ≈ 177 N；Electron 冒烟
  `npm run smoke` / `smoke:iterate` / `smoke:curve` / `smoke:rod` 均通过。
