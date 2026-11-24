/* 阀体工程图（CAD）绘制模块
 * ============================================================
 * 纯几何：build() 只依赖参数，可在 Node 下测试；paint()/toSVG()/toPNG()
 * 负责把命令列表渲染到 Canvas / SVG / PNG。
 *
 * 坐标系：x = 半径 (mm)，y = 轴向 (mm)，y=0 为活塞底面；x=0 为回转轴。
 * 视图：半剖（只画 y≥0 的上半部分），配合垂直中心线 x=0。
 */
(function (global) {
  'use strict';

  const STYLE = {
    outline: { color: '#0f172a', width: 0.32 },
    cut: { color: '#0f172a', width: 0.22 },
    thin: { color: '#64748b', width: 0.16 },
    hatch: { color: '#94a3b8', width: 0.12 },
    shim: { color: '#94a3b8', width: 0.12 },
    shimline: { color: '#475569', width: 0.18 },
    dim: { color: '#334155', width: 0.14 },
    center: { color: '#94a3b8', width: 0.14, dash: [7, 3, 1.5, 3] },
    text: { color: '#0f172a' },
    title: { color: '#0f172a', width: 0.28 },
  };
  const FONT = 'Arial, "Microsoft YaHei", system-ui, sans-serif';

  function num(v, d) {
    const x = Number(v);
    return Number.isFinite(x) ? x : d;
  }

  function normalize(input) {
    const p = input || {};
    const dPiston = Math.max(2, num(p.d_piston_mm, 46));
    const dRod = Math.max(0.5, Math.min(dPiston - 1, num(p.d_rod_mm, 18)));
    const clampD = Math.max(dRod, Math.min(dPiston, num(p.clamp_diameter_mm,
      p.seat_radius_mm != null ? p.seat_radius_mm * 2 : dRod)));
    const shimD = Math.max(clampD, Math.min(dPiston + 40,
      num(p.shim_outer_diameter_mm,
        p.shim_outer_mm != null ? p.shim_outer_mm * 2 : dPiston)));
    const bodyR = Math.max(dPiston / 2, shimD / 2);
    const portR = Math.min(Math.max(clampD / 2 + 0.2, num(p.port_radius_mm, 16)),
      Math.max(clampD / 2 + 0.3, shimD / 2 - 0.2));
    const w = Math.max(0.3, num(p.port_width_mm, 8));
    const dr = Math.max(0.3, num(p.port_radial_mm, 4));
    const nPorts = Math.max(1, Math.round(num(p.n_ports, 6)));
    const deck = Math.max(0, num(p.deck_height_mm, 0.10));
    const tP = Math.max(2, num(p.t_piston_mm, 12));
    const bypassD = Math.max(0, num(p.bypass_d_mm, 0));
    const bypassN = Math.max(0, Math.round(num(p.bypass_n, 0)));
    const bypassR = Math.min(Math.max(clampD / 2 + 0.5,
      num(p.bypass_r_mm, (clampD / 2 + portR) / 2)),
      Math.max(clampD / 2 + 0.6, bodyR - 0.5));
    let layers = Array.isArray(input && input.layers) ? input.layers : [];
    layers = layers.map((l) => ({
      din: Math.max(0, num(l.d_in_mm != null ? l.d_in_mm : l.din, dRod)),
      dout: Math.max(0, num(l.d_out_mm != null ? l.d_out_mm : l.dout, shimD)),
      t: Math.max(0.01, num(l.t_mm != null ? l.t_mm : l.t, 0.2)),
    })).filter((l) => l.dout > l.din + 0.1 && l.t > 0);
    if (!layers.length) {
      layers = [{ din: Math.max(dRod, clampD), dout: shimD, t: 0.2 }];
    }
    return {
      dPiston, dRod, clampD, shimD, bodyR, portR, w, dr, nPorts, deck,
      tP, bypassD, bypassN, bypassR, layers,
      stackT: layers.reduce((s, l) => s + l.t, 0),
    };
  }

  // ---------------- 命令构造 ----------------
  function line(c, x1, y1, x2, y2, s) {
    c.push({ t: 'l', x1, y1, x2, y2, s: s || 'outline' });
  }
  function rect(c, x, y, w, h, s, fill) {
    c.push({ t: 'r', x, y, w, h, s: s || 'outline', fill: fill || null });
  }
  function text(c, x, y, str, size, s, align, bold, rot) {
    c.push({ t: 't', x, y, str, size: size || 2.4, s: s || 'text',
             align: align || 'center', bold: !!bold, rot: rot || 0 });
  }
  function poly(c, pts, fill, s) {
    c.push({ t: 'p', pts, fill: fill || null, s: s || 'outline' });
  }

  function hatch(c, x, y, w, h, s, spacing) {
    const sp = spacing || 1.6;
    const bump = (y - (x + w));
    const top = (y + h) - x;
    for (let b = Math.ceil(bump / sp) * sp; b <= top + 1e-9; b += sp) {
      const xs = Math.max(x, y - b);
      const xe = Math.min(x + w, y + h - b);
      if (xe - xs > 0.05) line(c, xs, xs + b, xe, xe + b, s || 'hatch');
    }
  }

  function arrow(c, x, y, dx, dy, size) {
    const s = size || 1.1;
    const px = -dy, py = dx;
    poly(c, [[x, y], [x - dx * s + px * s * 0.32, y - dy * s + py * s * 0.32],
             [x - dx * s - px * s * 0.32, y - dy * s - py * s * 0.32]], '#0f172a');
  }

  function extLine(c, x1, y1, x2, y2) { line(c, x1, y1, x2, y2, 'thin'); }

  function dimH(c, x1, x2, y, label) {
    line(c, x1, y, x2, y, 'dim');
    arrow(c, x1, y, 1, 0); arrow(c, x2, y, -1, 0);
    text(c, (x1 + x2) / 2, y + 1.5, label, 2.2, 'dim');
  }
  function dimV(c, y1, y2, x, label) {
    line(c, x, y1, x, y2, 'dim');
    arrow(c, x, y1, 0, 1); arrow(c, x, y2, 0, -1);
    // 文字沿尺寸线旋转 90°（自下而上），避免与相邻竖向尺寸文字重叠
    text(c, x - 0.8, (y1 + y2) / 2, label, 2.1, 'dim', 'center', false, 90);
  }
  function diaDim(c, R, y, label) {
    line(c, -R, y, R, y, 'dim');
    arrow(c, -R, y, 1, 0); arrow(c, R, y, -1, 0);
    text(c, 0, y + 1.6, label, 2.4, 'dim', 'center', true);
  }
  function leader(c, x1, y1, x2, y2, label, align) {
    line(c, x1, y1, x2, y2, 'thin');
    arrow(c, x1, y1, (x2 - x1) === 0 ? 1 : Math.sign(x2 - x1),
          (y2 - y1) === 0 ? 0 : Math.sign(y2 - y1), 0.9);
    text(c, x2, y2 + 1.2, label, 2.2, 'text', align || (x2 >= x1 ? 'start' : 'end'));
  }
  function labelBg(c, x, y, str, size, align) {
    const w = str.length * size * 0.62;
    const x0 = align === 'start' ? x : align === 'end' ? x - w : x - w / 2;
    rect(c, x0 - 0.6, y - size * 0.75, w + 1.2, size * 1.5, 'thin', '#ffffff');
    text(c, x, y, str, size, 'text', align || 'center');
  }

  // ---------------- 几何 ----------------
  function build(input) {
    const p = normalize(input);
    const c = [];
    const R = p.bodyR;
    const rodR = p.dRod / 2;
    const clampR = p.clampD / 2;
    const shimR = p.shimD / 2;
    const zShim0 = p.tP + p.deck;
    const zTop = zShim0 + p.stackT;
    const zRodBot = -10;

    // 活塞杆（穿透中心）
    rect(c, 0, zRodBot, rodR, zTop + 5 - zRodBot, 'outline');
    hatch(c, 0, zRodBot, rodR, zTop + 5 - zRodBot, 'hatch', 1.9);

    // 活塞体（杆孔 → 外圆），切除窗口/常通孔再用白底盖回
    rect(c, rodR, 0, R - rodR, p.tP, 'outline');
    hatch(c, rodR, 0, R - rodR, p.tP, 'hatch', 1.5);

    const zMid = p.tP / 2;
    // 窗口（半剖中假设剖切面过窗口中心）
    if (p.w > 0 && p.dr > 0) {
      const x0 = Math.max(rodR + 0.2, p.portR - p.dr / 2);
      const x1 = Math.min(R - 0.2, p.portR + p.dr / 2);
      if (x1 > x0) {
        rect(c, x0, 0, x1 - x0, p.tP, 'cut', '#ffffff');
        line(c, x0, 0, x0, p.tP, 'thin');
        line(c, x1, 0, x1, p.tP, 'thin');
      }
    }
    // 常通孔
    if (p.bypassD > 0 && p.bypassR > rodR + 0.2 && p.bypassR < R - 0.2) {
      const bx0 = p.bypassR - p.bypassD / 2;
      rect(c, bx0, 0, p.bypassD, p.tP, 'cut', '#ffffff');
      line(c, bx0, 0, bx0, p.tP, 'thin');
      line(c, bx0 + p.bypassD, 0, bx0 + p.bypassD, p.tP, 'thin');
    }

    // 初始间隙 + 阀片堆叠（最底下一片先画，state.layers[0] = 顶片）
    line(c, clampR, p.tP, Math.max(shimR, R), p.tP, 'thin');
    line(c, clampR, zShim0, Math.max(shimR, R), zShim0, 'thin');
    const layers = p.layers.slice().reverse();
    let z = zShim0;
    for (const l of layers) {
      const x0 = Math.max(0, l.din / 2);
      const x1 = Math.max(x0 + 0.2, l.dout / 2);
      rect(c, x0, z, x1 - x0, l.t, 'shimline');
      hatch(c, x0, z, x1 - x0, l.t, 'shim', Math.max(0.45, l.t * 0.9));
      z += l.t;
    }

    // 夹紧环（压住堆叠内缘）
    rect(c, 0, zTop, clampR, 2.5, 'outline');
    hatch(c, 0, zTop, clampR, 2.5, 'hatch', 1.1);
    line(c, clampR, zTop, clampR, zTop + 2.5, 'outline');

    // 中心线
    line(c, 0, zRodBot - 2, 0, zTop + 30, 'center');

    // ---- 尺寸 ----
    const dZ = zTop + 8;
    const dims = [
      { R: rodR, y: dZ, label: '杆 Ø' + p.dRod.toFixed(1), from: zTop + 4 },
      { R: clampR, y: dZ + 7, label: '夹持 Ø' + p.clampD.toFixed(1), from: zTop + 2.5 },
      { R: shimR, y: dZ + 14, label: 'Ø' + p.shimD.toFixed(1), from: zTop },
      { R: R, y: dZ + 21, label: 'Ø' + p.dPiston.toFixed(1), from: p.tP },
    ];
    for (const dm of dims) {
      extLine(c, dm.R, dm.from, dm.R, dm.y + 1);
      extLine(c, -dm.R, dm.from, -dm.R, dm.y + 1);
      diaDim(c, dm.R, dm.y, dm.label);
    }

    // 竖向尺寸（右侧）：t / H 用尺寸线，极小尺寸用引线标注
    const xd = R + 6;
    extLine(c, R, 0, xd + 2, 0);
    extLine(c, R, p.tP, xd + 2, p.tP);
    dimV(c, 0, p.tP, xd, 't=' + p.tP.toFixed(1));
    dimV(c, 0, zTop, R + 14, 'H=' + zTop.toFixed(1));
    leader(c, shimR, zShim0, R + 10, zTop + 4,
           'h_deck=' + p.deck.toFixed(2), 'start');
    leader(c, shimR, zTop, R + 10, zTop + 6.5,
           'Σt=' + p.stackT.toFixed(2), 'start');

    // 窗口尺寸（下方）
    const zWin = -5;
    extLine(c, 0, -1, 0, zWin - 1);
    extLine(c, p.portR, 0, p.portR, zWin - 1);
    dimH(c, 0, p.portR, zWin, 'r=' + p.portR.toFixed(1));
    const zDr = zWin - 7;
    if (p.dr > 0) {
      const a = Math.max(0.5, p.portR - p.dr / 2);
      const b = p.portR + p.dr / 2;
      extLine(c, a, 0, a, zDr - 1);
      extLine(c, b, 0, b, zDr - 1);
      dimH(c, a, b, zDr, 'dr=' + p.dr.toFixed(1));
    }

    // 标签（引线 + 白底文字，避免压住剖面线/尺寸）
    leader(c, p.portR, zMid, R + 2.5, -4,
           '窗口 ' + p.nPorts + '×（w=' + p.w.toFixed(1) + '）', 'start');
    if (p.bypassD > 0) {
      leader(c, p.bypassR, zMid, R + 2.5, -8.5,
             '常通孔 ' + Math.max(1, p.bypassN) + '×Ø' + p.bypassD.toFixed(1), 'start');
    }
    leader(c, (rodR + R) / 2, p.tP * 0.45, R + 2.5, 3.0, '活塞体', 'start');
    leader(c, (clampR + shimR) / 2, zTop - p.stackT * 0.5,
           R + 2.5, zTop + 1.0, '阀片堆叠', 'start');
    leader(c, clampR * 0.5, zTop + 1.2, -R * 0.62, zTop + 2.2, '夹紧', 'end');

    // ---- 标题栏 ----
    const tb = { x0: -R * 0.82, x1: R * 0.82, z0: -31, z1: -17 };
    rect(c, tb.x0, tb.z0, tb.x1 - tb.x0, tb.z1 - tb.z0, 'title', '#ffffff');
    line(c, tb.x0, tb.z1 - 4.5, tb.x1, tb.z1 - 4.5, 'title');
    line(c, tb.x0, tb.z1 - 8.5, tb.x1, tb.z1 - 8.5, 'title');
    line(c, tb.x0, tb.z1 - 11.5, tb.x1, tb.z1 - 11.5, 'title');
    text(c, 0, tb.z1 - 2.2, '阀体设计图（半剖）', 3.0, 'text', 'center', true);
    text(c, 0, tb.z1 - 6.6, 'VALVE BODY SECTION', 2.2, 'text', 'center');
    text(c, tb.x0 + 1.5, tb.z1 - 10.2,
         '单位 mm · 比例 1:1 · 半剖', 2.0, 'text', 'start');
    text(c, tb.x0 + 1.5, tb.z1 - 13.4,
         'Ø' + p.dPiston.toFixed(0) + ' / Ø' + p.dRod.toFixed(0) +
         ' · ' + p.nPorts + '×窗口', 1.8, 'text', 'start');
    text(c, tb.x1 - 1.5, tb.z1 - 13.4, 'ShimTuner', 1.6, 'text', 'end');

    const bbox = {
      x0: -R - 8, x1: R + 34,
      z0: tb.z0 - 2, z1: dZ + 21 + 6,
    };
    return { cmds: c, bbox, p };
  }

  // ---------------- Canvas 渲染 ----------------
  function styleOf(s) { return STYLE[s] || STYLE.outline; }

  function paint(ctx2d, dwg, mapper, pxPerMm) {
    const scale = pxPerMm;
    ctx2d.save();
    ctx2d.lineCap = 'round';
    ctx2d.lineJoin = 'round';
    for (const cmd of dwg.cmds) {
      const st = styleOf(cmd.s);
      if (cmd.t === 'l') {
        const a = mapper(cmd.x1, cmd.y1), b = mapper(cmd.x2, cmd.y2);
        ctx2d.strokeStyle = st.color;
        ctx2d.lineWidth = Math.max(0.8, st.width * scale);
        ctx2d.setLineDash((st.dash || []).map((v) => v * scale));
        ctx2d.beginPath(); ctx2d.moveTo(a[0], a[1]); ctx2d.lineTo(b[0], b[1]);
        ctx2d.stroke();
      } else if (cmd.t === 'r') {
        const a = mapper(cmd.x, cmd.y + cmd.h);
        const b = mapper(cmd.x + cmd.w, cmd.y);
        ctx2d.fillStyle = cmd.fill || '#ffffff';
        ctx2d.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
        ctx2d.strokeStyle = st.color;
        ctx2d.lineWidth = Math.max(0.8, st.width * scale);
        ctx2d.setLineDash([]);
        ctx2d.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      } else if (cmd.t === 'p') {
        ctx2d.beginPath();
        cmd.pts.forEach(([x, y], i) => {
          const q = mapper(x, y);
          if (i === 0) ctx2d.moveTo(q[0], q[1]); else ctx2d.lineTo(q[0], q[1]);
        });
        ctx2d.closePath();
        if (cmd.fill) { ctx2d.fillStyle = cmd.fill; ctx2d.fill(); }
        ctx2d.strokeStyle = st.color;
        ctx2d.lineWidth = Math.max(0.8, st.width * scale);
        ctx2d.setLineDash([]);
        ctx2d.stroke();
      } else if (cmd.t === 't') {
        const q = mapper(cmd.x, cmd.y);
        const px = Math.max(6, Math.min(40, cmd.size * scale));
        ctx2d.font = (cmd.bold ? '600 ' : '') + px + 'px ' + FONT;
        ctx2d.fillStyle = st.color;
        ctx2d.textAlign = cmd.align || 'center';
        ctx2d.textBaseline = 'middle';
        ctx2d.setLineDash([]);
        if (cmd.rot) {
          ctx2d.save();
          ctx2d.translate(q[0], q[1]);
          ctx2d.rotate(-cmd.rot * Math.PI / 180);
          ctx2d.fillText(cmd.str, 0, 0);
          ctx2d.restore();
        } else {
          ctx2d.fillText(cmd.str, q[0], q[1]);
        }
      }
    }
    ctx2d.restore();
  }

  // ---------------- SVG ----------------
  function esc(s) {
    return String(s).replace(/[&<>"]/g, (m) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));
  }
  function toSVG(dwg) {
    const b = dwg.bbox;
    const W = b.x1 - b.x0, H = b.z1 - b.z0;
    const Y = (z) => b.z1 - z;
    const out = [];
    out.push('<svg xmlns="http://www.w3.org/2000/svg" ' +
      `width="${W.toFixed(2)}mm" height="${H.toFixed(2)}mm" ` +
      `viewBox="${b.x0.toFixed(3)} 0 ${W.toFixed(3)} ${H.toFixed(3)}">`);
    out.push(`<rect x="${b.x0}" y="0" width="${W}" height="${H}" fill="#ffffff"/>`);
    for (const cmd of dwg.cmds) {
      const st = styleOf(cmd.s);
      const dash = st.dash ? ` stroke-dasharray="${st.dash.join(' ')}"` : '';
      if (cmd.t === 'l') {
        out.push(`<line x1="${cmd.x1.toFixed(3)}" y1="${Y(cmd.y1).toFixed(3)}" ` +
          `x2="${cmd.x2.toFixed(3)}" y2="${Y(cmd.y2).toFixed(3)}" ` +
          `stroke="${st.color}" stroke-width="${st.width}"${dash}/>`);
      } else if (cmd.t === 'r') {
        out.push(`<rect x="${cmd.x.toFixed(3)}" y="${Y(cmd.y + cmd.h).toFixed(3)}" ` +
          `width="${cmd.w.toFixed(3)}" height="${cmd.h.toFixed(3)}" ` +
          `fill="${cmd.fill || '#ffffff'}" stroke="${st.color}" ` +
          `stroke-width="${st.width}"/>`);
      } else if (cmd.t === 'p') {
        const pts = cmd.pts.map(([x, y]) => `${x.toFixed(3)},${Y(y).toFixed(3)}`).join(' ');
        out.push(`<polygon points="${pts}" fill="${cmd.fill || 'none'}" ` +
          `stroke="${st.color}" stroke-width="${st.width}"/>`);
      } else if (cmd.t === 't') {
        const anchor = cmd.align === 'start' ? 'start'
          : cmd.align === 'end' ? 'end' : 'middle';
        const rot = cmd.rot
          ? ` transform="rotate(${(-cmd.rot).toFixed(2)} ${cmd.x.toFixed(3)} ${Y(cmd.y).toFixed(3)})"`
          : '';
        out.push(`<text x="${cmd.x.toFixed(3)}" y="${Y(cmd.y).toFixed(3)}" ` +
          `font-family='${FONT}' font-size="${cmd.size}" fill="${st.color}" ` +
          `text-anchor="${anchor}" dominant-baseline="middle"${rot}` +
          `${cmd.bold ? ' font-weight="600"' : ''}>${esc(cmd.str)}</text>`);
      }
    }
    out.push('</svg>');
    return out.join('\n');
  }

  // ---------------- PNG ----------------
  function toPNG(dwg, pxPerMm) {
    const b = dwg.bbox;
    const s = pxPerMm || 10;
    const W = Math.max(1, Math.round((b.x1 - b.x0) * s));
    const H = Math.max(1, Math.round((b.z1 - b.z0) * s));
    const cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    const ctx2d = cv.getContext('2d');
    ctx2d.fillStyle = '#ffffff';
    ctx2d.fillRect(0, 0, W, H);
    const map = (x, y) => [(x - b.x0) * s, (b.z1 - y) * s];
    paint(ctx2d, dwg, map, s);
    return cv.toDataURL('image/png');
  }

  const api = { build, normalize, paint, toSVG, toPNG, STYLE };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.ValveDrawing = api;
})(typeof window !== 'undefined' ? window : globalThis);
