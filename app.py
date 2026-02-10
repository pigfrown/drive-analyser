from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from drive_analyser import build_report, render_html


class DriveAnalyserHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self._send_html(render_html())

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        params = parse_qs(body)

        device = params.get("device", [""])[0].strip()
        mount_path = params.get("mount_path", [""])[0].strip()

        report = None
        error = None

        if not device or not mount_path:
            error = "Both device and mount path are required."
        else:
            try:
                report = build_report(device, mount_path)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

        self._send_html(render_html(report=report, error=error))

    def _send_html(self, content: str) -> None:
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run() -> None:
    server = HTTPServer(("0.0.0.0", 8000), DriveAnalyserHandler)
    print("Drive analyser running on http://0.0.0.0:8000")
    server.serve_forever()


if __name__ == "__main__":
    run()
