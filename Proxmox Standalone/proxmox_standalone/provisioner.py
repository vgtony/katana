import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .client import ProxmoxAPIError, ProxmoxVEClient


logger = logging.getLogger(__name__)

BRIDGE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[0-9]+)?$")


@dataclass
class BridgeSpec:
    name: str
    type: str
    ip: Optional[str] = None
    netmask: Optional[str] = None
    gateway: Optional[str] = None
    default_gateway: bool = False
    model: str = "virtio"
    firewall: bool = False
    mtu: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BridgeSpec":
        bridge = cls(
            name=data["name"],
            type=data["type"],
            ip=data.get("ip"),
            netmask=data.get("netmask"),
            gateway=data.get("gateway"),
            default_gateway=data.get("default_gateway", False),
            model=data.get("model", "virtio"),
            firewall=data.get("firewall", False),
            mtu=data.get("mtu"),
        )
        bridge.validate()
        return bridge

    def validate(self) -> None:
        self._validate_name()

        if self.type not in {"management", "custom"}:
            raise ValueError(f"Unsupported bridge type '{self.type}' for bridge '{self.name}'")

        if self.type == "custom":
            for required_field in ("ip", "netmask"):
                if not getattr(self, required_field):
                    raise ValueError(
                        f"Bridge '{self.name}' is missing required field '{required_field}'"
                    )
            ipaddress.ip_address(self.ip)
            ipaddress.IPv4Network(f"0.0.0.0/{self.netmask}", strict=False)
            if self.gateway:
                ipaddress.ip_address(self.gateway)

    def _validate_name(self) -> None:
        if not self.name:
            raise ValueError("Bridge name is required")
        if ":" in self.name:
            raise ValueError(
                f"Bridge '{self.name}' is invalid. Use 'vmbr1' or 'vmbr1.<vlan_id>', not ':'."
            )
        if not BRIDGE_NAME_RE.match(self.name):
            raise ValueError(
                f"Bridge '{self.name}' is invalid. Allowed format is bridge or bridge.<vlan_id>."
            )
        if "." in self.name:
            _, vlan = self.name.split(".", 1)
            vlan_id = int(vlan)
            if vlan_id < 1 or vlan_id > 4094:
                raise ValueError(
                    f"Bridge '{self.name}' has invalid VLAN '{vlan}'. VLAN must be 1-4094."
                )


@dataclass
class VmSpec:
    name: str
    template: Optional[int]
    cpu: int
    ram: int
    storage_type: str
    disk_size: int
    bridges: List[BridgeSpec] = field(default_factory=list)
    pool: Optional[str] = None
    target: Optional[str] = None
    start: bool = True
    full_clone: bool = True
    disk_name: str = "scsi0"
    cloud_init: Dict[str, Any] = field(default_factory=dict)
    ostype: str = "l26"
    scsihw: str = "virtio-scsi-pci"
    boot: Optional[str] = None
    bootdisk: str = "scsi0"
    agent: Optional[int] = 1
    onboot: Optional[int] = 1
    tags: Optional[str] = None
    description: Optional[str] = None
    wait_for_ip: bool = False
    ip_wait_timeout: int = 120
    ip_poll_interval: int = 5
    iso_image: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VmSpec":
        if not isinstance(data, dict):
            raise ValueError("Each VM definition must be an object")

        required = ["name", "cpu", "ram", "storage_type", "disk_size", "bridges"]
        missing = [field for field in required if field not in data]
        if missing:
            raise ValueError(f"VM '{data.get('name', 'unknown')}' is missing: {', '.join(missing)}")

        template = data.get("template")
        if template == "":
            template = None
        if template is not None:
            template = int(template)
            if template <= 0:
                raise ValueError(f"VM '{data['name']}' template must be a positive VMID")
        elif not data.get("iso_image"):
            raise ValueError(
                f"VM '{data['name']}' is a fresh VM and must include iso_image"
            )

        cpu = int(data["cpu"])
        ram = int(data["ram"])
        disk_size = int(data["disk_size"])
        if cpu <= 0:
            raise ValueError(f"VM '{data['name']}' cpu must be greater than 0")
        if ram <= 0:
            raise ValueError(f"VM '{data['name']}' ram must be greater than 0")
        if disk_size <= 0:
            raise ValueError(f"VM '{data['name']}' disk_size must be greater than 0")

        bridges = [BridgeSpec.from_dict(item) for item in data["bridges"]]
        if not bridges:
            raise ValueError(f"VM '{data['name']}' must define at least one bridge")

        return cls(
            name=data["name"],
            template=template,
            cpu=cpu,
            ram=ram,
            storage_type=data["storage_type"],
            disk_size=disk_size,
            bridges=bridges,
            pool=data.get("pool"),
            target=data.get("target"),
            start=data.get("start", True),
            full_clone=data.get("full_clone", True),
            disk_name=data.get("disk_name", "scsi0"),
            cloud_init=data.get("cloud_init", {}),
            ostype=data.get("ostype", "l26"),
            scsihw=data.get("scsihw", "virtio-scsi-pci"),
            boot=data.get("boot"),
            bootdisk=data.get("bootdisk", "scsi0"),
            agent=data.get("agent", 1),
            onboot=data.get("onboot", 1),
            tags=data.get("tags"),
            description=data.get("description"),
            wait_for_ip=data.get("wait_for_ip", False),
            ip_wait_timeout=int(data.get("ip_wait_timeout", 120)),
            ip_poll_interval=int(data.get("ip_poll_interval", 5)),
            iso_image=data.get("iso_image"),
        )


