# -*- coding: utf-8 -*-
"""FlowSolver 后端回归：JSON 契约、极限、单调性、数组挠度场、输入校验。"""

from __future__ import annotations

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import channel_flow as cf  # noqa: E402
import coupling as cp  # noqa: E402
from backend import service  # noqa: E402
from backend.schemas import (AnalyzeRequest, FvCurveRequest, IterateRequest,  # noqa: E402
                            CurveParams, LayerInput, RodCurveRequest)


def analyze(**kw) -> dict:
    req = AnalyzeRequest(**kw)
    return service.analyze(req)


class ContractTest(unittest.TestCase):
    def test_default_request_contract(self):
        out = analyze()
        for key in ("solver", "solver_label", "headline", "elapsed_ms",
                    "charts", "report", "meta"):
            self.assertIn(key, out)
        self.assertEqual(set(out["charts"]), {"gap", "pressure", "force"})
        for chart in out["charts"].values():
            self.assertIn("series", chart)
            for s in chart["series"]:
                self.assertEqual(len(s["x"]), len(s["y"]))
        self.assertGreater(out["meta"]["F_damper"] if "F_damper" in out["meta"] else 1, 0)
        self.assertTrue(out["report"]["kpis"])
        json.dumps(out)  # 必须可 JSON 序列化

    def test_zero_speed_gives_zero_force(self):
        out = analyze(operating=dict(v_max_mps=0.5, n_points=5, v_point_mps=0.0))
        self.assertAlmostEqual(out["meta"]["dp_curtain_Pa"], 0.0, places=9)
        self.assertLessEqual(abs(out["meta"]["F_jet_N"]), 1e-12)
        self.assertEqual(out["charts"]["force"]["series"][0]["y"][0], 0.0)

    def test_force_monotonic_in_speed(self):
        f = []
        for v in (0.05, 0.15, 0.3):
            out = analyze(operating=dict(v_max_mps=0.5, n_points=5,
                                         v_point_mps=v))
            f.append(out["meta"]["dp_curtain_Pa"])
        self.assertLess(f[0], f[1])
        self.assertLess(f[1], f[2])

    def test_array_gap_matches_power_law(self):
        # 用 21 点采样幂律场，转成数组输入，结果应与幂律模式一致
        import numpy as np
        piston = dict(d_piston_mm=46, d_rod_mm=18, n_ports=6,
                      port_width_mm=8, port_radius_mm=16, seat_radius_mm=9,
                      deck_height_mm=0.10, shim_outer_mm=21)
        r_mm = np.linspace(9, 21, 21)
        x = (r_mm - 9) / (21 - 9)
        w_mm = (0.15 * x ** 2).tolist()
        base = dict(piston=piston, operating=dict(v_max_mps=0.3, n_points=5,
                                                 v_point_mps=0.2))
        a = analyze(**base)
        b = analyze(**base, gap=dict(mode="array", r_mm=r_mm.tolist(),
                                     w_mm=w_mm))
        rel = abs(a["meta"]["F_damper"] if "F_damper" in a["meta"]
                  else a["charts"]["force"]["series"][0]["y"][0]
                  - b["charts"]["force"]["series"][0]["y"][0])
        fa = a["charts"]["force"]["series"][0]["y"][-1]
        fb = b["charts"]["force"]["series"][0]["y"][-1]
        self.assertLess(abs(fa - fb) / fa, 0.02)


class ValidationTest(unittest.TestCase):
    def test_bad_geometry_rejected(self):
        bad = dict(d_piston_mm=46, d_rod_mm=18, n_ports=6, port_width_mm=8,
                   port_radius_mm=16, seat_radius_mm=17, deck_height_mm=0.1,
                   shim_outer_mm=21)
        with self.assertRaises(service.ValidationError):
            analyze(piston=bad)

    def test_array_length_mismatch(self):
        with self.assertRaises(service.ValidationError):
            analyze(gap=dict(mode="array", r_mm=[9, 12, 15], w_mm=[0.0, 0.1]))

    def test_negative_speed_rejected_by_schema(self):
        with self.assertRaises(Exception):
            AnalyzeRequest(operating=dict(v_max_mps=-1))


