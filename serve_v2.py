#!/usr/bin/env python3
"""Local dashboard server. Serves index.html and fetches Yahoo prices on demand, so the
Watchlists "Add" button fills in a new ticker right away (and remembers it in extra_tickers.txt).

    py serve_v2.py            # serve with the current data.js (use the page's refresh button to update)
    py serve_v2.py --fetch    # refresh all data first (takes a minute or two), then serve
"""
import json, re, sys, threading, webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yfinance as yf

from fetch_yf_v2 import extra_tickers, latest_html, num, returns, start_date

HERE = Path(__file__).parent
PORT = 8765
OK = re.compile(r"^[A-Z0-9.\-^=]{1,12}$")
JOB = {"running": False, "ok": None, "error": ""}


def start_refresh():
    """Run fetch_yf_v2.main() in the background; the page polls /refresh_status."""
    if JOB["running"]:
        return
    JOB.update(running=True, ok=None, error="")

    def work():
        try:
            import fetch_yf_v2
            fetch_yf_v2.main()
            JOB["ok"] = True
        except Exception as e:
            print("Refresh failed:", e)
            JOB.update(ok=False, error=str(e))
        finally:
            JOB["running"] = False

    threading.Thread(target=work, daemon=True).start()


def clean(raw):
    return [x.strip().upper().lstrip("$") for x in raw.split(",") if OK.match(x.strip().upper().lstrip("$"))]


def quotes(symbols):
    out = {}
    if not symbols:
        return out
    df = yf.download(symbols, start=start_date(), interval="1d", auto_adjust=False,
                     group_by="ticker", progress=False, threads=True)
    for s in symbols:
        try:
            cl = df[s]["Close"].dropna().tolist()
        except KeyError:
            continue
        if len(cl) >= 2:
            out[s] = {"last": num(cl[-1], 4), "prices": returns(cl)}
            if len(symbols) <= 3:  # name lookup is slow, so only for single adds
                try:
                    info = yf.Ticker(s).get_info()
                    out[s]["name"] = info.get("shortName") or info.get("longName")
                except Exception:
                    pass
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/quote":
            try:
                self.send_json(quotes(clean(q.get("symbols", [""])[0])))
            except Exception as e:
                print("quote error:", e)
                self.send_json({})
        elif u.path == "/refresh":
            start_refresh()
            self.send_json({"started": True})
        elif u.path == "/refresh_status":
            self.send_json(JOB)
        elif u.path == "/extra":
            for t in clean(q.get("add", [""])[0]):
                if t not in extra_tickers():
                    with open(HERE / "extra_tickers.txt", "a", encoding="utf-8") as f:
                        f.write("\n" + t)
            self.send_json({"ok": True})
        else:
            super().do_GET()


def main():
    if "--fetch" in sys.argv:
        import fetch_yf_v2
        try:
            fetch_yf_v2.main()
        except Exception as e:
            print("Data refresh failed, serving the existing data.js:", e)
    url = f"http://127.0.0.1:{PORT}/{latest_html().name}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is busy - the server may already be running. Opening {url}")
        webbrowser.open(url)
        return
    print(f"Dashboard running at {url}   (close this window to stop)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