class ProxmoxProvisioner:
    def __init__(self, client: ProxmoxVEClient, cluster_config: Dict[str, Any]) -> None:
        self.client = client
        self.cluster_config = cluster_config
        self.node = cluster_config["node"]

    def provision_from_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        vm_defs = config.get("vms")
        if not isinstance(vm_defs, list) or not vm_defs:
            raise ValueError("VM config must include a non-empty 'vms' list")

        results = []
        for item in vm_defs:
            spec = VmSpec.from_dict(item)
            results.append(self.provision_vm(spec))

        return {
            "cluster": self.cluster_config.get("name"),
            "node": self.node,
            "vm_count": len(results),
            "results": results,
        }

    def provision_vm(self, vm: VmSpec) -> Dict[str, Any]:
        vmid = self.client.next_vmid()
        clone_task = None
        create_task = None
        source = "template-clone" if vm.template is not None else "fresh-create"
        warnings: List[str] = []
        storage_id = self._storage_id(vm.storage_type)
        source_node = self.node
        deployment_node = vm.target or self.node
        switch_to_disk_boot_after_start = False

        if vm.template is not None:
            self._validate_template(source_node, vm.template)
            clone_task = self.client.clone_vm(
                node=source_node,
                template_vmid=vm.template,
                new_vmid=vmid,
                name=vm.name,
                storage=storage_id,
                pool=vm.pool,
                target=vm.target,
                full=vm.full_clone,
            )
            self.client.wait_for_task(source_node, clone_task)
        else:
            self._validate_iso_image(deployment_node, vm.iso_image)
            switch_to_disk_boot_after_start = vm.boot is None
            create_task = self.client.create_vm(
                node=deployment_node,
                vmid=vmid,
                name=vm.name,
                memory=vm.ram,
                cores=vm.cpu,
                scsihw=vm.scsihw,
                ostype=vm.ostype,
                disk=f"{storage_id}:{vm.disk_size}",
                boot=vm.boot or f"order=ide2;{vm.bootdisk}",
                bootdisk=vm.bootdisk,
                extra_config={
                    **self._optional_vm_config(vm),
                    "ide2": f"{vm.iso_image},media=cdrom",
                },
            )
            self.client.wait_for_task(deployment_node, create_task)
            warnings.append(
                "Fresh VM was created with an empty disk and the selected ISO attached as cdrom."
            )

        self.client.update_vm_config(
            deployment_node,
            vmid,
            cores=vm.cpu,
            memory=vm.ram,
            **self._optional_vm_config(vm),
        )

        if vm.template is not None and vm.disk_size > 0:
            try:
                self.client.resize_disk(deployment_node, vmid, vm.disk_name, f"{vm.disk_size}G")
            except Exception as exc:
                warnings.append(f"Disk resize failed for {vm.disk_name}: {exc}")
                logger.warning("Disk resize for VM %s failed: %s", vm.name, exc)

        nic_config = self._build_nic_config(vm.bridges)
        if nic_config:
            self.client.update_vm_config(deployment_node, vmid, **nic_config)

        cloud_init_config, cloud_init_warnings = self._build_cloud_init_config(
            vm.bridges,
            vm.cloud_init,
        )
        warnings.extend(cloud_init_warnings)
        if cloud_init_config:
            self.client.update_vm_config(deployment_node, vmid, **cloud_init_config)

        start_task = None
        if vm.start:
            start_task = self.client.start_vm(deployment_node, vmid)
            self.client.wait_for_task(deployment_node, start_task)
            if switch_to_disk_boot_after_start:
                self.client.update_vm_config(
                    deployment_node,
                    vmid,
                    boot=f"order={vm.bootdisk};ide2",
                )
                warnings.append(
                    "Fresh VM booted from ISO for first start; future boots prefer disk."
                )

        ip_result = self._resolve_vm_ips(deployment_node, vmid, vm, warnings)

        return {
            "name": vm.name,
            "vmid": vmid,
            "template": vm.template,
            "source": source,
            "storage": storage_id,
            "iso_image": vm.iso_image,
            "node": deployment_node,
            "source_node": source_node if vm.template is not None else deployment_node,
            "status": "created",
            "started": vm.start,
            "clone_task": clone_task,
            "create_task": create_task,
            "start_task": start_task,
            "primary_ip": ip_result["primary_ip"],
            "ip_addresses": ip_result["ip_addresses"],
            "ip_status": ip_result["status"],
            "network_interfaces": ip_result["interfaces"],
            "warnings": warnings,
            "bridges": [bridge.__dict__ for bridge in vm.bridges],
        }

    def _storage_id(self, storage_type: str) -> str:
        if not storage_type:
            raise ValueError("storage_type is required")
        return str(storage_type).split(":", 1)[0]

    def _optional_vm_config(self, vm: VmSpec) -> Dict[str, Any]:
        return {
            "agent": vm.agent,
            "onboot": vm.onboot,
            "tags": vm.tags,
            "description": vm.description,
        }

    def _validate_template(self, node: str, template_vmid: int) -> None:
        try:
            template_config = self.client.vm_config(node, template_vmid)
        except ProxmoxAPIError as exc:
            raise ValueError(
                f"Template VMID {template_vmid} was not found on node '{node}'. "
                "Use a VM/template ID from Proxmox inventory, not the Proxmox API port."
            ) from exc

        if not template_config:
            raise ValueError(
                f"Template VMID {template_vmid} returned an empty config on node '{node}'."
            )

    def _validate_iso_image(self, node: str, iso_image: Optional[str]) -> None:
        if not iso_image:
            raise ValueError("Fresh VM creation requires iso_image")
        if ":" not in iso_image:
            raise ValueError(
                f"ISO image '{iso_image}' is invalid. Expected Proxmox volid like 'local:iso/file.iso'."
            )

        storage, _ = iso_image.split(":", 1)
        try:
            iso_items = self.client.storage_content(node, storage, content="iso")
        except ProxmoxAPIError as exc:
            raise ValueError(
                f"Could not list ISO images on storage '{storage}' for node '{node}': {exc}"
            ) from exc

        for item in iso_items:
            if item.get("volid") == iso_image:
                return

        raise ValueError(
            f"ISO image '{iso_image}' was not found on storage '{storage}' for node '{node}'."
        )

    def _resolve_vm_ips(
        self,
        node: str,
        vmid: int,
        vm: VmSpec,
        warnings: List[str],
    ) -> Dict[str, Any]:
        if not vm.start:
            return {
                "status": "not_started",
                "primary_ip": None,
                "ip_addresses": [],
                "interfaces": [],
            }

        if not vm.wait_for_ip:
            return {
                "status": "skipped",
                "primary_ip": None,
                "ip_addresses": [],
                "interfaces": [],
            }

        deadline = time.time() + max(vm.ip_wait_timeout, 0)
        last_error = None

        while time.time() <= deadline:
            try:
                interfaces = self.client.vm_network_interfaces(node, vmid)
                parsed = self._parse_guest_interfaces(interfaces)
                if parsed["ip_addresses"]:
                    return {
                        "status": "ready",
                        "primary_ip": parsed["ip_addresses"][0],
                        "ip_addresses": parsed["ip_addresses"],
                        "interfaces": parsed["interfaces"],
                    }
            except ProxmoxAPIError as exc:
                last_error = exc

            time.sleep(max(vm.ip_poll_interval, 1))

        if last_error:
            warnings.append(
                "VM IP was not available from QEMU guest agent before timeout: "
                f"{last_error}"
            )
        else:
            warnings.append("VM IP was not available from QEMU guest agent before timeout.")

        return {
            "status": "pending",
            "primary_ip": None,
            "ip_addresses": [],
            "interfaces": [],
        }

    def _parse_guest_interfaces(self, payload: Any) -> Dict[str, Any]:
        interfaces = payload.get("result", payload) if isinstance(payload, dict) else payload
        if not isinstance(interfaces, list):
            return {"ip_addresses": [], "interfaces": []}

        parsed_interfaces = []
        ip_addresses = []

        for interface in interfaces:
            if not isinstance(interface, dict):
                continue
            name = interface.get("name")
            addresses = []
            for item in interface.get("ip-addresses", []):
                if item.get("ip-address-type") != "ipv4":
                    continue
                ip_address = item.get("ip-address")
                if not ip_address or ip_address.startswith("127.") or ip_address.startswith("169.254."):
                    continue
                addresses.append(ip_address)
                ip_addresses.append(ip_address)
            if addresses:
                parsed_interfaces.append({"name": name, "ipv4": addresses})

        return {
            "ip_addresses": ip_addresses,
            "interfaces": parsed_interfaces,
        }

    def _build_nic_config(self, bridges: List[BridgeSpec]) -> Dict[str, str]:
        config: Dict[str, str] = {}
        for index, bridge in enumerate(bridges):
            bridge_name, vlan_tag = self._split_bridge(bridge.name)
            parts = [bridge.model, f"bridge={bridge_name}"]
            if vlan_tag:
                parts.append(f"tag={vlan_tag}")
            if bridge.firewall:
                parts.append("firewall=1")
            if bridge.mtu:
                parts.append(f"mtu={bridge.mtu}")
            config[f"net{index}"] = ",".join(parts)
        return config

    def _build_cloud_init_config(
        self,
        bridges: List[BridgeSpec],
        cloud_init: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[str]]:
        config: Dict[str, Any] = {}
        warnings: List[str] = []
        has_management = any(bridge.type == "management" for bridge in bridges)
        gateway_assigned = False

        for index, bridge in enumerate(bridges):
            key = f"ipconfig{index}"
            if bridge.type == "management":
                config[key] = "ip=dhcp"
                continue

            cidr = ipaddress.IPv4Network(f"0.0.0.0/{bridge.netmask}", strict=False).prefixlen
            value = f"ip={bridge.ip}/{cidr}"

            should_use_gateway = bool(
                bridge.gateway and (
                    bridge.default_gateway or (not has_management and not gateway_assigned)
                )
            )
            if should_use_gateway:
                value = f"{value},gw={bridge.gateway}"
                gateway_assigned = True
            elif bridge.gateway and not should_use_gateway:
                warnings.append(
                    f"Gateway on bridge '{bridge.name}' was ignored to avoid multiple default routes."
                )

            config[key] = value

        if cloud_init.get("ciuser"):
            config["ciuser"] = cloud_init["ciuser"]
        if cloud_init.get("cipassword"):
            config["cipassword"] = cloud_init["cipassword"]
        if cloud_init.get("sshkeys"):
            config["sshkeys"] = cloud_init["sshkeys"]
        if cloud_init.get("nameserver"):
            config["nameserver"] = cloud_init["nameserver"]
        if cloud_init.get("searchdomain"):
            config["searchdomain"] = cloud_init["searchdomain"]

        return config, warnings

    def _split_bridge(self, bridge_name: str) -> Tuple[str, Optional[str]]:
        if "." not in bridge_name:
            return bridge_name, None
        base, vlan = bridge_name.split(".", 1)
        return base, vlan
