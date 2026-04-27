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

    def _set_default_headers(self, content_length):
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

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
        self._set_default_headers(len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def _load_client(self, body):
        from proxmox_standalone.client import ProxmoxVEClient

        return ProxmoxVEClient.from_config(body)

    def _load_overview(self, body):
        from proxmox_standalone.reporting import build_cluster_overview

        client = self._load_client(body)
        return build_cluster_overview(client, cluster_name=body.get("name", ""))

    def _send_endpoint_catalog(self):
        self._send_json(
            200,
            {
                "service": "proxmox-standalone",
                "endpoints": [
                    {"method": "GET", "path": "/health", "description": "Health check"},
                    {"method": "GET", "path": "/api", "description": "Endpoint catalog"},
                    {
                        "method": "POST",
                        "path": "/api/proxmox/test",
                        "description": "Validate authentication and return basic Proxmox info",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/connect",
                        "description": "Connect using only URL and credentials, then return available nodes",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/nodes",
                        "description": "Return available Proxmox nodes for the authenticated user",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/overview",
                        "description": "Return clusters, servers, vms, usage, remaining_resources",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/clusters",
                        "description": "Return only cluster metadata and summary",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/servers",
                        "description": "Return only Proxmox nodes/servers",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/vms",
                        "description": "Return only VMs from the overview payload",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/usage",
                        "description": "Return only cluster-wide usage",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/remaining-resources",
                        "description": "Return only remaining capacity",
                    },
                    {
                        "method": "POST",
                        "path": "/api/proxmox/list-vms",
                        "description": "Return raw VM list for a specific node",
                    },
                ],
            },
        )

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

        if method == "GET" and path == "/api":
            self._send_endpoint_catalog()
            return

        if method == "POST" and path == "/api/proxmox/test":
            body = self._read_json_body()
            client = self._load_client(body)
            self._send_json(
                200,
                {
                    "cluster": body.get("name"),
                    "authenticated": True,
                    "version": client.version(),
                    "nodes": client.list_nodes(),
                },
            )
            return

        if method == "POST" and path == "/api/proxmox/connect":
            body = self._read_json_body()
            client = self._load_client(body)
            self._send_json(
                200,
                {
                    "cluster": body.get("name"),
                    "authenticated": True,
                    "version": client.version(),
                    "nodes": client.list_nodes(),
                },
            )
            return

        if method == "POST" and path == "/api/proxmox/nodes":
            body = self._read_json_body()
            client = self._load_client(body)
            self._send_json(
                200,
                {
                    "cluster": body.get("name"),
                    "nodes": client.list_nodes(),
                },
            )
            return

        if method == "POST" and path == "/api/proxmox/list-vms":
            body = self._read_json_body()
            query_params = parse_qs(parsed.query)
            client = self._load_client(body)
            node = query_params.get("node", [body.get("node")])[0]
            if not node:
                raise ValueError(
                    "Missing 'node'. First call /api/proxmox/connect or /api/proxmox/nodes to fetch available nodes, then pass the selected node here."
                )

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
            self._send_json(200, self._load_overview(body))
            return

        if method == "POST" and path == "/api/proxmox/clusters":
            body = self._read_json_body()
            overview = self._load_overview(body)
            self._send_json(200, {"clusters": overview["clusters"]})
            return

        if method == "POST" and path == "/api/proxmox/servers":
            body = self._read_json_body()
            overview = self._load_overview(body)
            self._send_json(200, {"servers": overview["servers"]})
            return

        if method == "POST" and path == "/api/proxmox/vms":
            body = self._read_json_body()
            overview = self._load_overview(body)
            self._send_json(200, {"vms": overview["vms"]})
            return

        if method == "POST" and path == "/api/proxmox/usage":
            body = self._read_json_body()
            overview = self._load_overview(body)
            self._send_json(200, {"usage": overview["usage"]})
            return

        if method == "POST" and path == "/api/proxmox/remaining-resources":
            body = self._read_json_body()
            overview = self._load_overview(body)
            self._send_json(200, {"remaining_resources": overview["remaining_resources"]})
            return

        self._send_json(404, {"error": f"Route not found: {method} {path}"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_default_headers(0)
        self.end_headers()

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
