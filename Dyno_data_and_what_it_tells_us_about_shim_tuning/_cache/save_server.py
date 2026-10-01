"""Local save server: browser POSTs raw data here, it lands in _cache/.

Endpoints:
  GET  /                      -> 'ok'
  POST /save/<relative/path>  -> writes request body bytes to _cache/<relative/path>
CORS is wide open so the HTTPS forum page can post to http://127.0.0.1:8765.
"""
import os, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ROOT = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning\_cache"
os.makedirs(ROOT, exist_ok=True)
LOG = os.path.join(ROOT, "_server.log")

class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, *")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b""
            rel = unquote(self.path[len("/save/"):]) if self.path.startswith("/save/") else None
            if not rel:
                self.send_response(400); self._cors(); self.end_headers(); return
            rel = rel.replace("\\", "/").lstrip("/")
            dst = os.path.join(ROOT, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(body)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"saved {rel} {len(body)} bytes\n")
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"saved")
        except Exception as e:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"ERROR {self.path}: {e}\n")
            try:
                self.send_response(500); self._cors(); self.end_headers()
            except Exception:
                pass

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8765), H)
    print("save-server listening on 127.0.0.1:8765", flush=True)
    srv.serve_forever()
