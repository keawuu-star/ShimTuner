# FlowSolver · 挠度场 + 流道 → 压力场

ShimTuner 项目的第二半：给定**阀片挠度场** `w(r)` 与**总流量 Q**（或活塞速度 v），
求阀片/活塞面之间流道的**稳态压力场** `p(r)`、截流压降与阻尼力 `F`。
服务于最终的 `Q → w → P → w′` 定点迭代器（见 [`../ShimTuner/ShimTunerPhysics.md`](../ShimTuner/ShimTunerPhysics.md)
「模拟逻辑设计」）。

## 物理模型（v1）

```
Q ──▶ 活塞窗口(射流) ──▶ 截流面积 A_th ──▶ 径向间隙流道 h(r) ──▶ 外缘背压 p_back
```

1. **截流面积**（阀片底面与活塞面之间的"帘幕"面积）
   ```
   A_th = N · w_port · h(r_port),      h(r) = h_deck + w(r)
   Δp_curtain = ρ Q² / (2 C_d² A_th²)
   ```
   这里只用**周向宽度** `w_port`（弧长）× 开度：窗口被等效成出口处的一条弧槽，
   径向宽度 `dr_port` 不进入 A_th（径向位置塌缩到 `r_port`）。因此单靠
   `N、w_port、r_port` **不能**区分圆形孔/长条窗——圆形孔需 `w_port=dr_port=d`，
   且真实圆孔的周向宽度随半径变化，模型不做这个细节（见下文"窗口几何"）。
2. **射流效应**（油束冲击阀片）
   ```
   v_port = Q / A_th,   p_jet = ½ ρ v_port²,   F_jet = C_f ρ Q² / A_th
   ```
   射流在阀片上的作用足迹为椭圆高斯：周向 σ=`w_port/2`、径向 σ=`dr_port/2`
   （两者相等即圆形孔），供挠度求解器作为**局部压力包**施加。
3. **径向间隙流**（黏性 + 惯性，次要项）
   ```
   u(r) = Q / (2π r h)
   dp/dr = −12 μ u / h² + ρ u² (1/r + h′/h),     p(r_outer) = p_back
   p_up = p(r_port) + Δp_curtain
   ```
4. **定常旁通孔**（常通孔，可选、固定几何）
   ```
   A_b = n · π/4 · d_b²                    （与阀片挠度无关，不参与迭代）
   Q_b = C_d,b · A_b · sqrt(2 Δp / ρ)      （薄壁孔）
   Q_total = Q_main + Q_b                  并联分流，Δp 由两者共同决定
   ```
   求解时对 `Q_main` 做一维求根，使 `Q_main + Q_b(Δp(Q_main)) = Q_total`；
   `Q_b` 不进入阀片载荷（旁通油不经过阀片底面），但通过改变 Δp 影响 `F_damper`。
5. 输出
   - 阻尼力 `F_damper = (p_up − p_back) · A_eff`，`A_eff = π/4 (d_p² − d_rod²)`；
   - 阀片受载 `F_shim = ∫(p−p_back)dA + F_jet`；
   - 损失分解（截流/黏性/惯性）、`Re`、截流流速、`Q_main/Q_bypass` 分流；
   - 告警（空化、湍流、间隙过小、常通孔分流占比过高）。

**v1 假设**：轴对称、不可压、定常、层流；无空化/湍流/choked-flow 模型；
挠度场是输入（尚未闭环）。这些都在报告的 `notes` 里明示。

## 文件

| 文件 | 内容 |
|---|---|
| `channel_flow.py` | 物理内核：`PistonSpec` / `FluidSpec` / `PowerLawGap` / `ArrayGap` / `solve_point` / `solve_split`（常通孔并联）/ `solve_curve`，自带数值自检 |
| `flow_checker.py` | Tk GUI（深色主题、双图、KPI/告警面板；含常通孔 Ø/个数/C_d） |
| `couple_demo.py` | Q→w→P→w′ 定点迭代命令行演示 |
| `backend/` | FastAPI 服务：`/api/health`、`/api/config`、`/api/analyze`、`/api/iterate` |
| `desktop/` | Electron 桌面版（Flow Studio：单点求解 + 耦合迭代两种模式） |
| `run-desktop.bat` | 一键启动桌面版（自动建 venv / 装依赖 / 拉起后端） |
| `tests/` | 后端数值回归 |