class IterateTest(unittest.TestCase):
    """Q→w→P→w′ 定点迭代（调用 DeflectionSolver，较慢 ~8 s）。"""

    def test_iterate_converges_and_reports(self):
        req = IterateRequest(layers=[
            LayerInput(d_in_mm=18, d_out_mm=42, t_mm=0.25),
            LayerInput(d_in_mm=18, d_out_mm=36, t_mm=0.20),
            LayerInput(d_in_mm=18, d_out_mm=30, t_mm=0.20),
        ])
        out = service.iterate(req)
        meta = out["meta"]
        self.assertTrue(meta["converged"])
        self.assertLessEqual(meta["iterations"], 25)
        self.assertGreater(meta["F_damper"], 0.0)
        for key in ("convergence", "pressure", "deflection"):
            self.assertIn(key, out["charts"])
        json.dumps(out)

    def test_iterate_rejects_bad_stack(self):
        # 阀片内径的半径大于活塞落座半径 → 应给出中文校验错误
        req = IterateRequest(layers=[LayerInput(d_in_mm=40, d_out_mm=42,
                                                t_mm=0.25)])
        with self.assertRaises(service.ValidationError):
            service.iterate(req)


class CurveTest(unittest.TestCase):
    """闭环 Q–F 曲线（3 点，粗网格；约 5 s）。"""

    def test_fv_curve_points_converge_and_monotone(self):
        req = FvCurveRequest(
            layers=[
                LayerInput(d_in_mm=18, d_out_mm=42, t_mm=0.25),
                LayerInput(d_in_mm=18, d_out_mm=36, t_mm=0.20),
                LayerInput(d_in_mm=18, d_out_mm=30, t_mm=0.20),
            ],
            curve=CurveParams(v_min_mps=0.05, v_max_mps=0.25, n_points=3,
                              max_iter=20, n_radial=12, n_theta=8),
        )
        out = service.fv_curve(req)
        self.assertEqual(out["solver"], "fv-curve")
        self.assertEqual(out["meta"]["n_points"], 3)
        self.assertIn("curve", out["charts"])
        self.assertIn("convergence", out["charts"])
        pts = out["meta"]["points"]
        qs = [p["Q_m3s"] for p in pts]
        fs = [p["F_N"] for p in pts]
        self.assertTrue(all(b > a for a, b in zip(qs, qs[1:])))
        self.assertTrue(all(b > a for a, b in zip(fs, fs[1:])))
        self.assertGreaterEqual(out["meta"]["n_converged"], 2)
        json.dumps(out)


