/* Node 端测试：design.js 的纯几何/参数识别（无需 Electron）
 * 运行：node test-design.js  （或 npm run test:design）
 */
'use strict';

const assert = require('assert');
const V = require('./renderer/design.js');

function near(a, b, tol, what) {
  assert.ok(Math.abs(a - b) <= tol,
    `${what}: ${a} != ${b} (±${tol})`);
}

// 1) 示例草图：Ø46/Ø18 活塞、Ø18 夹持（与杆孔同径，走默认）、Ø42 阀片、
//    16 mm 处 8×4 mm 矩形窗口、N=6、h_deck=0.10
const demo = V.demoShapes();
const res = V.extractParams(demo, { n_ports: 6, h_deck: 0.10 });
near(res.params.d_piston_mm, 46, 1e-9, '活塞直径');
near(res.params.d_rod_mm, 18, 1e-9, '杆直径');
near(res.params.clamp_diameter_mm, 18, 1e-9, '夹持直径默认=杆径');
near(res.params.shim_outer_diameter_mm, 42, 1e-9, '最大阀片直径');
near(res.params.port_radius_mm, 16, 1e-9, '窗口中心半径');
near(res.params.port_width_mm, 32 * Math.asin(4 / 16), 1e-6, '窗口周向弧长');
near(res.params.port_radial_mm, 4, 1e-9, '窗口径向宽度');
assert.strictEqual(res.params.n_ports, 6);
near(res.params.deck_height_mm, 0.10, 1e-12, '初始间隙');
assert.deepStrictEqual(res.missing, [], '示例应无缺失参数');
assert.deepStrictEqual(res.issues, [], '示例应无校验问题');
assert.strictEqual(res.guide.filter((g) => g.ok).length, res.guide.length);

// 2) 缺省检测：只画活塞外圆 + 杆孔
const partial = [
  { id: 1, kind: 'circle', cx: 0, cy: 0, r: 23, role: 'piston_outer' },
  { id: 2, kind: 'circle', cx: 0, cy: 0, r: 9, role: 'rod' },
];
const r2 = V.extractParams(partial, { n_ports: 6, h_deck: 0.10 });
assert.deepStrictEqual(r2.missing, ['阀片最大外圆', '窗口（单个）']);
near(r2.params.clamp_diameter_mm, 18, 1e-9, '夹持默认=杆径');
assert.ok(r2.guide.find((g) => g.id === 'window' && !g.ok), '窗口应为待绘制');

// 3) 圆形窗口：r_port=10、孔半径 2 → 弧长=2·10·asin(0.2)、径向=4
const circ = [
  { id: 1, kind: 'circle', cx: 0, cy: 0, r: 23, role: 'piston_outer' },
  { id: 2, kind: 'circle', cx: 0, cy: 0, r: 9, role: 'rod' },
  { id: 3, kind: 'circle', cx: 0, cy: 0, r: 21, role: 'shim_outer' },
  { id: 4, kind: 'circle', cx: 10, cy: 0, r: 2, role: 'window' },
];
const r3 = V.extractParams(circ, { n_ports: 6, h_deck: 0.10 });
near(r3.params.port_radius_mm, 10, 1e-9, '圆窗中心半径');
near(r3.params.port_width_mm, 20 * Math.asin(2 / 10), 1e-9, '圆窗弧长');
near(r3.params.port_radial_mm, 4, 1e-9, '圆窗径向宽度');

// 4) 关系非法：杆径 > 活塞直径
const bad = [
  { id: 1, kind: 'circle', cx: 0, cy: 0, r: 5, role: 'piston_outer' },
  { id: 2, kind: 'circle', cx: 0, cy: 0, r: 9, role: 'rod' },
  { id: 3, kind: 'circle', cx: 0, cy: 0, r: 21, role: 'shim_outer' },
  { id: 4, kind: 'circle', cx: 16, cy: 0, r: 2, role: 'window' },
];
const r4 = V.extractParams(bad, { n_ports: 6, h_deck: 0.10 });
assert.ok(r4.issues.some((s) => s.includes('杆直径')), '应报告杆径非法');

// 5) 几何工具：矩形窗口的径向范围 / 角向跨度
near(V.windowGeometry(demo[3]).radial, 4, 1e-9, '矩形径向');
near(V.windowGeometry(demo[3]).r_port, 16, 1e-9, '矩形中心半径');

// 7) 从当前参数生成草图（角色/内径/窗口齐全）
const gen = V.shapesFromPiston({
  d_piston_mm: 46, d_rod_mm: 18, clamp_diameter_mm: 18,
  shim_outer_diameter_mm: 42, port_width_mm: 8, port_radial_mm: 4,
  port_radius_mm: 16, n_ports: 6, deck_height_mm: 0.1,
  bypass_d_mm: 0, bypass_n: 0,
});
const roles = gen.map((s) => s.role);
for (const r of ['piston_outer', 'rod', 'clamp', 'shim_outer', 'window']) {
  assert.ok(roles.includes(r), `生成草图应含角色 ${r}`);
}

// 8) 工程图构建（纯几何）
const Drawing = require('./renderer/valve-drawing.js');
const dwg = Drawing.build({
  d_piston_mm: 46, d_rod_mm: 18, clamp_diameter_mm: 18,
  shim_outer_diameter_mm: 42, port_width_mm: 8, port_radial_mm: 4,
  port_radius_mm: 16, n_ports: 6, deck_height_mm: 0.1,
  bypass_d_mm: 0, bypass_n: 0, t_piston_mm: 12,
  layers: [{ d_in_mm: 18, d_out_mm: 42, t_mm: 0.25 },
           { d_in_mm: 18, d_out_mm: 36, t_mm: 0.2 }],
});
assert.ok(dwg.cmds.length > 120, '工程图命令数应足够');
assert.ok(dwg.bbox.x1 > dwg.bbox.x0 && dwg.bbox.z1 > dwg.bbox.z0, 'bbox 有效');
const svg = Drawing.toSVG(dwg);
assert.ok(svg.startsWith('<svg'), 'SVG 头');
assert.ok(svg.includes('VALVE BODY'), '标题栏');
assert.ok(svg.includes('Ø46'), '直径标注');
assert.ok(svg.includes('阀片堆叠详图'), '应有阀片堆叠详图');
assert.ok(svg.includes('①=贴活塞'), '详图应标出第 1 片贴活塞');
const shimRects = dwg.cmds.filter((c) => c.s === 'shimline' && c.y > 0);
assert.strictEqual(shimRects.length, 2, '两层阀片都应绘制');
// 层序：第 1 片（42）贴活塞 → 画在下方且更宽；第 2 片（36）在其上
assert.ok(shimRects[0].y < shimRects[1].y, '第 1 片应在下方（贴活塞）');
assert.ok(shimRects[0].w > shimRects[1].w, '第 1 片（42）应比上一层（36）宽');

// 9) 圆形过孔与长条窗口都用同一套参数识别
const circWin = V.windowGeometry({ kind: 'circle', cx: 12, cy: 0, r: 2 });
near(circWin.r_port, 12, 1e-9, '圆窗中心半径');
assert.ok(circWin.width > 0 && circWin.radial === 4, '圆窗尺寸');

console.log('design.js tests: OK');
