# -*- coding: utf-8 -*-
"""Copy selected images into images/ folder."""
import os, shutil

BASE = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
SRC = os.path.join(BASE, "images_raw")
DST = os.path.join(BASE, "images")
os.makedirs(DST, exist_ok=True)

files = """1a-TT-dyno-valves.png|2-dyno-range.png|3-stacks-all.png|18-curve-shape.png|19-suspension-speed.png|1-belleville.png|2-shim-factor.png|3-sf-table.png|4-stack-sf.png|6-almen-laszlo.png|7-tsf-work.png|8-tsf-eqn.png|10-stack-tsf.png|34-tsf-eqn.png|11-sf-dyno.png|12-ohlins4pc.png|18-ohlins-dyno.png|20-ohlins-finger.png|x1-stack-deflection.png|13-finger-press.png|13a-finger-press.jpg|14-finger-data.png|15-edge-lift.png|16-finger-force.png|17-finger-press-scandi.png|21-finger-press-maxv.png|26-backer-limit.png|27-ohlins-3pc.png|29-ct2025-dyno.png|31-kyb46wc.png|32-rmz250-152-dyno.png|22-belleville-stiffness.png|22a-belleville-stiffness.png|23-belleville-compreession.png|24-shim-deflection.png|25-coffee-filter.png|21c-nonlinear.png|5-fulcrum.png|6-stacked-bowls.png|7-finger-press.png|8-racerfmx-stack-height.png|1-mx3c.png|3-mx3c-dyno.png|5-mx9c.png|6-mx9c-fea.png|7-mx9c-dyno.png|13-mog-dyno.png|14-dyno-table.png|9-mog.png|10-mog-table.png|8a-styles.png|c1-co-styles.png|24-trapped-cross.png|26-jake-norm-dyno.png|8-open-stages.png|9-mash-stack.png|30g-fo-shimfac14.png|31-fo-rmz.png|18b-co-close.png|1-straight-v-taper-stack.png|3-jake-dyno.png|6-VL-straight-v-taper-stack.png|7-VL-straight-v-taper-dyno.png|9-kawa-straight-v-taper-stack.png|10-kawa-straight-v-taper-dyno.png|13-clamp-stress.png|4-trapped-co.png|5-tapered-section.png|b1-TT-dyno-valves.png|b2-kyb46mm-rebound.png|b3-kyb46mm-compression.png|b4-hdeck.png|b6-showa-valve.png|21-racetech-stack.png|22-racetech-3pc.png|23-racetech-dyno.png|23b-port-area.png|23c-port-perim.png|24-stack-wave.png|28-ring-stack.png|29-ring-shim.png|30-ring-lift.png|31-ring-dyno.png|33-ring-ying-yang.png|8-shape-tuning.png|4-crossover.png|5-crossover-match.png|6-ring-shim.png|7-preload-match.png|b7-flow-circuits.png|b7a-flow-circuits.png|10-yz250c-hysteresis.png|9-yz250-chmb-press.png|12-penske.png|11-curea-yz250-dyno.png|16-FC-chmb-press.png|20-kxf250-pbal.png|10-spring-mass-damp.png|13-zeta-response.png|12-zeta.png|1-peak-eqn.png|2-force.png|3-balance.png|4-yz-oil-flow.png|5-shaft-correction.png|6-ema-dyno.png|7-ema-error.png|8-vl-psf.png|9-psf-vel.png|1-stack-uncertainty.png|2-concave-surface.png|5-pvp-vs-cvp-dyno.png|15-TT-dyno-data.png|16-pdf.png|1-tsf-data2.png|x6-sf-error.png|41-tsf-error.png|1-main-event.png|2-low-speed.png|3-high-speed.png""".split("|")

ok, miss = 0, []
log = []
for f in files:
    p = os.path.join(SRC, f)
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(DST, f))
        ok += 1
    else:
        miss.append(f)
log.append(f"copied {ok}/{len(files)}")
log.append("missing: " + ", ".join(miss))
with open(os.path.join(BASE, "_cache", "_copyout.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(log))
print("\n".join(log))
