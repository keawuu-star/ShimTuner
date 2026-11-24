# -*- coding: utf-8 -*-
"""项目文件格式回归：示例项目可读，含多方案与预留字段."""

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "FlowSolver" / "projects" / "示例-前减震.json"


class ProjectFileTest(unittest.TestCase):
    def test_example_project_schema(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "shimtuner-shock-project/1")
        proj = data["project"]
        self.assertTrue(proj["name"])
        self.assertIn(proj["position"], {"front", "rear", "other"})
        self.assertIn(proj["valve_type"], {"mid", "base", "both"})
        self.assertIn("reserved", proj)
        self.assertTrue(proj["reserved"])

    def test_example_schemes_and_active(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        schemes = data["schemes"]
        self.assertGreaterEqual(len(schemes["shim_stacks"]), 2)
        self.assertGreaterEqual(len(schemes["restrictors"]), 2)
        active = data["active_schemes"]
        stack_ids = {s["id"] for s in schemes["shim_stacks"]}
        restr_ids = {r["id"] for r in schemes["restrictors"]}
        self.assertIn(active["shim_stack"], stack_ids)
        self.assertIn(active["restrictor"], restr_ids)
        for s in schemes["shim_stacks"]:
            self.assertTrue(s["layers"])
            for layer in s["layers"]:
                self.assertLess(layer["d_in_mm"], layer["d_out_mm"])
        for r in schemes["restrictors"]:
            piston = r["piston"]
            self.assertGreaterEqual(piston["n_ports"], 1)
            self.assertGreater(piston["port_width_mm"], 0)

    def test_case_input_is_reusable(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        case = data["case"]
        self.assertEqual(case["schema"], "shimtuner-flow-case/1")
        self.assertIn(case["mode"], {"single", "iterate", "curve", "rod"})
        self.assertIn("input", case)
        self.assertIn("piston", case["input"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
