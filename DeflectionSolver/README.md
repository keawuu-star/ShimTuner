# DeflectionSolver · 压力场 → 挠度场

> **状态：长期闲置项目（拟合曲线优化暂缓）。** 工具仍可运行；详见仓库根目录 [`STATUS.md`](../STATUS.md)。

阀片堆叠在给定压强下的挠度场求解器（ShimTuner 项目的第一半）。输入阀片几何、
材料与压强（均匀或指定分布），输出各层挠度场 `wᵢ(r,θ)`、外缘挠度 `h(p)`、
接触状态与数值核验报告。

理论见 [`../ShimTuner/ShimTunerPhysics.md`](../ShimTuner/ShimTunerPhysics.md)
（第一章解析理论 + 分层模型；层间摩擦按项目决策取 μ=0，不计）。

## 四种求解器

| 模式 | 模块 | 说明 |
|---|---|---|
| 1D 环元 v0 | `shim_physics_core.py` | 轴对称 Kirchhoff 环元，等效阶梯刚度 `D=ΣDᵢ` |
| 2D FEM Mindlin | `shim_fem_plate.py` | 极坐标 Q4 板，含横向剪切 |
| 2D 大挠度 vK | `shim_nl_plate.py` | von Kármán 膜应变 + 几何刚度，Newton 续载 |
| 2D 分层 μ=0 | `shim_layered.py` | 逐层中面 + 法向罚接触（可分离），线性 / vK |

共用：`shim_stack_mesh.py`（极坐标网格）、`shim_element.py`（Q4 单元核）。

## 三种前端

1. **Tk GUI**（零依赖，最快）：
   ```powershell
   python -X utf8 deflection_checker.py
   ```
2. **FastAPI 后端**（供任何前端调用）：
   ```powershell
   python -m backend --port 8760     # 文档: http://127.0.0.1:8760/docs
   ```
3. **Electron 桌面版**（Deflection Studio，见 `desktop/README.md`）：
   ```powershell
   run-desktop.bat
   ```

## 自检与测试

```powershell
# 各模块自带数值自检（无需 GUI）
python -X utf8 shim_physics_core.py     # 1D vs 解析, 7.5e-7
python -X utf8 shim_stack_mesh.py       # 网格不变量
python -X utf8 shim_fem_plate.py        # 2D vs 解析/1D
python -X utf8 shim_nl_plate.py         # 切线/网格收敛
python -X utf8 shim_layered.py          # 分层接触验收门

# 后端回归（需 fastapi/pydantic）
python -m unittest discover -s tests -v
```

依赖：`numpy` / `scipy`；后端另需 `fastapi` / `uvicorn`（`requirements-ui.txt`）。