## 快速开始

```powershell
cd "D:\Desktop\shim stacker\FlowSolver"

# 1) 物理自检（无需 GUI；约 1 s）
python -X utf8 channel_flow.py

# 2) Tk GUI
python -X utf8 flow_checker.py

# 3) 数值后端（可选，任何前端都可调用）
python -m backend --port 8761
# http://127.0.0.1:8761/docs

# 4) Electron 桌面版（Flow Studio）
run-desktop.bat
```

依赖：`numpy`（必需）、`matplotlib`（GUI）、`fastapi`/`uvicorn`（后端）。
本仓库 `..\.venv` 已含全部依赖。

## 演示数据（默认活塞）

| 参数 | 值 |
|---|---|
| 活塞 / 杆径 | Ø46 / Ø18 mm |
| 窗口 | 6 个：周向宽度 8 mm（弧长）× 径向宽度 4 mm，中心半径 16 mm（单窗张角 28.6°） |
| 夹持直径 / 最大阀片直径 | 18 / 42 mm（内部按半径 9 / 21 mm 计算） |
| 初始间隙 h_deck | 0.10 mm |
| 油液 | ρ=850 kg/m³，ν=15 cSt |
| 挠度场 | 幂律：外缘抬升 0.15 mm，h = 0.1 + 0.15·x² |

默认工况（v = 0.25 m/s，即 Q ≈ 0.35 L/s）：
`F ≈ 3131 N`，`Δp ≈ 2.22 MPa`，截流占 92%，射流滞止压 ≈ 1.0 MPa。
低工况（v = 0.05 m/s）黏性项才与截流项同量级——这正是"截流面积主导"的判据。

## 定常旁通孔（常通孔）对阻尼力的影响

常通孔是活塞/阀片上加工的**固定截面油孔**（如 2×Ø1.0 mm），与"窗口 + 阀片间隙"
主阀路径**并联**。它的几何不受阀片挠度影响，因此闭环迭代过程中始终是同一个 `A_b`；
迭代只改变 `Q_main` 与 `Q_b` 的分配比例（`Q_main + Q_b = Q_total`，
`Q_b = C_d,b·A_b·√(2Δp/ρ)`）。

静态挠度场（外缘抬升 0.15 mm，v=0.25 m/s，A_th≈7.25 mm²）下：

| 常通孔 | A_b (mm²) | F (N) | Δp (MPa) | Q_b 占比 |
|---|---|---|---|---|
| 无 | 0 | 3131.4 | 2.225 | 0 % |
| 1×Ø1.0 | 0.785 | 2625.9 | 1.866 | 9.2 % |
| 2×Ø1.5 | 3.534 | 1564.8 | 1.112 | 31.8 % |
| 2×Ø2.0 | 6.283 | 1036.6 | 0.737 | 46.1 % |

低速段常通孔的相对影响更大（v=0.02 m/s：无孔 57.5 N → 2×Ø1.0 时 38.3 N，−33%）；
高速段主阀开度大、常通孔占比下降。在**闭环迭代**中（3 片堆叠，12×8 网格，2×Ø1.0）：

| v (m/s) | 无孔 F (N) | 有孔 F (N) | Q_bypass (mL/s) |
|---|---|---|---|
| 0.05 | 90.6 | 81.1 | 11.3 |
| 0.15 | 193.2 | 181.8 | 17.0 |
| 0.25 | 280.4 | 268.3 | 20.6 |

配置入口：`piston.bypass_d_mm / bypass_n / bypass_Cd`（请求体）、
Tk GUI 与桌面端「活塞数据」中的**常通孔 Ø / 个数 / C_d**、
命令行 `--bypass-d 1.0 --bypass-n 2 --bypass-cd 0.62`。
报告与 KPI 会给出 `Q_total / Q_main / Q_bypass` 与分流占比，分流 >50%/>90% 时告警。

## 计算性能

分层有限元是主要耗时项；已做以下加速（`DeflectionSolver/shim_layered.py`）：

