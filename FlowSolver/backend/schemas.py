# -*- coding: utf-8 -*-
"""请求结构体：只做类型/范围校验，几何语义校验在 service.py。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PistonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    d_piston_mm: float = Field(default=46.0, gt=0, le=500)
    d_rod_mm: float = Field(default=18.0, gt=0, le=500)
    n_ports: int = Field(default=6, ge=1, le=200)
    # 窗口：周向宽度（弧长）+ 径向宽度 + 中心半径
    port_width_mm: float = Field(default=8.0, gt=0, le=200)
    port_radial_mm: float = Field(default=4.0, gt=0, le=200)
    port_radius_mm: float = Field(default=16.0, gt=0, le=500)
    # 夹持直径（推荐）与旧字段 seat_radius_mm（兼容，半径）
    clamp_diameter_mm: float | None = Field(default=None, gt=0, le=1000)
    seat_radius_mm: float = Field(default=9.0, gt=0, le=500)
    deck_height_mm: float = Field(default=0.10, gt=0, le=20)
    # 最大阀片直径（推荐）与旧字段 shim_outer_mm（兼容，半径）
    shim_outer_diameter_mm: float | None = Field(default=None, gt=0, le=1000)
    shim_outer_mm: float = Field(default=21.0, gt=0, le=500)
    # 定常旁通孔（常通孔）：几何固定，不随阀片挠度/迭代变化
    bypass_d_mm: float = Field(default=0.0, ge=0, le=50)
    bypass_n: int = Field(default=0, ge=0, le=200)
    bypass_Cd: float = Field(default=0.62, gt=0, le=1)


class FluidInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rho: float = Field(default=850.0, gt=0, le=5000)
    cSt: float = Field(default=15.0, gt=0, le=10000)
    p_back_MPa: float = Field(default=0.0, ge=-5, le=100)
    p_vapor_kPa: float = Field(default=0.0, ge=0, le=1000)
    # 工作温度 + 黏度曲线（ASTM D341）：KV40>0 时用 KV40/KV100 外推 T 下黏度
    T_C: float = Field(default=40.0, ge=-60, le=300)
    KV40: float = Field(default=0.0, ge=0, le=10000)
    KV100: float = Field(default=0.0, ge=0, le=10000)
    VI: float = Field(default=0.0, ge=-400, le=400)


class GapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["power_law", "array"] = "power_law"
    lift_mm: float = Field(default=0.15, ge=0, le=20)
    power: float = Field(default=2.0, gt=0, le=10)
    r_mm: list[float] | None = None
    w_mm: list[float] | None = None


class CoefficientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    C_d: float = Field(default=0.70, gt=0, le=5)
    C_f: float = Field(default=0.30, ge=0, le=5)


class OperatingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v_max_mps: float = Field(default=0.50, gt=0, le=20)
    n_points: int = Field(default=21, ge=2, le=200)
    v_point_mps: float | None = Field(default=None, ge=0, le=20)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    piston: PistonInput = PistonInput()
    fluid: FluidInput = FluidInput()
    gap: GapInput = GapInput()
    coefficients: CoefficientInput = CoefficientInput()
    operating: OperatingInput = OperatingInput()
    grid_points: int = Field(default=400, ge=50, le=4000)


class LayerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    d_in_mm: float = Field(gt=0, le=1000)
    d_out_mm: float = Field(gt=0, le=1000)
    t_mm: float = Field(gt=0, le=50)


class IterateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v_mps: float = Field(default=0.15, gt=0, le=20)
    alpha: float = Field(default=0.5, gt=0.05, le=0.95)
    tol_um: float = Field(default=2.0, gt=0, le=100)
    max_iter: int = Field(default=25, ge=1, le=100)
    relax_step_um: float = Field(default=150.0, gt=1, le=2000)
    nl: bool = False
    n_radial: int = Field(default=16, ge=4, le=60)
    n_theta: int = Field(default=12, ge=8, le=64)
    # 窗口分布载荷（v2）：基础压力 × 周向平滑调制 W(θ) + N 个圆形射流冲击斑
    # 开启后 nθ 自动抬到 ≥48（分辨射流斑）
    window_load: bool = False
    # 周向调制对比度 c（W=1+c·cos(Nθ)）；缺省 = 1−窗口覆盖率
    window_contrast: float | None = Field(default=None, ge=0, le=0.95)
    # 兼容旧字段（已废弃：旧"阶跃掩膜"已移除，sector_load 等价于 window_load）
    sector_load: bool = False
    sector_scale: bool = True
    # 扇区降维：N 重周期载荷只解 1/N 扇区 + 周期边界（默认开）
    sector_reduction: bool = True
    # vK Newton 内层相对容差（默认 1e-6；1e-4 可提速约 25%）
    nl_tol: float = Field(default=1e-6, gt=0, le=1e-1)


class IterateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: list[LayerInput]
    piston: PistonInput = PistonInput()
    fluid: FluidInput = FluidInput()
    coefficients: CoefficientInput = CoefficientInput()
    iterate: IterateParams = IterateParams()


class CurveParams(IterateParams):
    """Q–F 曲线参数：速度区间 + 点数（其余同迭代参数）。"""

    v_min_mps: float = Field(default=0.02, gt=0, le=20)
    v_max_mps: float = Field(default=0.50, gt=0, le=20)
    n_points: int = Field(default=6, ge=2, le=30)


class RodCurveRequest(BaseModel):
    """活塞杆速度 → 阻尼力（压缩/回弹两条分支，可给不同阀片堆叠）."""

    model_config = ConfigDict(extra="forbid")

    layers: list[LayerInput]
    layers_rebound: list[LayerInput] | None = None
    piston: PistonInput = PistonInput()
    fluid: FluidInput = FluidInput()
    coefficients: CoefficientInput = CoefficientInput()
    curve: CurveParams = CurveParams()
    # 阀类型：mid=活塞阀（Q=(A_p−A_rod)v） / base=底阀（Q=A_rod·v）
    valve_type: Literal["mid", "base"] = "mid"


class FvCurveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: list[LayerInput]
    piston: PistonInput = PistonInput()
    fluid: FluidInput = FluidInput()
    coefficients: CoefficientInput = CoefficientInput()
    curve: CurveParams = CurveParams()

