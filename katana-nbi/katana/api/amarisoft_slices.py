import logging
from logging import handlers
import re
import time
import uuid

from bson.json_util import dumps
from flask import request
from flask_classful import FlaskView, route

from katana.shared_utils.emsUtils.amarisoft_sliceUtils import AmarisoftSliceClient
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


class AmarisoftSlicesView(FlaskView):
    """
    Direct Amarisoft network-slice API.

    This endpoint is intentionally narrower than Katana's descriptor-driven slice
    workflow. It models a logical 5G slice across one Amari CORE target and one
    Amari RAN target, with optional dry-run support for UI previews.
    """

    route_prefix = "/api"
    route_base = "/amarisoft-slices"
    collection = "amarisoft_slices"

    def __init__(self):
        self.client = AmarisoftSliceClient()

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
            slice_id = str(uuid.uuid4())
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
            return f"Error: No such Amarisoft slice: {uuid}", 404

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
            return f"Error: No such Amarisoft slice: {uuid}", 404

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
        if not isinstance(targets, dict):
            return "Error: Field 'targets' must be a JSON object", 400
        for target_name in ("ran", "core"):
            target = targets.get(target_name)
            if not isinstance(target, dict):
                return f"Error: Field 'targets.{target_name}' must be a JSON object", 400
            if not target.get("ems_id") and not target.get("url"):
                return f"Error: Field 'targets.{target_name}' requires ems_id or url", 400

        subscribers = data.get("subscribers", [])
        if subscribers is not None and not isinstance(subscribers, list):
            return "Error: Field 'subscribers' must be a list", 400

        qos = data.get("qos", {})
        if qos is not None and not isinstance(qos, dict):
            return "Error: Field 'qos' must be a JSON object", 400

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
        return {
            "ran": self._target_payload("ran", targets["ran"], request_payload),
            "core": self._target_payload("core", targets["core"], request_payload),
        }

    def _target_payload(self, target_type, target, request_payload):
        target_info = self._resolve_target(target)
        payload = dict(request_payload)
        payload["target_type"] = target_type
        payload["component"] = "amari-ran" if target_type == "ran" else "amari-core"
        if target.get("payload"):
            payload.update(target["payload"])

        return {
            "ems_id": target.get("ems_id"),
            "url": target_info["url"],
            "payload": payload,
        }

    def _resolve_target(self, target):
        if target.get("url"):
            return {"url": target["url"].rstrip("/")}

        ems = mongoUtils.find("ems", {"id": target["ems_id"]})
        if not ems:
            raise ValueError(f"EMS '{target['ems_id']}' not found")
        if ems.get("type") != "amarisoft-ems":
            raise ValueError(f"EMS '{target['ems_id']}' is not an amarisoft-ems")
        return {"url": ems["url"].rstrip("/")}

    def _apply_record(self, record):
        record["status"] = "applying"
        record["updated_at"] = time.time()
        results = {
            "ran": self.client.create_slice(
                record["targets"]["ran"]["url"],
                record["targets"]["ran"]["payload"],
            ),
            "core": self.client.create_slice(
                record["targets"]["core"]["url"],
                record["targets"]["core"]["payload"],
            ),
        }
        record["apply_result"] = results
        record["status"] = "running" if all(result["ok"] for result in results.values()) else "error"
        record["updated_at"] = time.time()
        return record

    def _delete_from_targets(self, record):
        results = {}
        for target_name in ("ran", "core"):
            target = record.get("targets", {}).get(target_name)
            if not target or not target.get("url"):
                continue
            results[target_name] = self.client.delete_slice(target["url"], record["_id"])
        return results