class RodCurveTest(unittest.TestCase):
    """杆速 → 阻尼力：运动学与压缩/回弹双分支。"""

    def test_rod_kinematics_areas(self):
        piston = cf.demo_piston()
        kin = cf.rod_flows(0.25, piston)
        self.assertAlmostEqual(kin["A_p_m2"],
                               0.25 * 3.141592653589793 * 0.046 ** 2, 12)
        self.assertAlmostEqual(kin["A_rod_m2"],
                               0.25 * 3.141592653589793 * 0.018 ** 2, 12)
        self.assertAlmostEqual(kin["A_eff_m2"],
                               kin["A_p_m2"] - kin["A_rod_m2"], 15)
        self.assertAlmostEqual(kin["Q_valve_m3s"], kin["A_eff_m2"] * 0.25, 15)
        self.assertAlmostEqual(kin["Q_rod_m3s"], kin["A_rod_m2"] * 0.25, 15)

    def test_rod_curve_two_branches(self):
        req = RodCurveRequest(
            layers=[
                LayerInput(d_in_mm=18, d_out_mm=42, t_mm=0.25),
                LayerInput(d_in_mm=18, d_out_mm=36, t_mm=0.20),
                LayerInput(d_in_mm=18, d_out_mm=30, t_mm=0.20),
            ],
            layers_rebound=[
                LayerInput(d_in_mm=18, d_out_mm=42, t_mm=0.20),
                LayerInput(d_in_mm=18, d_out_mm=36, t_mm=0.15),
            ],
            curve=CurveParams(v_min_mps=0.05, v_max_mps=0.25, n_points=2,
                              max_iter=20, n_radial=12, n_theta=8),
        )
        out = service.rod_curve(req)
        self.assertEqual(out["solver"], "rod-curve")
        self.assertIn("force", out["charts"])
        # 杆速↔流量为线性换算，不再输出 flow 图
        self.assertNotIn("flow", out["charts"])
        branches = out["meta"]["branches"]
        self.assertEqual(set(branches), {"compression", "rebound"})
        comp = branches["compression"]["points"]
        reb = branches["rebound"]["points"]
        self.assertEqual(len(comp), 2)
        self.assertEqual(len(reb), 2)
        # 每个点的流量由杆速决定
        for p in comp:
            self.assertAlmostEqual(p["Q_valve_m3s"],
                                   cf.demo_piston().area_effective_m2()
                                   * p["v_mps"], 12)
        # 更薄的回弹堆叠 → 同速下阻尼力更小
        self.assertLess(reb[0]["F_N"], comp[0]["F_N"])
        self.assertGreaterEqual(out["meta"]["total_ok"], 3)
        json.dumps(out)


class BypassTest(unittest.TestCase):
    """定常旁通孔（常通孔）：固定几何、与主阀并联分流、降低阻尼力。"""

    def _gap(self):
        pis = cf.demo_piston()
        return cf.PowerLawGap(lift_m=0.15e-3, r_seat_m=pis.r_seat_m(),
                              r_outer_m=pis.r_outer_m(), power=2.0,
                              h_deck_m=pis.h_deck_m())

    def test_bypass_split_conserves_flow_and_reduces_force(self):
        pis = cf.demo_piston()
        Q = 0.25 * pis.area_effective_m2()
        gap = self._gap()
        r0 = cf.solve_split(Q, pis, cf.DEMO_FLUID, gap)
        p_b = cf.demo_piston(bypass_d_mm=1.0, bypass_n=2)
        r1 = cf.solve_split(Q, p_b, cf.DEMO_FLUID, gap)
        self.assertAlmostEqual(r0.Q_bypass_m3s, 0.0)
        self.assertGreater(r1.Q_bypass_m3s, 0.0)
        self.assertAlmostEqual(r1.Q_m3s + r1.Q_bypass_m3s, Q, delta=Q * 1e-8)
        self.assertLess(r1.F_damper, r0.F_damper)
        # 孔流量满足薄壁孔方程
        Ab = p_b.area_bypass_m2()
        qb = (p_b.bypass_Cd * Ab
              * (2.0 * r1.dp_total / cf.DEMO_FLUID.rho) ** 0.5)
        self.assertAlmostEqual(r1.Q_bypass_m3s, qb, delta=qb * 1e-6)

    def test_bypass_absent_matches_main_only(self):
        pis = cf.demo_piston()
        Q = 1e-4
        gap = self._gap()
        a = cf.solve_point(Q, pis, cf.DEMO_FLUID, gap)
        b = cf.solve_split(Q, pis, cf.DEMO_FLUID, gap)
        self.assertAlmostEqual(a.dp_total, b.dp_total, delta=1e-12)
        self.assertAlmostEqual(a.F_damper, b.F_damper, delta=1e-9)
        self.assertEqual(b.Q_total_m3s, Q)

    def test_analyze_reports_bypass_split(self):
        out = analyze(piston=dict(bypass_d_mm=1.0, bypass_n=2))
        self.assertIn("常通孔分流", {k["label"] for k in out["report"]["kpis"]})
        meta = out["meta"]
        self.assertGreater(meta["Q_bypass_m3s"], 0.0)
        self.assertGreater(meta["A_bypass_m2"], 0.0)
        self.assertAlmostEqual(meta["Q_total_m3s"],
                               meta["Q_m3s"] + meta["Q_bypass_m3s"],
                               delta=meta["Q_total_m3s"] * 1e-8)
        json.dumps(out)


