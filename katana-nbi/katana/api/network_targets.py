import logging
from logging import handlers
import time
import uuid

from bson.json_util import dumps
from flask import request
from flask_classful import FlaskView, route

from katana.shared_utils.emsUtils.generic_sliceUtils import SliceAdapterClient
from katana.shared_utils.mongoUtils import mongoUtils


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


class NetworkTargetsView(FlaskView):
    """
    Registry for generic network slice targets.

    A target is a concrete adapter endpoint for one managed part of a lab, such
    as one Amarisoft RAN config, one Amarisoft CORE config, or one Free5GC core.
    """

    route_prefix = "/api"
    route_base = "/network-targets"
    collection = "network_targets"
    req_fields = ["id", "role", "driver", "url"]

    def __init__(self):
        self.client = SliceAdapterClient(timeout=30)

    def index(self):
        target_data = mongoUtils.index(self.collection)
        return_data = []
        for item in target_data:
            return_data.append(
                {
                    "_id": item["_id"],
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "role": item.get("role"),
                    "driver": item.get("driver"),
                    "url": item.get("url"),
                    "enabled": item.get("enabled", True),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
            )
        return dumps(return_data), 200

    def get(self, uuid):
        data = mongoUtils.get(self.collection, uuid)
        if not data:
            return "Not Found", 404
        return dumps(data), 200

    def post(self):
        data = request.get_json(silent=True) or {}
        validation_error = self._validate_target_payload(data)
        if validation_error:
            return validation_error

        if mongoUtils.find(self.collection, {"id": data["id"]}):
            return f"Error: Network target with id {data['id']} already exists", 400

        now = time.time()
        target_id = str(uuid.uuid4())
        record = self._target_record(target_id, data, now)
        mongoUtils.add(self.collection, record)
        return dumps(record), 201

    def put(self, uuid):
        data = request.get_json(silent=True) or {}
        old_data = mongoUtils.get(self.collection, uuid)
        if not old_data:
            return f"Error: No such network target: {uuid}", 404

        validation_error = self._validate_target_payload(data)
        if validation_error:
            return validation_error

        existing = mongoUtils.find(self.collection, {"id": data["id"]})
        if existing and existing["_id"] != uuid:
            return f"Error: Network target with id {data['id']} already exists", 400

        record = self._target_record(uuid, data, time.time())
        record["created_at"] = old_data["created_at"]
        mongoUtils.update(self.collection, uuid, record)
        return dumps(record), 200

    def delete(self, uuid):
        result = mongoUtils.delete(self.collection, uuid)
        if not result:
            return f"Error: No such network target: {uuid}", 404
        return f"Deleted network target {uuid}", 200

    @route("/<uuid>/health", methods=["GET"])
    def health(self, uuid):
        target = mongoUtils.get(self.collection, uuid)
        if not target:
            return f"Error: No such network target: {uuid}", 404

        result = self.client.health(target["url"])
        status_code = 200 if result["ok"] else 502
        return dumps(
            {
                "_id": uuid,
                "id": target.get("id"),
                "role": target.get("role"),
                "driver": target.get("driver"),
                "url": target.get("url"),
                "health": result,
            }
        ), status_code

    def _validate_target_payload(self, data):
        if not isinstance(data, dict):
            return "Error: Request body must be a JSON object", 400

        missing = []
        for field in self.req_fields:
            if data.get(field) in (None, "", []):
                missing.append(field)
        if missing:
            return f"Error: Required fields missing: {', '.join(missing)}", 400

        if not isinstance(data.get("id"), str):
            return "Error: Field 'id' must be a string", 400
        if not isinstance(data.get("role"), str):
            return "Error: Field 'role' must be a string", 400
        if not isinstance(data.get("driver"), str):
            return "Error: Field 'driver' must be a string", 400
        if not isinstance(data.get("url"), str):
            return "Error: Field 'url' must be a string", 400

        for field in ("config", "payload", "metadata"):
            if data.get(field) is not None and not isinstance(data.get(field), dict):
                return f"Error: Field '{field}' must be a JSON object", 400

        return None

    def _target_record(self, target_uuid, data, now):
        return {
            "_id": target_uuid,
            "id": data["id"],
            "name": data.get("name", data["id"]),
            "role": data["role"],
            "driver": data["driver"],
            "url": data["url"].rstrip("/"),
            "enabled": bool(data.get("enabled", True)),
            "config": data.get("config", {}),
            "payload": data.get("payload", {}),
            "metadata": data.get("metadata", {}),
            "created_at": now,
            "updated_at": now,
        }
