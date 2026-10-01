"""Build readable text dumps of the thread for analysis."""
import json, os

BASE = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
CACHE = os.path.join(BASE, "_cache")
OUT = os.path.join(BASE, "_cache", "readable")
os.makedirs(OUT, exist_ok=True)

with open(os.path.join(CACHE, "thread.json"), "r", encoding="utf-8") as f:
    posts = json.load(f)

def render(p, i):
    head = f"\n{'='*100}\nPOST #{i:03d} | author={p['author']} | page={p['p']} | id={p['id']} | date={p['date']}\n{'='*100}\n"
    return head + p["text"] + "\n"

# full thread dump, split in parts of ~120000 chars
lines = []
for i, p in enumerate(posts):
    lines.append(render(p, i))
full = "\n".join(lines)
parts = []
cur = []
size = 0
part = 1
for chunk in lines:
    if size + len(chunk) > 110000 and cur:
        with open(os.path.join(OUT, f"full_{part:02d}.txt"), "w", encoding="utf-8") as f:
            f.write("".join(cur))
        parts.append(part)
        cur, size, part = [], 0, part + 1
    cur.append(chunk)
    size += len(chunk)
if cur:
    with open(os.path.join(OUT, f"full_{part:02d}.txt"), "w", encoding="utf-8") as f:
        f.write("".join(cur))
    parts.append(part)

# clicked-only dump
cl = [render(p, i) for i, p in enumerate(posts) if p["author"] == "Clicked"]
cur, size, part = [], 0, 1
cparts = []
for chunk in cl:
    if size + len(chunk) > 110000 and cur:
        with open(os.path.join(OUT, f"clicked_{part:02d}.txt"), "w", encoding="utf-8") as f:
            f.write("".join(cur))
        cparts.append(part)
        cur, size, part = [], 0, part + 1
    cur.append(chunk)
    size += len(chunk)
if cur:
    with open(os.path.join(OUT, f"clicked_{part:02d}.txt"), "w", encoding="utf-8") as f:
        f.write("".join(cur))
    cparts.append(part)

print("full parts:", parts)
print("clicked parts:", cparts)