1. **弹性刚度缓存**：`nl=False` 时弹性 K 与载荷/接触状态无关，只装配一次；
2. **接触罚项增量叠加**：每个接触活动集只需把罚项加到缓存的 K 上，不再重装全部单元；
3. **LU 分解缓存**：按接触活动集（packbits 键）缓存 `splu`，同一活动集跨载荷直接回代；
4. **活动集热启动**：用上一次求解的接触状态作为初值，外层迭代通常 1–2 次即收敛；
5. **流动求解向量化**：压力积分由 Python 循环改为 `cumsum`；
6. **vK 热启动**：非线性求解支持 `U0`，耦合迭代后续步用上一轮位移场起步；
7. **Newton 单次装配**：`_newton` 每次迭代只装配一次（同时得到 r 与 K），不再重复装配；
8. **vK 装配缓存**：弯曲/剪切单元刚度、索引与 Gauss 数据预计算，只重算膜力非线性项；
9. **接触活动集热启动（非线性）**：vK 求解沿用上次接触状态，减少外层循环；
10. **`nl_tol`**：vK Newton 内层容差可放宽到 1e-4（默认 1e-6），再提速约 25%；
11. **扇区降维（周期对称）**：载荷是 N 重周期（N 个均布窗口；轴对称载荷同样成立）时，
    只解 1/N 扇区 + 旋转周期约束 `u_s = R(2π/N)·u_m`，DOF 降为 1/N，结果与整环一致。

实测（3 片堆叠，v=0.25 m/s）：

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 线性分层 solve，12×48（n_dof 7200） | ≈6458 ms/次 | **≈2.5 ms/次** |
| 线性耦合单点 12×12 | ≈3.8 s | **≈0.11 s** |
| 窗口扇区耦合单点 12×48 | ≈66 s | **≈0.8 s** |
| Q–F 曲线 6 点（桌面冒烟） | ≈30 s | **≈1 s** |
| vK 单次 solve 12×12（n_steps=1） | ≈1293 ms | **≈350 ms** |
| vK 耦合单点 12×12 | ≈16.7 s | **≈3.3 s**（nl_tol=1e-4）/ ≈4.2 s（1e-6） |
| vK 耦合单点 16×12 | ≈25.9 s | **≈4.8 s**（nl_tol=1e-4） |
| 线性 + 窗口分布 12×48（扇区 6×8） | — | **≈0.05 s/点** |
| **vK + 窗口分布 12×48（扇区 6×8）** | **≈183.6 s/点** | **≈3.0 s/点（≈60×）** |

结果不变（F 分别 249.8 / 282.2 / 369.8 N 与优化前一致）。扇区降维后 **vK + 窗口分布
12×48 从 183.6 s/点降到 3.0 s/点（约 60×）**，F 与迭代次数完全相同（411.63 N / 13 次）。
vK 的剩余时间主要在 Newton 迭代与稀疏分解；下一步可做 Aitken/Anderson 外层加速、
向量化稀疏组装，或分阶段 vK（线性收敛后 1–2 次 vK 修正）。

## Tk GUI（零依赖，最快上手）

* 左侧：活塞几何 / 油液 / 系数（C_d、C_f）/ 挠度场（幂律或导入 CSV、JSON）/ 工况；
* 右侧：`h(r)` 与 `p(r)`（双 y 轴）、`F(v)` 曲线，标注落座/窗口/外缘半径；
* 报告：F、Δp、A_th、截流流速、射流滞止压、Re、损失分解、告警；
* 参数改动 300 ms 防抖自动重算。

## 桌面端（Electron · Flow Studio）

```powershell
cd "D:\Desktop\shim stacker\FlowSolver"
run-desktop.bat            # 或: cd desktop && npm start
```

* 后端由 Electron 主进程自动拉起（`python -m backend`，随机空闲端口，健康检查/重启）；
* 渲染层手写 Canvas 图表（与 DeflectionSolver 桌面版同一 `chart.js`），零前端依赖；
* **四种模式**：
  * 单点求解：活塞/油液/系数/幂律挠度场/工况 → `p(r)`、`F(v)`、KPI、损失分解；
  * 耦合迭代：阀片堆叠 + 迭代参数（α、容差、最大迭代、网格、vK 开关）→
    残差收敛曲线 + 收敛后的 `p(r)`、`w(r)`、F/KPI；
  * **Q–F 曲线**：给定 v 区间与点数，逐点闭环迭代（相邻点热启动）→
    F(Q) 曲线 + 逐点迭代次数/残差 + 汇总 KPI；
  * **v–F 杆速**：流量由杆侵入油缸的体积速度决定 →
    `F(v)` 曲线（可加回弹分支）；`Q_valve/Q_rod` 为线性换算，只列在报告表格中；