class WindowLoadTest(unittest.TestCase):
    """窗口分布载荷（v2）：周向平滑调制 + 圆形射流斑叠加（无阶跃、不删除区域）."""

    def test_window_profile_smooth(self):
        piston = cf.demo_piston()
        prof, cov, contrast = cp.window_profile(piston)
        self.assertAlmostEqual(cov, 6 * 8.0 / (2 * math.pi * 16.0), places=6)
        self.assertAlmostEqual(contrast, 1.0 - cov, places=12)
        th = np.radians(np.linspace(0.0, 360.0, 721))
        w = prof(th)
        self.assertAlmostEqual(float(np.mean(w)), 1.0, places=2)
        self.assertGreater(float(np.min(w)), 0.0)          # 处处为正（不删除）
        self.assertGreater(float(w[0]), 1.0)               # 窗口中心最高
        self.assertGreater(float(w[90]), 0.0)              # 窗间仍有压力
        self.assertLess(float(np.max(np.abs(np.diff(w)))), 0.1)  # 无阶跃

    def test_window_pressure_distribution(self):
        import copy

        piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
        gap = cf.PowerLawGap(lift_m=0.15e-3, r_seat_m=piston.r_seat_m(),
                             r_outer_m=piston.r_outer_m(), power=2.0,
                             h_deck_m=piston.h_deck_m())
        res = cf.solve_split(0.25 * piston.area_effective_m2(), piston,
                             fluid, gap)
        spec = cp.IterationSpec(window_load=True)
        fn = cp._pressure_fn(res, piston, 0.0, spec)
        th = np.array([0.0, math.radians(30.0)])

        # 落座区：窗口中心 > 窗间，但窗间不为 0
        p_land = fn(np.array([10e-3, 10e-3]), th)
        self.assertGreater(abs(float(p_land[0])), abs(float(p_land[1])))
        self.assertGreater(abs(float(p_land[1])), 0.0)

        # 去掉射流后，窗间压力 = p_up · (1 + c·cos(Nθ))，与解析一致
        res_nojet = copy.copy(res)
        res_nojet.p_jet = 0.0
        fn2 = cp._pressure_fn(res_nojet, piston, 0.0, spec)
        p2 = fn2(np.array([10e-3, 10e-3]), th)
        c = 1.0 - (6 * 8.0 / (2 * math.pi * 16.0))
        expect_mid = abs(res.p_up) * (1.0 + c * math.cos(6 * math.radians(30.0)))
        self.assertAlmostEqual(abs(float(p2[1])), expect_mid,
                               delta=abs(res.p_up) * 1e-9)

        # 射流：r_port 处窗中心 > 窗间，但**窗间射流不为 0**（圆形斑叠加）
        p_rp = fn(np.array([piston.r_port_m()] * 2), th)
        self.assertGreater(abs(float(p_rp[0])), abs(float(p_rp[1])))
        self.assertGreater(abs(float(p_rp[1])), 0.0)

    def test_window_iterate_smoke(self):
        piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
        layers = [(18, 42, 0.25), (18, 36, 0.20), (18, 30, 0.20)]
        spec = cp.IterationSpec(n_radial=6, n_theta=12, max_iter=2,
                                tol_um=1000.0, window_load=True)
        stack = cp.build_stack(layers, piston, spec)
        # 扇区降维自动生效：等效全环 48 → 6 扇区 × 8
        self.assertEqual(spec.n_theta_eff(), 48)
        self.assertEqual(stack.sector, 6)
        self.assertEqual(stack.n_theta, 8)
        pt = cp.iterate_point(0.25, piston, fluid, stack, spec)
        self.assertGreaterEqual(pt["iterations"], 1)
        self.assertTrue(np.isfinite(pt["res"].F_damper))


