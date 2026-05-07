import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests


class ProxmoxAPIError(RuntimeError):
    pass


class TaskTimeoutError(ProxmoxAPIError):
    pass


class TaskFailedError(ProxmoxAPIError):
    pass


class ProxmoxVEClient:
    def __init__(
        self,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_token_id: Optional[str] = None,
        api_token_secret: Optional[str] = None,
        verify_ssl: bool = False,
        timeout: int = 30,
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.username = username
        self.password = password
        self.api_token_id = api_token_id
        self.api_token_secret = api_token_secret
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        self._csrf_token = None
        self._ticket = None

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ProxmoxVEClient":
        return cls(
            base_url=config["url"],
            username=config.get("username"),
            password=config.get("password"),
            api_token_id=config.get("api_token_id"),
            api_token_secret=config.get("api_token_secret"),
            verify_ssl=config.get("verify_ssl", False),
            timeout=config.get("timeout", 30),
        )

    def _normalize_base_url(self, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            return value.rstrip("/") + "/api2/json"
        if value.startswith("http://") or value.startswith("https://"):
            return value.rstrip("/") + "/api2/json"
        return f"https://{value.rstrip('/')}/api2/json"

    def _headers(self, method: str) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token_id and self.api_token_secret:
            headers["Authorization"] = (
                f"PVEAPIToken={self.api_token_id}={self.api_token_secret}"
            )
        elif method.upper() in {"POST", "PUT", "DELETE"} and self._csrf_token:
            headers["CSRFPreventionToken"] = self._csrf_token
        return headers

    def authenticate(self) -> None:
        if self.api_token_id and self.api_token_secret:
            return
        if self._ticket and self._csrf_token:
            return
        if not self.username or not self.password:
            raise ProxmoxAPIError(
                "Missing credentials. Provide username/password or api_token_id/api_token_secret."
            )

        response = self.session.post(
            f"{self.base_url}/access/ticket",
            data={"username": self.username, "password": self.password},
            timeout=self.timeout,
            verify=self.verify_ssl,
            headers={"Accept": "application/json"},
        )
        payload = self._decode_response(response, "POST", "access/ticket")
        self._ticket = payload["ticket"]
        self._csrf_token = payload["CSRFPreventionToken"]
        self.session.cookies.set("PVEAuthCookie", self._ticket)

    def _decode_response(self, response: requests.Response, method: str, path: str) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProxmoxAPIError(
                f"Unexpected response from Proxmox for {method.upper()} {path} "
                f"({response.status_code}): {response.text}"
            ) from exc

        if not response.ok:
            message = None
            if isinstance(payload, dict):
                errors = payload.get("errors")
                message = payload.get("message") or payload.get("error") or payload.get("data")
                if errors:
                    message = f"{message or 'Request failed'}: {errors}"
            if not message:
                message = response.text
            raise ProxmoxAPIError(
                f"Proxmox API request failed for {method.upper()} {path} "
                f"with status {response.status_code}: {message}"
            )

        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        self.authenticate()
        response = self.session.request(
            method=method.upper(),
            url=f"{self.base_url}/{path.lstrip('/')}",
            params=params,
            data=data,
            timeout=self.timeout,
            verify=self.verify_ssl,
            headers=self._headers(method),
        )
        return self._decode_response(response, method, path)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("POST", path, data=data)

    def put(self, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("PUT", path, data=data)

    def delete(self, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("DELETE", path, data=data)

    def version(self) -> Dict[str, Any]:
        return self.get("version")

    def cluster_status(self) -> Any:
        return self.get("cluster/status")

    def cluster_resources(self, resource_type: Optional[str] = None) -> Any:
        params = {"type": resource_type} if resource_type else None
        return self.get("cluster/resources", params=params)

    def list_nodes(self) -> Any:
        return self.get("nodes")

    def node_status(self, node: str) -> Any:
        return self.get(f"nodes/{node}/status")

    def list_vms(self, node: str) -> Any:
        return self.get(f"nodes/{node}/qemu")

    def list_storages(self, node: str) -> Any:
        return self.get(f"nodes/{node}/storage")

    def storage_content(
        self,
        node: str,
        storage: str,
        content: Optional[str] = None,
    ) -> Any:
        params = {"content": content} if content else None
        return self.get(f"nodes/{node}/storage/{storage}/content", params=params)

    def next_vmid(self) -> int:
        return int(self.get("cluster/nextid"))

    def clone_vm(
        self,
        node: str,
        template_vmid: int,
        new_vmid: int,
        name: str,
        storage: Optional[str] = None,
        pool: Optional[str] = None,
        target: Optional[str] = None,
        full: bool = True,
    ) -> str:
        payload: Dict[str, Any] = {
            "newid": new_vmid,
            "name": name,
            "full": int(full),
        }
        if storage:
            payload["storage"] = storage
        if pool:
            payload["pool"] = pool
        if target:
            payload["target"] = target
        return self.post(f"nodes/{node}/qemu/{int(template_vmid)}/clone", data=payload)

    def create_vm(
        self,
        node: str,
        vmid: int,
        name: str,
        memory: int,
        cores: int,
        scsihw: str = "virtio-scsi-pci",
        ostype: str = "l26",
        disk: Optional[str] = None,
        boot: Optional[str] = None,
        bootdisk: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        payload: Dict[str, Any] = {
            "vmid": int(vmid),
            "name": name,
            "memory": int(memory),
            "cores": int(cores),
            "scsihw": scsihw,
            "ostype": ostype,
        }
        if disk:
            payload["scsi0"] = disk
        if boot:
            payload["boot"] = boot
        if bootdisk:
            payload["bootdisk"] = bootdisk
        if extra_config:
            payload.update({key: value for key, value in extra_config.items() if value is not None})
        return self.post(f"nodes/{node}/qemu", data=payload)

    def update_vm_config(self, node: str, vmid: int, **kwargs: Any) -> Any:
        payload = {key: value for key, value in kwargs.items() if value is not None}
        return self.put(f"nodes/{node}/qemu/{int(vmid)}/config", data=payload)

    def resize_disk(self, node: str, vmid: int, disk: str, size: str) -> Any:
        return self.put(
            f"nodes/{node}/qemu/{int(vmid)}/resize",
            data={"disk": disk, "size": size},
        )

    def start_vm(self, node: str, vmid: int) -> Any:
        return self.post(f"nodes/{node}/qemu/{int(vmid)}/status/start")

    def vm_config(self, node: str, vmid: int) -> Any:
        return self.get(f"nodes/{node}/qemu/{int(vmid)}/config")

    def vm_network_interfaces(self, node: str, vmid: int) -> Any:
        return self.get(f"nodes/{node}/qemu/{int(vmid)}/agent/network-get-interfaces")

    def task_status(self, node: str, upid: str) -> Dict[str, Any]:
        return self.get(f"nodes/{node}/tasks/{upid}/status")

    def task_log(self, node: str, upid: str) -> Any:
        return self.get(f"nodes/{node}/tasks/{upid}/log")

    def wait_for_task(
        self,
        node: str,
        upid: str,
        timeout: int = 600,
        poll_interval: int = 2,
    ) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.task_status(node, upid)
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    raise TaskFailedError(
                        f"Task {upid} failed with exit status {status.get('exitstatus')}"
                    )
                return status
            time.sleep(poll_interval)
        raise TaskTimeoutError(f"Timed out waiting for Proxmox task {upid}")
