# -*- coding: utf-8 -*-
"""Verify all images referenced in the md exist; copy any missing from images_raw."""
import os, re, shutil

BASE = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
MD = os.path.join(BASE, "ShimTuning_Dyno数据技术参考.md")
IMG = os.path.join(BASE, "images")
RAW = os.path.join(BASE, "images_raw")

with open(MD, "r", encoding="utf-8") as f:
    text = f.read()

refs = re.findall(r"\]\((images/[^)]+)\)", text)
print("total refs:", len(refs))

missing = []
for r in refs:
    p = os.path.join(BASE, r.replace("/", os.sep))
    if not os.path.exists(p):
        missing.append(os.path.basename(r))

print("missing:", missing)
fixed = []
for m in missing:
    src = os.path.join(RAW, m)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(IMG, m))
        fixed.append(m)
if fixed:
    print("copied from raw:", fixed)

# re-verify
still = [m for m in missing if not os.path.exists(os.path.join(IMG, m))]
print("STILL MISSING:", still)

# count total images now
n = len([f for f in os.listdir(IMG) if os.path.isfile(os.path.join(IMG, f))])
print("images folder total:", n)