class GeometryDiameterTest(unittest.TestCase):
    """界面口径：夹持直径 / 最大阀片直径 / 窗口径向宽度."""

    def test_piston_properties(self):
        p = cf.demo_piston()
        self.assertAlmostEqual(p.clamp_diameter_mm(), 18.0)
        self.assertAlmostEqual(p.shim_outer_diameter_mm(), 42.0)
        self.assertAlmostEqual(p.port_arc_deg(), math.degrees(8.0 / 16.0),
                               places=6)

    def test_service_converts_diameters(self):
        from backend.schemas import PistonInput
        spec = service._piston_from(PistonInput(clamp_diameter_mm=20.0,
                                                shim_outer_diameter_mm=44.0,
                                                port_radial_mm=6.0))
        self.assertAlmostEqual(spec.seat_radius_mm, 10.0)
        self.assertAlmostEqual(spec.shim_outer_mm, 22.0)
        self.assertAlmostEqual(spec.port_radial_mm, 6.0)
        # 旧半径字段兼容
        spec2 = service._piston_from(PistonInput(seat_radius_mm=9.0,
                                                 shim_outer_mm=21.0))
        self.assertAlmostEqual(spec2.seat_radius_mm, 9.0)
        self.assertAlmostEqual(spec2.shim_outer_mm, 21.0)

    def test_jet_spot_uses_radial_width(self):
        from dataclasses import replace

        piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
        gap = cf.PowerLawGap(lift_m=0.15e-3, r_seat_m=piston.r_seat_m(),
                             r_outer_m=piston.r_outer_m(), power=2.0,
                             h_deck_m=piston.h_deck_m())
        res = cf.solve_split(0.25 * piston.area_effective_m2(), piston,
                             fluid, gap)
        spec = cp.IterationSpec(window_load=True)
        r = np.array([piston.r_port_m() + 2e-3])
        th = np.array([0.0])
        wide = cp._pressure_fn(res, piston, 0.0, spec)(r, th)
        narrow = cp._pressure_fn(res, replace(piston, port_radial_mm=1.0),
                                 0.0, spec)(r, th)
        # 射流斑径向宽度越大，同一径向偏移处冲击压力越大
        self.assertGreater(abs(float(wide[0])), abs(float(narrow[0])))


class SectorReductionTest(unittest.TestCase):
    """扇区降维：1/N 周期扇区 + 旋转周期约束，结果与整环一致."""

    def test_linear_equivalence(self):
        from shim_layered import LayeredStack
        layers = [(18, 42, 0.25), (18, 36, 0.20), (18, 30, 0.20)]
        full = LayeredStack(layers, n_elem_base_radial=12, n_theta=48,
                            clamp_radius_mm=9)
        sec = LayeredStack(layers, n_elem_base_radial=12, n_theta=8,
                           clamp_radius_mm=9, sector=6)
        full.solve(1.0e6)
        sec.solve(1.0e6)
        wf = full.w_matrix(0)
        ws = sec.w_matrix(0)
        rep = np.tile(ws, (1, 6))[:, :48]
        scale = max(float(np.max(np.abs(wf))), 1e-30)
        self.assertLess(float(np.max(np.abs(wf - rep))) / scale, 1e-6)
        self.assertLess(sec.n_dof, full.n_dof)

    def test_coupling_window_sector_matches(self):
        piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
        layers = [(18, 42, 0.25), (18, 36, 0.20), (18, 30, 0.20)]
        outs = []
        for red in (False, True):
            spec = cp.IterationSpec(n_radial=6, n_theta=48, max_iter=3,
                                    window_load=True, sector_reduction=red)
            stack = cp.build_stack(layers, piston, spec)
            pt = cp.iterate_point(0.25, piston, fluid, stack, spec)
            outs.append((pt["res"].F_damper, pt["w"][-1], stack.sector))
        self.assertEqual(outs[1][2], 6)
        self.assertAlmostEqual(outs[0][0], outs[1][0], delta=1e-6)
        self.assertAlmostEqual(float(outs[0][1]), float(outs[1][1]), delta=1e-12)


