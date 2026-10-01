"""Download all pages of the ThumperTalk shim tuning thread to _cache/."""
import os, sys, time, urllib.request, urllib.error, gzip, io

BASE = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
CACHE = os.path.join(BASE, "_cache")
os.makedirs(CACHE, exist_ok=True)

THREAD = "https://www.thumpertalk.com/forums/topic/1124437-dyno-data-%E2%80%93and-what-it-tells-us-about-how-to-tune-a-shim-stack-and-control-the-shape-of-the-damping-force-curve"
COOKIE = ("pow_bypass=5f08debb26fe35a7bb61877480819785|1790689901|6|"
          "b24902b2397ce5ef593ad99019b56f528bdf027250a304ad830f03f21ebceb36|3d63f17de633eced1e45765a")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Code/1.139.1 Chrome/150.0.7871.250 Electron/43.6.0 Safari/537.36")

def fetch(url, out_path):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Cookie": COOKIE,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                html = raw.decode("utf-8", errors="replace")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)
            return r.status, len(html)
        except Exception as e:
            if attempt == 2:
                return None, str(e)
            time.sleep(2)
    return None, "unknown"

def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 23
    for n in range(start, end + 1):
        out = os.path.join(CACHE, f"page_{n:02d}.html")
        if os.path.exists(out) and os.path.getsize(out) > 50000:
            print(f"page {n:02d}: cached")
            continue
        url = THREAD + ("/" if n == 1 else f"/page/{n}/")
        status, info = fetch(url, out)
        print(f"page {n:02d}: status={status} size={info}")
        time.sleep(1.0)

if __name__ == "__main__":
    main()
