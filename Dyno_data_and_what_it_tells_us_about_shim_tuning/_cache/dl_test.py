"""Test direct downloads: CDN image + HTML page with both cookies."""
import urllib.request, gzip, json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Code/1.139.1 Chrome/150.0.7871.250 Electron/43.6.0 Safari/537.36")
report = []

def get(url, cookie=None, binary=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*" if binary else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, len(raw), raw[:64]
    except Exception as e:
        return None, str(e)[:200], b""

# 1) CDN image, no cookie
s, n, head = get("https://cdn.thumpertalk.com/uploads/imageproxy/12-ohlins4pc.png.a221c79d51d4e05cdce3d2cb9d81cbab.png", binary=True)
report.append(f"IMG no-cookie: status={s} len={n} head={head[:16]!r}")

# 2) HTML page with both cookies
CK = ("pow_bypass=5f08debb26fe35a7bb61877480819785|1790689901|6|b24902b2397ce5ef593ad99019b56f528bdf027250a304ad830f03f21ebceb36|3d63f17de633eced1e45765a; "
      "pow_trace=5f08debb26fe35a7bb61877480819785|1790689901")
s, n, head = get("https://www.thumpertalk.com/forums/topic/1124437-dyno-data-%E2%80%93and-what-it-tells-us-about-how-to-tune-a-shim-stack-and-control-the-shape-of-the-damping-force-curve/page/2/", cookie=CK)
report.append(f"HTML with both cookies: status={s} len={n} head={head[:80]!r}")

with open(r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning\_cache\_dltest.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(report))
print("done")
