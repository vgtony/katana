import logging
from logging import handlers
import re
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


SD_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


class NetworkSlicesView(FlaskView):
    """
    Generic network-slice API.

    A slice can target any number of registered or direct adapter endpoints. The
    adapter decides how to map the normalized intent into device-specific config.
    """

    route_prefix = "/api"
    route_base = "/network-slices"
    collection = "network_slices"

    def __init__(self):
        self.client = SliceAdapterClient()

    def index(self):
        slice_data = mongoUtils.index(self.collection)
        return_data = []
        for item in slice_data:
            return_data.append(
                {
                    "_id": item["_id"],
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "s_nssai": item.get("s_nssai"),
                    "plmn": item.get("plmn"),
                    "dnn": item.get("dnn"),
                    "isolation_mode": item.get("isolation_mode"),
                    "target_count": len(item.get("targets", [])),
                    "target_roles": [target.get("role") for target in item.get("targets", [])],
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
        try:
            validation_error = self._validate_slice_payload(data)
            if validation_error:
                return validation_error

            now = time.time()
            slice_id = data.get("id") or str(uuid.uuid4())
            record = self._slice_record(slice_id, data, now)
        except ValueError as exc:
            return f"Error: {str(exc)}", 400

        dry_run = bool(data.get("dry_run", False))
        if dry_run:
            record["status"] = "planned"
            record["apply_result"] = {"dry_run": True, "targets": record["targets"]}
            mongoUtils.add(self.collection, record)
            return dumps(record), 201

        record = self._apply_record(record)
        mongoUtils.add(self.collection, record)
        status_code = 201 if record["status"] == "running" else 502
        return dumps(record), status_code

    def delete(self, uuid):
        record = mongoUtils.get(self.collection, uuid)
        if not record:
            return f"Error: No such network slice: {uuid}", 404

        delete_result = self._delete_from_targets(record)
        mongoUtils.delete(self.collection, uuid)
        return dumps({"_id": uuid, "deleted": True, "target_results": delete_result}), 200

    @route("/preview", methods=["POST"])
    def preview(self):
        data = request.get_json(silent=True) or {}
        try:
            validation_error = self._validate_slice_payload(data)
            if validation_error:
                return validation_error

            preview_id = data.get("id") or str(uuid.uuid4())
            record = self._slice_record(preview_id, data, time.time())
        except ValueError as exc:
            return f"Error: {str(exc)}", 400

        return dumps(
            {
                "_id": record["_id"],
                "name": record["name"],
                "status": "preview",
                "targets": record["targets"],
                "request": record["request"],
            }
        ), 200

    @route("/<uuid>/apply", methods=["POST"])
    def apply(self, uuid):
        record = mongoUtils.get(self.collection, uuid)
        if not record:
            return f"Error: No such network slice: {uuid}", 404

        if record.get("status") == "running":
            return dumps(record), 200

        record = self._apply_record(record)
        mongoUtils.update(self.collection, uuid, record)
        status_code = 200 if record["status"] == "running" else 502
        return dumps(record), status_code

    def _validate_slice_payload(self, data):
        if not isinstance(data, dict):
            return "Error: Request body must be a JSON object", 400

        missing = []
        for field in ("name", "s_nssai", "plmn", "dnn", "targets"):
            if data.get(field) in (None, "", []):
                missing.append(field)
        if missing:
            return f"Error: Required fields missing: {', '.join(missing)}", 400

        s_nssai = data.get("s_nssai")
        if not isinstance(s_nssai, dict):
            return "Error: Field 's_nssai' must be a JSON object", 400
        try:
            sst = int(s_nssai.get("sst"))
        except (TypeError, ValueError):
            return "Error: Field 's_nssai.sst' must be an integer", 400
        if sst < 0 or sst > 255:
            return "Error: Field 's_nssai.sst' must be between 0 and 255", 400

        sd = s_nssai.get("sd")
        if sd is not None and not SD_RE.match(str(sd).replace("0x", "")):
            return "Error: Field 's_nssai.sd' must be a 6 digit hex value", 400

        plmn_error = self._validate_plmn(data.get("plmn"))
        if plmn_error:
            return plmn_error, 400

        targets = data.get("targets")
        if not isinstance(targets, list):
            return "Error: Field 'targets' must be a list", 400
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                return f"Error: Field 'targets[{index}]' must be a JSON object", 400
            if not target.get("target_id") and not target.get("url"):
                return f"Error: Field 'targets[{index}]' requires target_id or url", 400
            if target.get("url") and not target.get("role"):
                return f"Error: Field 'targets[{index}].role' is required with direct url", 400
            if target.get("payload") is not None and not isinstance(target.get("payload"), dict):
                return f"Error: Field 'targets[{index}].payload' must be a JSON object", 400

        subscribers = data.get("subscribers", [])
        if subscribers is not None and not isinstance(subscribers, list):
            return "Error: Field 'subscribers' must be a list", 400

        qos = data.get("qos", {})
        if qos is not None and not isinstance(qos, dict):
            return "Error: Field 'qos' must be a JSON object", 400

        metadata = data.get("metadata", {})
        if metadata is not None and not isinstance(metadata, dict):
            return "Error: Field 'metadata' must be a JSON object", 400

        return None

    def _validate_plmn(self, plmn):
        if isinstance(plmn, str):
            if not plmn.isdigit() or len(plmn) not in (5, 6):
                return "Error: Field 'plmn' must be a 5 or 6 digit string"
            return None

        if not isinstance(plmn, dict):
            return "Error: Field 'plmn' must be a JSON object or string"
        mcc = str(plmn.get("mcc", ""))
        mnc = str(plmn.get("mnc", ""))
        if not (mcc.isdigit() and len(mcc) == 3):
            return "Error: Field 'plmn.mcc' must be a 3 digit string"
        if not (mnc.isdigit() and len(mnc) in (2, 3)):
            return "Error: Field 'plmn.mnc' must be a 2 or 3 digit string"
        return None

    def _slice_record(self, slice_id, data, now):
        s_nssai = self._normalize_s_nssai(data["s_nssai"])
        plmn = self._normalize_plmn(data["plmn"])
        request_payload = {
            "slice_id": slice_id,
            "name": data["name"],
            "s_nssai": s_nssai,
            "plmn": plmn,
            "dnn": data["dnn"],
            "qos": data.get("qos", {}),
            "subscribers": data.get("subscribers", []),
            "isolation_mode": data.get("isolation_mode", "shared"),
            "description": data.get("description"),
            "metadata": data.get("metadata", {}),
        }
        targets = self._build_target_payloads(data["targets"], request_payload)

        return {
            "_id": slice_id,
            "name": data["name"],
            "status": "created",
            "s_nssai": s_nssai,
            "plmn": plmn,
            "dnn": data["dnn"],
            "qos": data.get("qos", {}),
            "subscribers": data.get("subscribers", []),
            "isolation_mode": data.get("isolation_mode", "shared"),
            "request": request_payload,
            "targets": targets,
            "created_at": now,
            "updated_at": now,
        }

    def _normalize_s_nssai(self, s_nssai):
        normalized = {"sst": int(s_nssai["sst"])}
        sd = s_nssai.get("sd")
        if sd is not None:
            normalized["sd"] = str(sd).lower().replace("0x", "")
        return normalized

    def _normalize_plmn(self, plmn):
        if isinstance(plmn, str):
            return {"id": plmn}
        mcc = str(plmn["mcc"])
        mnc = str(plmn["mnc"])
        return {"id": f"{mcc}{mnc}", "mcc": mcc, "mnc": mnc}

    def _build_target_payloads(self, targets, request_payload):
        return [self._target_payload(target, request_payload) for target in targets]

    def _target_payload(self, target, request_payload):
        target_info = self._resolve_target(target)
        payload = dict(request_payload)
        payload["target_type"] = target_info["role"]
        payload["target_id"] = target_info.get("target_id")
        payload["component"] = target_info.get("component")
        payload["driver"] = target_info["driver"]
        payload["target_config"] = target_info.get("config", {})
        payload.update(target_info.get("payload", {}))
        if target.get("payload"):
            payload.update(target["payload"])

        return {
            "target_id": target_info.get("target_id"),
            "role": target_info["role"],
            "driver": target_info["driver"],
            "url": target_info["url"],
            "payload": payload,
        }

    def _resolve_target(self, target):
        if target.get("url"):
            return {
                "target_id": target.get("target_id"),
                "role": target["role"],
                "driver": target.get("driver", "generic-adapter"),
                "url": target["url"].rstrip("/"),
                "component": target.get("component", target["role"]),
                "config": target.get("config", {}),
                "payload": {},
            }

        registered_target = mongoUtils.find("network_targets", {"id": target["target_id"]})
        if not registered_target:
            raise ValueError(f"Network target '{target['target_id']}' not found")
        if not registered_target.get("enabled", True):
            raise ValueError(f"Network target '{target['target_id']}' is disabled")

        return {
            "target_id": registered_target["id"],
            "role": target.get("role", registered_target["role"]),
            "driver": target.get("driver", registered_target["driver"]),
            "url": registered_target["url"].rstrip("/"),
            "component": target.get("component", registered_target.get("component", registered_target["role"])),
            "config": registered_target.get("config", {}),
            "payload": registered_target.get("payload", {}),
        }

    def _apply_record(self, record):
        record["status"] = "applying"
        record["updated_at"] = time.time()
        results = []
        for target in record.get("targets", []):
            result = self.client.create_slice(target["url"], target["payload"])
            results.append(
                {
                    "target_id": target.get("target_id"),
                    "role": target.get("role"),
                    "driver": target.get("driver"),
                    "url": target.get("url"),
                    "result": result,
                }
            )

        record["apply_result"] = results
        record["status"] = "running" if all(item["result"]["ok"] for item in results) else "error"
        record["updated_at"] = time.time()
        return record

    def _delete_from_targets(self, record):
        results = []
        for target in record.get("targets", []):
            if not target.get("url"):
                continue
            result = self.client.delete_slice(target["url"], record["_id"])
            results.append(
                {
                    "target_id": target.get("target_id"),
                    "role": target.get("role"),
                    "driver": target.get("driver"),
                    "url": target.get("url"),
                    "result": result,
                }
            )
        return results