class ViscosityTest(unittest.TestCase):
    """油液黏度曲线：ASTM D341 外推 + ASTM D2270 VI."""

    def test_walther_roundtrip(self):
        self.assertAlmostEqual(cf.kv_at_temperature(32.0, 5.4, 40.0), 32.0,
                               places=6)
        self.assertAlmostEqual(cf.kv_at_temperature(32.0, 5.4, 100.0), 5.4,
                               places=6)
        v80 = cf.kv_at_temperature(32.0, 5.4, 80.0)
        self.assertTrue(5.4 < v80 < 32.0)

    def test_viscosity_index(self):
        vi = cf.viscosity_index(32.0, 5.4)
        self.assertTrue(100.0 < vi < 250.0)
        # 100 VI 参考油：U=H 时 VI=100
        L, H = cf._vi_LH(10.0)
        self.assertAlmostEqual(cf.viscosity_index(H, 10.0), 100.0, places=6)

    def test_fluid_effective_cst(self):
        f = cf.FluidSpec(rho=850, cSt=15, T_C=80, KV40=32, KV100=5.4, VI=171)
        self.assertAlmostEqual(f.effective_cSt(),
                               cf.kv_at_temperature(32, 5.4, 80), places=9)
        self.assertEqual(cf.FluidSpec(rho=850, cSt=15).effective_cSt(), 15)

    def test_fluid_requires_kv100(self):
        with self.assertRaises(ValueError):
            cf.FluidSpec(rho=850, cSt=15, KV40=32).validate()
        cf.FluidSpec(rho=850, cSt=15, KV40=32, KV100=5.4).validate()

    def test_analyze_uses_temperature_viscosity(self):
        from backend.schemas import FluidInput
        req = AnalyzeRequest(fluid=FluidInput(rho=880, cSt=15, T_C=80,
                                              KV40=32, KV100=5.4, VI=171))
        res = service.analyze(req)
        line = [ln for ln in res["report"]["lines"] if "KV40" in ln]
        self.assertTrue(line)
        self.assertIn("80°C", line[0])
        self.assertIn("cSt", line[0])


class LayerOrderTest(unittest.TestCase):
    """层序约定：UI 第 1 行 = 贴活塞（迎油面），LayeredStack 内部反转并加载底层."""

    def test_stack_reversed_and_bottom_loaded(self):
        piston, fluid = cf.demo_piston(), cf.DEMO_FLUID
        user_layers = [(18, 42, 0.25), (18, 36, 0.20), (18, 30, 0.20)]
        spec = cp.IterationSpec(n_radial=8, n_theta=12)
        stack = cp.build_stack(user_layers, piston, spec)
        # 内部层序：最上层 → 贴活塞层
        self.assertEqual([l.d_out_mm for l in stack.layers], [30.0, 36.0, 42.0])
        self.assertEqual(stack.load_layer, 2)
        self.assertEqual(stack.layers[stack.load_layer].d_out_mm, 42.0)

    def test_pressure_on_piston_side_couples_all_layers(self):
        piston = cf.demo_piston()
        user_layers = [(18, 42, 0.25), (18, 36, 0.20), (18, 30, 0.20)]
        spec = cp.IterationSpec(n_radial=8, n_theta=12)
        stack = cp.build_stack(user_layers, piston, spec)
        stack.set_pressure(lambda r, th: -1.0e6)
        stack.solve(1.0)
        # 第 1 片（贴活塞）受油压，经接触向上逐层传力：三片都应变形
        for i in range(len(stack.layers)):
            wm = stack.w_matrix(i)
            self.assertGreater(float(np.abs(wm).max()), 1e-6,
                               f"第{i+1}层未参与变形（接触未传力）")
        self.assertGreater(stack.info["n_active"], 0)