* 导入/导出工况 JSON（`shimtuner-flow-case/1`），复制报告；
* 冒烟测试：

```powershell
cd desktop
npm run smoke           # 单点模式端到端（Electron + 渲染 + 后端）
npm run smoke:iterate   # 耦合迭代模式端到端（约 10 s）
npm run smoke:curve     # Q–F 曲线模式端到端（约 30 s）
npm run smoke:rod       # v–F 杆速模式端到端（约 30 s）
```

## 耦合迭代接口 `/api/iterate`

请求：`layers[]` + `piston` + `fluid` + `coefficients` + `iterate{ v_mps, alpha,
tol_um, max_iter, relax_step_um, nl, n_radial, n_theta, window_load, window_contrast, sector_reduction, nl_tol }`。
后端调用 `DeflectionSolver` 的 `LayeredStack`（同级目录），用
`set_pressure(p(r,θ))` 施加流道压力与射流压力包，按 `w ← w + α·clip(w_P − w)`
迭代，返回：

```json
{
  "charts": {"convergence": {...}, "pressure": {...}, "deflection": {...}},
  "report": {"kpis": [...], "warnings": [...], "lines": [...]},
  "meta": {"converged": true, "iterations": 13, "residual_um": 1.17,
           "history": [...], "F_damper": 176.6}
}
```

默认演示（3 片 18/42、18/36、18/30，v=0.15 m/s）13 次迭代收敛，残差 1.2 µm，单点约 0.2 s。

### 窗口分布载荷（v2，可选）

`iterate.window_load=true` 时，阀片载荷由"环向均布"改为**连续分布函数**（不是阶跃掩膜）：

**① 周向平滑调制**（均值 1，处处为正，无阶跃）：

$$W(\theta)=1+c\cos(N\theta),\qquad c=\texttt{window\_contrast}\ (\text{默认 }1-\text{覆盖率})$$

$$p_{base}(r,\theta)=p_{back}+\bigl(p_{raw}(r)-p_{back}\bigr)\,\bigl[1+(W(\theta)-1)\,D(r)\bigr]$$

其中 `p_raw` 在落座区为 `p_up`、流道区为 `p(r)`；`D(r)` 为下游衰减（落座区 1，流道区
`exp[-(r−r_port)/(r_out−r_port)]`），表示油在间隙内逐渐沿周向铺开。

**② 射流冲击斑**（椭圆高斯，叠加，不删除任何区域）：

$$p_{jet}(r,\theta)=\sum_{k=1}^{N}p_{jet}\exp\!\left[-\frac12\left(
\left(\frac{r-r_{port}}{\sigma_r}\right)^2+
\left(\frac{r\,\Delta\theta_k}{\sigma_c}\right)^2\right)\right]$$

$$\sigma_c=\frac{w_{port}}{2}\ (\text{周向}),\qquad
\sigma_r=\frac{d_{r,port}}{2}\ (\text{径向})$$

周向宽度与径向宽度相等时（`w_port=dr_port`）就是**圆形**过孔；长条窗口得到的是椭圆斑。
这样窗口数据同时包含周向、径向两个尺寸，而不是只用一个宽度描述所有形状。

总载荷 `p_load = p_base + p_jet`。关键在于：**窗口中心与窗间的射流斑都保留**（窗间只是
叠加值较小，不是 0），落座区压力也处处为正（只是周向起伏）。

* 开启后 `n_theta` 自动抬到 ≥48（分辨射流斑），并自动做**扇区降维**（1/N 扇区 + 周期边界），
  等效分辨率不变、DOF 降为 1/N；`iterate.sector_reduction=false` 可关闭（用于对照验证）；
* 旧参数 `sector_load` 作为兼容别名（等价于 `window_load`）；旧的"阶跃掩膜"已删除；
* `window_contrast` 缺省 = `1−覆盖率`（默认几何 47.7% 覆盖 → c≈0.52）。

