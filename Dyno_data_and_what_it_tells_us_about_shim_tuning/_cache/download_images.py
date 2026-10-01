"""Download all thread images from the CDN into images_raw/, log results."""
import json, os, re, time, urllib.request, gzip

BASE = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
CACHE = os.path.join(BASE, "_cache")
OUT = os.path.join(BASE, "images_raw")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def fetch(url, out_path):
    url = "https:" + url if url.startswith("//") else url
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "image/*,*/*;q=0.8", "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            with open(out_path, "wb") as f:
                f.write(raw)
            return r.status, len(raw), raw[:8]
        except Exception as e:
            err = str(e)[:120]
            if attempt == 2:
                return None, err, b""
            time.sleep(1.5)
    return None, "fail", b""

with open(os.path.join(CACHE, "images_manifest.json"), "r", encoding="utf-8") as f:
    manifest = json.load(f)

log = []
ok = skip = fail = 0
for m in manifest:
    src, alt = m["src"], m["alt"]
    if "emoticon" in src or src.endswith(".gif"):
        skip += 1
        continue
    # name: prefer alt (has .png/.jpg); ensure uniqueness
    name = re.sub(r"[^\w.\-]", "_", alt) or f"img_{m['idx']}"
    if not re.search(r"\.(png|jpe?g|gif|webp)$", name, re.I):
        name += ".png"
    out = os.path.join(OUT, name)
    if os.path.exists(out) and os.path.getsize(out) > 100:
        ok += 1
        log.append(f"CACHED {name}")
        continue
    status, info, head = fetch(src, out)
    if status == 200 and isinstance(info, int):
        ok += 1
        log.append(f"OK {name} {info}")
    else:
        fail += 1
        log.append(f"FAIL {name} status={status} {info} url={src}")
    time.sleep(0.15)

log.append(f"--- ok={ok} skip={skip} fail={fail} total={len(manifest)}")
with open(os.path.join(CACHE, "_img_dl_log.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print(f"ok={ok} skip={skip} fail={fail}")
