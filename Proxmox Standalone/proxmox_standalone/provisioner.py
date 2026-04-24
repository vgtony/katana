import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .client import ProxmoxVEClient


logger = logging.getLogger(__name__)


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


@dataclass
class VmSpec:
    name: str
    template: int
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VmSpec":
        required = ["name", "template", "cpu", "ram", "storage_type", "disk_size", "bridges"]
        missing = [field for field in required if field not in data]
        if missing:
            raise ValueError(f"VM '{data.get('name', 'unknown')}' is missing: {', '.join(missing)}")

        bridges = [BridgeSpec.from_dict(item) for item in data["bridges"]]
        if not bridges:
            raise ValueError(f"VM '{data['name']}' must define at least one bridge")

        return cls(
            name=data["name"],
            template=int(data["template"]),
            cpu=int(data["cpu"]),
            ram=int(data["ram"]),
            storage_type=data["storage_type"],
            disk_size=int(data["disk_size"]),
            bridges=bridges,
            pool=data.get("pool"),
            target=data.get("target"),
            start=data.get("start", True),
            full_clone=data.get("full_clone", True),
            disk_name=data.get("disk_name", "scsi0"),
            cloud_init=data.get("cloud_init", {}),
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
        clone_task = self.client.clone_vm(
            node=self.node,
            template_vmid=vm.template,
            new_vmid=vmid,
            name=vm.name,
            storage=vm.storage_type,
            pool=vm.pool,
            target=vm.target,
            full=vm.full_clone,
        )
        self.client.wait_for_task(self.node, clone_task)

        self.client.update_vm_config(self.node, vmid, cores=vm.cpu, memory=vm.ram)

        if vm.disk_size > 0:
            try:
                self.client.resize_disk(self.node, vmid, vm.disk_name, f"{vm.disk_size}G")
            except Exception as exc:
                logger.warning("Disk resize for VM %s failed: %s", vm.name, exc)

        nic_config = self._build_nic_config(vm.bridges)
        if nic_config:
            self.client.update_vm_config(self.node, vmid, **nic_config)

        cloud_init_config, warnings = self._build_cloud_init_config(vm.bridges, vm.cloud_init)
        if cloud_init_config:
            self.client.update_vm_config(self.node, vmid, **cloud_init_config)

        start_task = None
        if vm.start:
            start_task = self.client.start_vm(self.node, vmid)
            self.client.wait_for_task(self.node, start_task)

        return {
            "name": vm.name,
            "vmid": vmid,
            "template": vm.template,
            "node": self.node,
            "status": "created",
            "started": vm.start,
            "clone_task": clone_task,
            "start_task": start_task,
            "warnings": warnings,
            "bridges": [bridge.__dict__ for bridge in vm.bridges],
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
