# -*- coding: utf-8 -*-
"""请求结构体: 只做类型/范围校验, 几何语义校验在 service.py."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LayerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    d_in_mm: float = Field(gt=0, le=1000)
    d_out_mm: float = Field(gt=0, le=1000)
    t_mm: float = Field(gt=0, le=50)


class MaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    E_GPa: float = Field(default=200.0, gt=0, le=5000)
    nu: float = Field(default=0.30, ge=0.0, lt=0.5)


class FemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_radial: int = Field(default=60, ge=4, le=200)
    n_theta: int = Field(default=0, ge=0, le=256)
    clamp_radius_mm: float | None = Field(default=None, gt=0, le=1000)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: list[LayerInput] = Field(min_length=1, max_length=12)
    material: MaterialInput = Field(default_factory=MaterialInput)
    p_max_MPa: float = Field(default=1.0, gt=0, le=200)
    solver: Literal["1d", "2d", "nl", "layered"] = "1d"
    fem: FemInput = Field(default_factory=FemInput)
    layered_nonlinear: bool = False
    curve_points: int = Field(default=101, ge=11, le=401)
