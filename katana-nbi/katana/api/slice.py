import logging
import os
from logging import handlers
from collections import deque
import uuid

from bson.json_util import dumps
from flask import request
from flask_classful import FlaskView, route
import requests
import urllib3

from katana.shared_utils.kafkaUtils import kafkaUtils
from katana.shared_utils.mongoUtils import mongoUtils
from katana.slice_mapping import slice_mapping

# Logging Parameters
logger = logging.getLogger(__name__)
file_handler = handlers.RotatingFileHandler("katana.log", maxBytes=10000, backupCount=5)
stream_handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
stream_formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
file_handler.setFormatter(formatter)
stream_handler.setFormatter(stream_formatter)
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


class SliceView(FlaskView):
    """
    Returns a list of slices and their details,
    used by: `katana slice ls`
    """

    urllib3.disable_warnings()
    route_prefix = "/api/"

    def _public_service_url(self, env_name, default_port):
        configured = os.getenv(env_name)
        if configured:
            return configured.rstrip("/")

        host = request.host.split(":", 1)[0]
        return f"{request.scheme}://{host}:{default_port}"

    def _prometheus_url(self):
        return os.getenv("KATANA_PROMETHEUS_URL", "http://katana-prometheus:9090").rstrip("/")

    def _slice_name(self, islice):
        return islice.get("slice_name") or islice.get("name") or islice["_id"]

    def _slice_prometheus_queries(self, islice):
        slice_id = islice["_id"]
        metric_slice_id = "slice_" + slice_id.replace("-", "_")
        queries = {
            "slice_status": f'katana_status{{slice_id="{slice_id}"}}',
            "network_services": f'ns_status{{slice_id="{slice_id}"}}',
        }

        if islice.get("slice_monitoring", {}).get("WIM"):
            queries["wim_flows_per_second"] = f"rate({metric_slice_id}_flows[1m])"

        return queries

    def _query_prometheus(self, queries):
        results = {}
        prometheus_url = self._prometheus_url()
        for name, query in queries.items():
            try:
                response = requests.get(
                    f"{prometheus_url}/api/v1/query",
                    params={"query": query},
                    timeout=2,
                )
                response.raise_for_status()
                results[name] = response.json()
            except Exception as exc:
                results[name] = {"status": "unavailable", "error": str(exc)}
        return results

    def _slice_observability_payload(self, islice, include_prometheus=False):
        monitoring = islice.get("slice_monitoring") or {}
        monitoring_configured = "slice_monitoring" in islice
        dashboard_available = bool(monitoring)
        dashboard_uid = islice["_id"] if dashboard_available else None
        grafana_url = self._public_service_url("KATANA_PUBLIC_GRAFANA_URL", 3000)
        prometheus_public_url = self._public_service_url("KATANA_PUBLIC_PROMETHEUS_URL", 9090)
        queries = self._slice_prometheus_queries(islice) if monitoring_configured else {}

        payload = {
            "_id": islice["_id"],
            "name": self._slice_name(islice),
            "status": islice.get("status"),
            "created_at": islice.get("created_at"),
            "monitoring": {
                "configured": monitoring_configured,
                "dashboard_available": dashboard_available,
                "details": monitoring,
                "grafana": {
                    "dashboard_uid": dashboard_uid,
                    "dashboard_url": f"{grafana_url}/d/{dashboard_uid}" if dashboard_uid else None,
                },
                "prometheus": {
                    "base_url": prometheus_public_url,
                    "queries": queries,
                },
            },
            "links": {
                "details": f"/api/slice/{islice['_id']}",
                "logs": f"/api/slice/{islice['_id']}/logs",
                "monitoring": f"/api/slice/{islice['_id']}/monitoring",
                "errors": f"/api/slice/{islice['_id']}/errors",
                "deployment_time": f"/api/slice/{islice['_id']}/time",
            },
        }

        if include_prometheus and queries:
            payload["monitoring"]["prometheus"]["results"] = self._query_prometheus(queries)

        return payload

    def _slice_state_events(self, islice):
        events = [
            {
                "source": "slice-record",
                "level": "info",
                "timestamp": islice.get("created_at"),
                "message": "Slice created",
                "slice_id": islice["_id"],
            }
        ]

        for step, duration in (islice.get("deployment_time") or {}).items():
            if duration is None:
                continue
            events.append(
                {
                    "source": "slice-record",
                    "level": "info",
                    "timestamp": None,
                    "message": f"{step} completed in {duration} seconds",
                    "slice_id": islice["_id"],
                }
            )

        for ns_id, locations in (islice.get("ns_inst_info") or {}).items():
            for location, info in locations.items():
                status = info.get("status")
                if not status:
                    continue
                events.append(
                    {
                        "source": "slice-record",
                        "level": "info",
                        "timestamp": None,
                        "message": f"Network service {ns_id} at {location} is {status}",
                        "slice_id": islice["_id"],
                    }
                )

        runtime_errors = islice.get("runtime_errors") or {}
        for key, value in runtime_errors.items():
            events.append(
                {
                    "source": "slice-record",
                    "level": "error",
                    "timestamp": None,
                    "message": f"Runtime error in {key}: {value}",
                    "slice_id": islice["_id"],
                }
            )

        events.append(
            {
                "source": "slice-record",
                "level": "info",
                "timestamp": None,
                "message": f"Current slice status: {islice.get('status')}",
                "slice_id": islice["_id"],
            }
        )
        return events

    def _local_slice_log_lines(self, slice_id, limit):
        paths = ["katana.log"]
        paths.extend(f"katana.log.{index}" for index in range(5, 0, -1))
        lines = deque(maxlen=limit)

        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, mode="r", encoding="utf-8", errors="replace") as log_file:
                    for line in log_file:
                        if slice_id in line:
                            lines.append(
                                {
                                    "source": path,
                                    "message": line.rstrip(),
                                    "slice_id": slice_id,
                                }
                            )
            except OSError as exc:
                lines.append(
                    {
                        "source": path,
                        "level": "warning",
                        "message": f"Could not read log file: {exc}",
                        "slice_id": slice_id,
                    }
                )

        return list(lines)

    def _request_limit(self, default=100, maximum=500):
        try:
            limit = int(request.args.get("limit", default))
        except (TypeError, ValueError):
            limit = default
        return max(1, min(limit, maximum))

    def _validate_slice_payload(self, data):
        if not isinstance(data, dict):
            return "Error: Request body must be a JSON object", 400

        base_slice_descriptor = data.get("base_slice_descriptor")
        if base_slice_descriptor is None:
            return "Error: Required field base_slice_descriptor is missing", 400
        if not isinstance(base_slice_descriptor, dict):
            return "Error: Field 'base_slice_descriptor' must be a JSON object", 400

        for field in ("service_descriptor", "test_descriptor"):
            if data.get(field) is not None and not isinstance(data[field], dict):
                return f"Error: Field '{field}' must be a JSON object", 400

        return None

    def index(self):
        """
        Returns a list of slices and their details,
        used by: `katana slice ls`
        """
        slice_data = mongoUtils.index("slice")
        return_data = []
        for islice in slice_data:
            return_data.append(dict(_id=islice["_id"], name=islice["slice_name"], created_at=islice["created_at"], status=islice["status"],))
        return dumps(return_data), 200

    @route("/observability")
    def observability_index(self):
        """
        Returns frontend-friendly monitoring/log links for all existing slices.
        """
        include_prometheus = request.args.get("include_prometheus") == "true"
        slice_data = mongoUtils.index("slice")
        return dumps(
            [
                self._slice_observability_payload(islice, include_prometheus=include_prometheus)
                for islice in slice_data
            ]
        ), 200

    def get(self, uuid):
        """
        Returns the details of specific slice,
        used by: `katana slice inspect [uuid]`
        """
        data = mongoUtils.get("slice", uuid)
        if data:
            return dumps(data), 200
        else:
            return "Not Found", 404

    @route("/<uuid>/observability")
    def show_observability(self, uuid):
        """
        Returns frontend-friendly monitoring/log links for one slice.
        """
        islice = mongoUtils.get("slice", uuid)
        if not islice:
            return "Slice not found", 404

        include_prometheus = request.args.get("include_prometheus", "true") != "false"
        return dumps(
            self._slice_observability_payload(islice, include_prometheus=include_prometheus)
        ), 200

    @route("/<uuid>/monitoring")
    def show_monitoring(self, uuid):
        """
        Returns monitoring details for one slice.
        """
        islice = mongoUtils.get("slice", uuid)
        if not islice:
            return "Slice not found", 404

        include_prometheus = request.args.get("include_prometheus", "true") != "false"
        payload = self._slice_observability_payload(
            islice,
            include_prometheus=include_prometheus,
        )
        return dumps(payload["monitoring"]), 200

    @route("/<uuid>/logs")
    def show_logs(self, uuid):
        """
        Returns stored slice events, runtime errors, and best-effort matching NBI log lines.
        """
        islice = mongoUtils.get("slice", uuid)
        if not islice:
            return "Slice not found", 404

        limit = self._request_limit()
        payload = {
            "_id": islice["_id"],
            "name": self._slice_name(islice),
            "status": islice.get("status"),
            "events": self._slice_state_events(islice),
            "log_lines": self._local_slice_log_lines(uuid, limit),
            "log_count": None,
            "limit": limit,
            "note": (
                "log_lines are best-effort matches from this API container's rotating "
                "katana.log files. Manager/container log aggregation is not configured."
            ),
        }
        payload["log_count"] = len(payload["log_lines"])
        return dumps(payload), 200

    @route("/<uuid>/time")
    def show_time(self, uuid):
        """
        Returns deployment time of a slice
        """
        islice = mongoUtils.get("slice", uuid)
        if islice:
            return dumps(islice["deployment_time"]), 200
        else:
            return "Not Found", 404

    @route("/<uuid>/modify", methods=["POST"])
    def modify(self, uuid):
        """
        Update the details of a specific slice.
        used by: `katana slice modify -f [file] [uuid]`
        """
        result = mongoUtils.get("slice", uuid)
        if not result:
            return f"Error: No such slice: {uuid}", 404
        # Send the message to katana-mngr
        producer = kafkaUtils.create_producer()
        slice_message = {"action": "update", "slice_id": uuid, "updates": request.json}
        producer.send("slice", value=slice_message)
        return f"Updating {uuid}", 200

    def post(self):
        """
        Add a new slice. The request must provide the slice details.
        used by: `katana slice add -f [file]`
        """
        new_uuid = str(uuid.uuid4())
        data = request.get_json(silent=True) or {}
        data["_id"] = new_uuid
        validation_error = self._validate_slice_payload(data)
        if validation_error:
            return validation_error

        # Get the NEST from the Slice Mapping process
        nest, error_code = slice_mapping.nest_mapping(data)

        if error_code:
            return nest, error_code

        # Send the message to katana-mngr
        producer = kafkaUtils.create_producer()
        slice_message = {"action": "add", "message": nest}
        producer.send("slice", value=slice_message)

        return new_uuid, 201

    def delete(self, uuid):
        """
        Delete a specific slice.
        used by: `katana slice rm [uuid]`
        """

        # Check if slice uuid exists
        delete_json = mongoUtils.get("slice", uuid)
        try:
            force = request.args["force"]
        except KeyError:
            force = None
        else:
            force = force if force == "true" else None

        if not delete_json:
            return f"Error: No such slice: {uuid}", 404
        else:
            # Send the message to katana-mngr
            producer = kafkaUtils.create_producer()
            slice_message = {"action": "delete", "message": uuid, "force": force}
            producer.send("slice", value=slice_message)
            return f"Deleting {uuid}", 200

    @route("<uuid>/errors")
    def show_errors(self, uuid):
        """
        Display the runitime errors of a slice
        """
        data = mongoUtils.get("slice", uuid)
        if data:
            runtime_errors = data["runtime_errors"]
            return dumps(runtime_errors), 200
        else:
            return "Slice not found", 404
