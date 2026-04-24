import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


def build_parser():
    parser = argparse.ArgumentParser(description="Standalone Proxmox VE HTTP API")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8099, help="Bind port")
    return parser


class ProxmoxHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "ProxmoxStandaloneHTTP/1.0"

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc

    def _send_json(self, status_code, payload):
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _route_request(self, method):
        parsed = urlparse(self.path)
        path = parsed.path

        if method == "GET" and path == "/health":
            self._send_json(
                200,
                {
                    "service": "proxmox-standalone",
                    "status": "ok",
                },
            )
            return

        if method == "POST" and path == "/api/proxmox/test":
            body = self._read_json_body()
            from proxmox_standalone.client import ProxmoxVEClient

            client = ProxmoxVEClient.from_config(body)
            self._send_json(
                200,
                {
                    "cluster": body.get("name"),
                    "version": client.version(),
                    "nodes": client.list_nodes(),
                },
            )
            return

        if method == "POST" and path == "/api/proxmox/list-vms":
            body = self._read_json_body()
            query_params = parse_qs(parsed.query)
            from proxmox_standalone.client import ProxmoxVEClient

            client = ProxmoxVEClient.from_config(body)
            node = query_params.get("node", [body.get("node")])[0]
            if not node:
                raise ValueError("Missing 'node' in request body or query string")

            self._send_json(
                200,
                {
                    "cluster": body.get("name"),
                    "node": node,
                    "vms": client.list_vms(node),
                },
            )
            return

        if method == "POST" and path == "/api/proxmox/overview":
            body = self._read_json_body()
            from proxmox_standalone.client import ProxmoxVEClient
            from proxmox_standalone.reporting import build_cluster_overview

            client = ProxmoxVEClient.from_config(body)
            self._send_json(200, build_cluster_overview(client, cluster_name=body.get("name", "")))
            return

        self._send_json(404, {"error": f"Route not found: {method} {path}"})

    def do_GET(self):
        try:
            self._route_request("GET")
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})

    def do_POST(self):
        try:
            self._route_request("POST")
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})

    def log_message(self, format_string, *args):
        return


def main():
    parser = build_parser()
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ProxmoxHTTPRequestHandler)
    print(
        json.dumps(
            {
                "service": "proxmox-standalone",
                "host": args.host,
                "port": args.port,
            }
        )
    )
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
