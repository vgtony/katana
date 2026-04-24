from typing import Any, Dict, List

from .client import ProxmoxVEClient


def _format_bytes(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{value} B"


def _resource_block(used: float, total: float, unit: str = "bytes") -> Dict[str, Any]:
    free = max(total - used, 0)
    used_percent = (used / total * 100.0) if total else 0.0
    block = {
        "used": used,
        "total": total,
        "free": free,
        "used_percent": round(used_percent, 2),
    }
    if unit == "bytes":
        block.update(
            {
                "used_human": _format_bytes(used),
                "total_human": _format_bytes(total),
                "free_human": _format_bytes(free),
            }
        )
    return block


def _cpu_block(cpu_fraction: float, total_cores: float) -> Dict[str, Any]:
    used_cores = cpu_fraction * total_cores
    free_cores = max(total_cores - used_cores, 0)
    return {
        "used_fraction": round(cpu_fraction, 4),
        "used_percent": round(cpu_fraction * 100.0, 2),
        "used_cores_estimate": round(used_cores, 2),
        "total_cores": total_cores,
        "free_cores_estimate": round(free_cores, 2),
    }


def build_cluster_overview(
    client: ProxmoxVEClient,
    cluster_name: str = "",
) -> Dict[str, Any]:
    version = client.version()
    cluster_status = client.cluster_status()
    cluster_resources = client.cluster_resources()

    node_entries = [item for item in cluster_resources if item.get("type") == "node"]
    vm_entries = [item for item in cluster_resources if item.get("type") == "qemu"]

    online_nodes = [item for item in node_entries if item.get("status") == "online"]
    running_vms = [item for item in vm_entries if item.get("status") == "running"]

    total_node_mem = sum(float(item.get("maxmem", 0)) for item in node_entries)
    used_node_mem = sum(float(item.get("mem", 0)) for item in node_entries)
    total_node_disk = sum(float(item.get("maxdisk", 0)) for item in node_entries)
    used_node_disk = sum(float(item.get("disk", 0)) for item in node_entries)
    total_node_cpu = sum(float(item.get("maxcpu", 0)) for item in node_entries)
    used_node_cpu_cores = sum(
        float(item.get("cpu", 0)) * float(item.get("maxcpu", 0)) for item in node_entries
    )

    total_vm_mem = sum(float(item.get("maxmem", 0)) for item in vm_entries)
    used_vm_mem = sum(float(item.get("mem", 0)) for item in vm_entries)
    total_vm_disk = sum(float(item.get("maxdisk", 0)) for item in vm_entries)
    used_vm_disk = sum(float(item.get("disk", 0)) for item in vm_entries)
    total_vm_cpu = sum(float(item.get("maxcpu", 0)) for item in vm_entries)
    used_vm_cpu_cores = sum(
        float(item.get("cpu", 0)) * float(item.get("maxcpu", 0)) for item in vm_entries
    )

    nodes: List[Dict[str, Any]] = []
    for item in sorted(node_entries, key=lambda entry: entry.get("node", "")):
        total_cores = float(item.get("maxcpu", 0))
        cpu_fraction = float(item.get("cpu", 0))
        used_mem = float(item.get("mem", 0))
        total_mem = float(item.get("maxmem", 0))
        used_disk = float(item.get("disk", 0))
        total_disk = float(item.get("maxdisk", 0))

        nodes.append(
            {
                "node": item.get("node"),
                "status": item.get("status"),
                "uptime_seconds": item.get("uptime"),
                "cpu": _cpu_block(cpu_fraction, total_cores),
                "memory": _resource_block(used_mem, total_mem),
                "disk": _resource_block(used_disk, total_disk),
                "ssl_fingerprint": item.get("ssl_fingerprint"),
                "level": item.get("level"),
            }
        )

    vms: List[Dict[str, Any]] = []
    for item in sorted(vm_entries, key=lambda entry: (entry.get("node", ""), entry.get("vmid", 0))):
        total_cores = float(item.get("maxcpu", 0))
        cpu_fraction = float(item.get("cpu", 0))
        used_mem = float(item.get("mem", 0))
        total_mem = float(item.get("maxmem", 0))
        used_disk = float(item.get("disk", 0))
        total_disk = float(item.get("maxdisk", 0))

        vms.append(
            {
                "vmid": item.get("vmid"),
                "name": item.get("name"),
                "node": item.get("node"),
                "status": item.get("status"),
                "template": item.get("template", 0) == 1,
                "tags": item.get("tags"),
                "cpu": _cpu_block(cpu_fraction, total_cores),
                "memory": _resource_block(used_mem, total_mem),
                "disk": _resource_block(used_disk, total_disk),
                "uptime_seconds": item.get("uptime"),
            }
        )

    return {
        "cluster": {
            "name": cluster_name or None,
            "version": version,
            "status": cluster_status,
            "summary": {
                "node_count": len(node_entries),
                "online_node_count": len(online_nodes),
                "vm_count": len(vm_entries),
                "running_vm_count": len(running_vms),
            },
            "physical_resources": {
                "cpu": {
                    "used_cores_estimate": round(used_node_cpu_cores, 2),
                    "total_cores": total_node_cpu,
                    "free_cores_estimate": round(max(total_node_cpu - used_node_cpu_cores, 0), 2),
                    "used_percent": round((used_node_cpu_cores / total_node_cpu * 100.0), 2)
                    if total_node_cpu
                    else 0.0,
                },
                "memory": _resource_block(used_node_mem, total_node_mem),
                "disk": _resource_block(used_node_disk, total_node_disk),
            },
            "vm_resources": {
                "cpu": {
                    "used_cores_estimate": round(used_vm_cpu_cores, 2),
                    "allocated_cores": total_vm_cpu,
                    "free_vs_allocated_cores_estimate": round(max(total_vm_cpu - used_vm_cpu_cores, 0), 2),
                    "used_percent_of_allocated": round((used_vm_cpu_cores / total_vm_cpu * 100.0), 2)
                    if total_vm_cpu
                    else 0.0,
                },
                "memory": {
                    **_resource_block(used_vm_mem, total_vm_mem),
                    "label": "VM allocated memory consumption",
                },
                "disk": {
                    **_resource_block(used_vm_disk, total_vm_disk),
                    "label": "VM allocated disk consumption",
                },
            },
        },
        "servers": nodes,
        "vms": vms,
    }
