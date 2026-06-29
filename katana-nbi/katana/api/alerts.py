import logging
import os
import time
from logging import handlers
from flask import request
from flask_classful import FlaskView
from time import sleep

from katana.shared_utils.mongoUtils import mongoUtils
from katana.shared_utils.sliceUtils.sliceUtils import check_runtime_errors
from katana.shared_utils.kafkaUtils.kafkaUtils import create_producer

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


def _utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _store_alert(alert, status):
    labels = alert.get("labels", {})
    slice_id = labels.get("slice_id")
    doc = {
        "slice_id": slice_id,
        "alertname": labels.get("alertname"),
        "status": status,
        "labels": labels,
        "annotations": alert.get("annotations", {}),
        "startsAt": alert.get("startsAt"),
        "endsAt": alert.get("endsAt"),
        "generatorURL": alert.get("generatorURL"),
        "received_at": time.time(),
        "received_at_iso": _utc_timestamp(),
    }
    mongoUtils.add("alerts", doc)
    return doc


class AlertView(FlaskView):
    route_prefix = "/api/"

    def post(self):
        """
        Get a new alert
        """
        alert_message = request.get_json(silent=True) or {}
        alerts = alert_message.get("alerts", [])
        if not isinstance(alerts, list):
            return "Error: alerts must be a list", 400

        stored_alerts = []
        # Check the alert type
        for ialert in alerts:
            if not isinstance(ialert, dict):
                logger.warning(f"Malformed alert item: {ialert}")
                continue
            stored_alerts.append(_store_alert(ialert, alert_message.get("status")))
            labels = ialert.get("labels", {})
            if labels.get("alertname") == "NSFailing":
                try:
                    ns_id = labels["ns_name"].split("__")[1].replace("_", "-")
                    location = labels["ns_name"].split("__")[2]
                    slice_id = labels["slice_id"]
                except (KeyError, IndexError):
                    logger.warning(f"Malformed NSFailing alert labels: {labels}")
                    continue
                logger.warning(
                    f"Failing Network Service {ns_id} in {location} for slice {slice_id}"
                )
                # Update the NEST
                nest = mongoUtils.get("slice", slice_id)
                if not nest:
                    logger.warning(f"Slice {slice_id} not found for received alert")
                    continue
                try:
                    nest["ns_inst_info"][ns_id][location]["status"] = "Error"
                except KeyError:
                    pass
                # Add the error to the runtime errors
                runtime_errors = nest.get("runtime_errors", {})
                ns_errors = runtime_errors.get("ns", [])
                ns_errors.append(ns_id)
                runtime_errors["ns"] = ns_errors
                nest["runtime_errors"] = runtime_errors
                check_runtime_errors(nest)
                # Notify APEX
                isapex = os.getenv("APEX", None)
                if isapex:
                    apex_message = {
                        "name": "SMAlert",
                        "nameSpace": "sm.alert.manager.events",
                        "version": "0.0.1",
                        "source": "SMAlertManager",
                        "target": "APEX",
                        "sliceId": slice_id,
                        "alertType": "FailingNS",
                        "alertMessage": {"NS_ID": ns_id, "NSD_ID": location, "status": "down"},
                    }
                    sleep(10)
                    apex_producer = create_producer()
                    logger.info(f"Sending alert to APEX {apex_message}")
                    apex_producer.send("apex-in-0", value=apex_message)
        return {"message": "Alert received", "stored_alerts": len(stored_alerts)}, 200
