"""Tiny SSE server to measure whether the OOD proxy chain buffers event streams.
Emits 12 events 400ms apart (each flushed), with the same SSE/no-buffering
headers ABA's /api/chat uses. If a client reading through /rnode sees events
arrive ~400ms apart, the proxy streams; if they all land at the end, it buffers.

Usage: python3 _sse_probe.py <port>
"""
import sys, time, json
# HTTPServer (not ThreadingHTTPServer) so it runs on the node's system python 3.6;
# single-threaded is fine — the test reads / then /sse sequentially.
from http.server import BaseHTTPRequestHandler, HTTPServer

N, GAP = 12, 0.4


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0].rstrip('/').endswith('sse'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            for i in range(N):
                try:
                    self.wfile.write(f"data: {json.dumps({'i': i, 't': time.time()})}\n\n".encode())
                    self.wfile.flush()
                except Exception:
                    break
                time.sleep(GAP)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b"<h1>sse probe ok</h1><p>GET /sse</p>")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(('0.0.0.0', int(sys.argv[1])), H).serve_forever()