class OuterCurtainTest(unittest.TestCase):
    """节流帘幕在窗口外缘：外缘弧长 × 外缘间隙，流道从外缘开始."""

    def test_curtain_geometry_and_area(self):
        p = cf.demo_piston()
        r_c = p.r_curtain_m()
        self.assertAlmostEqual(r_c, (p.port_radius_mm + 0.5 * p.port_radial_mm) * 1e-3)
        self.assertAlmostEqual(p.w_curtain_m(),
                               p.port_width_mm * 1e-3 * r_c / p.r_port_m())
        h0 = 0.2e-3
        gap = cf.PowerLawGap(lift_m=0.0, r_seat_m=p.r_seat_m(),
                             r_outer_m=p.r_outer_m(), power=2.0,
                             h_deck_m=h0)
        res = cf.solve_point(1e-4, p, cf.DEMO_FLUID, gap)
        self.assertAlmostEqual(res.A_th, p.n_ports * p.w_curtain_m() * h0)
        self.assertAlmostEqual(res.r_m[0], r_c, places=12)
        self.assertAlmostEqual(res.h_port, h0, places=12)


class ValveTypeTest(unittest.TestCase):
    """mid-valve / base-valve 流量与力面积切换."""

    def test_rod_flows_mid_vs_base(self):
        p = cf.demo_piston()
        mid = cf.rod_flows(0.25, p, "mid")
        base = cf.rod_flows(0.25, p, "base")
        A_eff = p.area_effective_m2()
        A_rod = p.area_rod_m2()
        self.assertAlmostEqual(mid["Q_valve_m3s"], A_eff * 0.25, places=15)
        self.assertAlmostEqual(mid["A_force_m2"], A_eff, places=15)
        self.assertAlmostEqual(base["Q_valve_m3s"], A_rod * 0.25, places=15)
        self.assertAlmostEqual(base["A_force_m2"], A_rod, places=15)
        self.assertEqual(mid["valve_type"], "mid")
        self.assertEqual(base["valve_type"], "base")

    def test_force_area_scales_damping_force(self):
        p = cf.demo_piston()
        gap = cf.PowerLawGap(lift_m=0.15e-3, r_seat_m=p.r_seat_m(),
                             r_outer_m=p.r_outer_m(), power=2.0,
                             h_deck_m=p.h_deck_m())
        Q = 0.25 * p.area_effective_m2()
        mid = cf.solve_point(Q, p, cf.DEMO_FLUID, gap)
        base = cf.solve_point(Q, p, cf.DEMO_FLUID, gap,
                              force_area_m2=p.area_rod_m2())
        self.assertAlmostEqual(mid.F_damper,
                               mid.dp_total * p.area_effective_m2())
        self.assertAlmostEqual(base.F_damper,
                               base.dp_total * p.area_rod_m2())
        self.assertLess(base.F_damper, mid.F_damper)

    def test_rod_curve_base_valve_api(self):
        layers = [LayerInput(d_in_mm=18, d_out_mm=42, t_mm=0.25)]
        req = RodCurveRequest(layers=layers, valve_type="base",
                              curve=CurveParams(v_min_mps=0.05,
                                                v_max_mps=0.1, n_points=2))
        out = service.rod_curve(req)
        self.assertEqual(out["meta"]["valve_type"], "base")
        pts = out["meta"]["branches"]["compression"]["points"]
        self.assertTrue(all(p["F_N"] > 0 for p in pts))
        A_rod = cf.demo_piston().area_rod_m2()
        for p in pts:
            self.assertAlmostEqual(p["Q_valve_m3s"], A_rod * p["v_mps"],
                                   places=15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
