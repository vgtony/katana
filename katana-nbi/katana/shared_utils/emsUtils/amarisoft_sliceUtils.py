import json
import logging
from logging import handlers

import requests


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


class AmarisoftSliceClient:
    """
    Client for the small Amarisoft slice adapter API expected in front of Amari RAN
    and Amari CORE.

    Katana sends normalized slice intents to this adapter. The adapter can then map
    those intents into the exact Amarisoft config file/API operations used in a lab.
    """

    def __init__(self, timeout=360):
        self.timeout = timeout

    def create_slice(self, url, payload):
        return self._request("post", url, "/slice", payload)

    def delete_slice(self, url, slice_id):
        return self._request("delete", url, f"/slice/{slice_id}")

    def _request(self, method, base_url, path, payload=None):
        endpoint = self._join_url(base_url, path)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            response = requests.request(
                method,
                endpoint,
                json=json.loads(json.dumps(payload)) if payload is not None else None,
                timeout=self.timeout,
                headers=headers,
            )
            response.raise_for_status()
            return {
                "ok": True,
                "url": endpoint,
                "status_code": response.status_code,
                "body": self._response_body(response),
            }
        except requests.exceptions.RequestException as exc:
            logger.exception("Amarisoft slice request failed")
            status_code = exc.response.status_code if exc.response is not None else None
            body = self._response_body(exc.response) if exc.response is not None else None
            return {
                "ok": False,
                "url": endpoint,
                "status_code": status_code,
                "error": str(exc),
                "body": body,
            }

    def _join_url(self, base_url, path):
        return base_url.rstrip("/") + path

    def _response_body(self, response):
        if response is None:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text
