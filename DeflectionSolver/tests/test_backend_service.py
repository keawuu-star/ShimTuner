# -*- coding: utf-8 -*-
"""后端数值服务回归测试: 与解析解/1D 结果对照, 以及输入校验。"""

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

from backend import service  # noqa: E402
from shim_physics_core import analytic_edge_deflection  # noqa: E402

LAYERS = [
    {"d_in_mm": 6, "d_out_mm": 10, "t_mm": 0.1},
    {"d_in_mm": 6, "d_out_mm": 9, "t_mm": 0.1},
    {"d_in_mm": 6, "d_out_mm": 8, "t_mm": 0.1},
]
SINGLE = [{"d_in_mm": 6, "d_out_mm": 10, "t_mm": 0.1}]


def request(layers=None, solver="1d", fem=None, **extra):
    body = {
        "layers": layers or LAYERS,
        "material": {"E_GPa": 200.0, "nu": 0.30},
        "p_max_MPa": 1.0,
        "solver": solver,
    }
    if fem:
        body["fem"] = fem
    body.update(extra)
    return body


class SingleShimAnalyticTest(unittest.TestCase):
    def test_1d_single_shim_matches_analytic(self):
        out = service.analyze(request(layers=SINGLE))
        h_fem = out["report"]["layers"][0]["h_mm"] * 1e-3
        h_ref = analytic_edge_deflection(6.0, 10.0, 0.1, 1e6, 200e9, 0.30)
        self.assertLess(abs(h_fem - h_ref) / h_ref, 1e-3)

    def test_2d_close_to_1d(self):
        one_d = service.analyze(request())
        two_d = service.analyze(request(solver="2d",
                                        fem={"n_radial": 60, "n_theta": 80}))
        h1 = one_d["report"]["layers"][0]["h_mm"]
        h2 = two_d["report"]["layers"][0]["h_mm"]
        self.assertLess(abs(h2 - h1) / h1, 0.015)
        self.assertEqual(len(two_d["charts"]["hp"]["series"]),
                         1 + 2 * len(LAYERS))

    def test_result_is_json_serializable(self):
        out = service.analyze(request())
        json.dumps(out, allow_nan=False)
        self.assertEqual(len(out["charts"]["hp"]["series"]), 1 + len(LAYERS))
        self.assertEqual(len(out["charts"]["profile"]["series"]), 1)
        self.assertTrue(all(len(s["y"]) == len(s["x"])
                            for s in out["charts"]["hp"]["series"]))


class LayeredSolverTest(unittest.TestCase):
    def test_layered_linear_runs_and_reports_contact(self):
        layers = LAYERS[:2]
        out = service.analyze(request(
            layers=layers, solver="layered",
            fem={"n_radial": 12, "n_theta": 12}))
        self.assertIn("接触对", "\n".join(out["report"]["lines"]))
        self.assertEqual(len(out["report"]["layers"]), 2)
        self.assertEqual(len(out["charts"]["profile"]["series"]), 3)
        self.assertGreater(out["report"]["layers"][0]["h_mm"], 0.0)


class ValidationTest(unittest.TestCase):
    def test_outer_must_exceed_inner(self):
        with self.assertRaisesRegex(service.AnalysisError, "外径"):
            service.analyze(request(layers=[{"d_in_mm": 10, "d_out_mm": 6,
                                             "t_mm": 0.1}]))

    def test_clamp_must_not_be_inside_hole(self):
        with self.assertRaisesRegex(service.AnalysisError, "无支撑悬环"):
            service.analyze(request(fem={"clamp_radius_mm": 2.0}))

    def test_pressure_must_be_positive(self):
        with self.assertRaisesRegex(service.AnalysisError, "p_max"):
            service.analyze(request(p_max_MPa=0.0))

    def test_unknown_solver_rejected(self):
        with self.assertRaisesRegex(service.AnalysisError, "未知求解器"):
            service.analyze(request(solver="magic"))


class ConfigTest(unittest.TestCase):
    def test_config_contract(self):
        cfg = service.get_config()
        self.assertEqual({s["id"] for s in cfg["solvers"]},
                         {"1d", "2d", "nl", "layered"})
        self.assertTrue(cfg["presets"])
        self.assertEqual(len(cfg["palette"]), 12)
        self.assertTrue(math.isfinite(cfg["material_defaults"]["E_GPa"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