## 与迭代器的衔接（已实现）

`ShimTunerPhysics.md`「模拟逻辑设计」的闭环已跑通：

1. 给定 `w_k(r)` 与 Q → 本求解器给出 `p_k(r)`（含窗口处射流压力包）；
2. `p_k(r)` 作为阀片底面载荷交给 DeflectionSolver 的分层模型 → `w_P(r)`；
3. 残差 `max|w_P − w_k|`，不满足则 `w_{k+1} = w_k + α·clip(w_P − w_k)`；
4. 收敛后输出 `F`（`meta.F_damper`）。

接口：后端 `POST /api/iterate` / 桌面端「耦合迭代」模式 / 命令行 `couple_demo.py`。

## Q–F 曲线接口 `/api/fv-curve`

请求：`layers[]` + `piston` + `fluid` + `coefficients` +
`curve{ v_min_mps, v_max_mps, n_points, alpha, tol_um, max_iter, relax_step_um, nl, n_radial, n_theta }`。

逐速度点执行一次 `/api/iterate` 的闭环（相邻点用上一点 `w` 热启动），返回：

```json
{
  "charts": {"curve": {...}, "convergence": {...}},
  "report": {"kpis": [...], "warnings": [...], "lines": [...]},
  "meta": {"points": [{"Q_m3s":..., "v_mps":..., "F_N":..., "iterations":...,
                        "residual_um":..., "converged": true}],
           "n_converged": 6, "n_points": 6, "total_ms": 26900}
}
```

默认演示（3 片，v 0.02→0.5 m/s，6 点，16×12 网格）：**F ≈ 48 → 423 N，6/6 收敛**，
平均 8.7 次迭代，约 1 s（优化后；热启动显著减少后续点迭代次数）。

## 杆速–阻尼力接口 `/api/rod-curve`

流量由**活塞杆侵入油缸的体积速度**决定，而不是独立给定：

```
A_p = π/4·D²          活塞面积
A_rod = π/4·d_rod²    杆面积
Q_rod  = A_rod·v      杆排开的体积速度（单筒进蓄能腔 / 双筒经底阀回油）
Q_valve = (A_p − A_rod)·v   通过活塞阀的流量 ← 本求解器输入
```

请求：`layers[]`（压缩堆叠）+ `layers_rebound[]`（可选，回弹堆叠）+
`piston` + `fluid` + `coefficients` + `curve{...}`。
返回 `charts.force`（F(v)，压缩实线/回弹虚线）；`meta.branches` 含每个分支的逐点数据。
`Q_valve / Q_rod` 随 v 是纯线性换算，只出现在报告表格与 `meta` 中，不再单独成图。

默认演示（Ø46/Ø18 → A_p=1661.9、A_rod=254.5、A_eff=1407.4 mm²）：

| 行程 | v=0.05 | v=0.15 | v=0.25 m/s |
|---|---|---|---|
| 压缩（3 片） | 90.6 N | 193.2 N | 280.4 N |
| 回弹（2 片薄堆叠） | 60.9 N | 127.9 N | 184.4 N |

对应 Q_valve = 70.4 / 211.1 / 351.9 mL/s，Q_rod = 12.7 / 38.2 / 63.6 mL/s；
每点内部仍是 Q→w→P→w′ 闭环迭代（压缩 6 点约 1 s）。

## 常见问题

* **双击 `run-desktop.bat` 一闪而过 / 没反应**：多半是脚本带 UTF-8 BOM，cmd 会把首行
  读成 `锘緻echo off` 而立即报错。本仓库已修正为无 BOM；若再出现，用编辑器另存为
  「UTF-8 无 BOM」，或直接在 `desktop` 目录执行 `npm start`。
* **窗口空白 / 立刻退出**：确认 `ELECTRON_RUN_AS_NODE` 不是 `1`（批处理会自动清除；
  手动跑 `npm start` 前先执行 `set ELECTRON_RUN_AS_NODE=`）。
* **提示找不到 Python**：在仓库根 `.venv` 中执行
  `python -m pip install -r FlowSolver\requirements-ui.txt`，或设置 `SHIMTUNER_PYTHON`。
* **迭代模式慢**：默认 16×12 网格、约 8–9 s；可减小 `n_radial/n_theta` 或放宽容差。
