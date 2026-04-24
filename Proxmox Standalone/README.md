# Proxmox Standalone

Standalone Proxmox VE API integration for this repository.

This module is intentionally separate from the current Flask/Mongo-based Katana Proxmox code so you can reuse it from scripts, services, or wire it back into the app later.

## What it does

- Connects to the Proxmox VE REST API directly
- Supports either password login or API token authentication
- Retrieves cluster, node, and VM inventory
- Reports current resource usage and remaining capacity
- Clones VMs from an existing Proxmox template
- Updates CPU, RAM, disk, and NIC configuration
- Applies basic cloud-init IP configuration
- Starts the VM and returns a deployment summary

## Folder layout

- `cli.py`: command-line entry point
- `server.py`: standalone HTTP API for curl-based testing
- `requirements.txt`: standalone dependencies
- `example_cluster.yaml`: cluster/auth example
- `example_vms.yaml`: VM provisioning example
- `proxmox_standalone/client.py`: low-level Proxmox VE API client
- `proxmox_standalone/provisioner.py`: VM provisioning workflow
- `proxmox_standalone/reporting.py`: cluster/node/vm usage reporting

## Install

```bash
python3 -m pip install -r "Proxmox Standalone/requirements.txt"
```

## Cluster config

Use either `username` + `password` or `api_token_id` + `api_token_secret`.

```yaml
name: "main-proxmox-cluster"
url: "https://10.0.0.10:8006"
node: "pve-node-01"
verify_ssl: false

username: "root@pam"
password: "change-me"

# Optional token-based auth instead of password auth
# api_token_id: "root@pam!katana"
# api_token_secret: "replace-with-secret"
```

## VM config

The VM schema is close to the existing repo format.

```yaml
vms:
  - name: "katana-vm-1"
    template: 9000
    cpu: 4
    ram: 4096
    storage_type: "local-lvm"
    disk_size: 20
    start: true
    cloud_init:
      ciuser: "ubuntu"
      cipassword: "ubuntu"
      nameserver: "8.8.8.8"
    bridges:
      - name: "vmbr0"
        type: "management"
      - name: "vmbr1.1601"
        type: "custom"
        ip: "192.168.50.10"
        netmask: "255.255.255.0"
        gateway: "192.168.50.1"
        default_gateway: true
```

## Commands

Test the connection:

```bash
python3 "Proxmox Standalone/cli.py" test \
  --cluster-file "Proxmox Standalone/example_cluster.yaml"
```

Get cluster, servers, VMs, usage, and remaining resources:

```bash
python3 "Proxmox Standalone/cli.py" overview \
  --cluster-file "Proxmox Standalone/example_cluster.yaml"
```

List VMs on the configured node:

```bash
python3 "Proxmox Standalone/cli.py" list-vms \
  --cluster-file "Proxmox Standalone/example_cluster.yaml"
```

Provision VMs from YAML:

```bash
python3 "Proxmox Standalone/cli.py" provision \
  --cluster-file "Proxmox Standalone/example_cluster.yaml" \
  --vm-file "Proxmox Standalone/example_vms.yaml"
```

## HTTP API

Start the standalone server:

```bash
python3 "Proxmox Standalone/server.py" --host 127.0.0.1 --port 8099
```

Health check:

```bash
curl http://127.0.0.1:8099/health
```

List available endpoints:

```bash
curl http://127.0.0.1:8099/api
```

Test Proxmox authentication:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/test \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

UI-friendly connect endpoint:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/connect \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

The `overview` response now has a cleaner app-friendly shape:

```json
{
  "clusters": [],
  "servers": [],
  "vms": [],
  "usage": {},
  "remaining_resources": {}
}
```

Get cluster, servers, VMs, usage, and remaining resources:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/overview \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

Get only cluster metadata:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/clusters \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

Get only servers/nodes:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

Get only VMs:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/vms \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

Get only usage:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/usage \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

Get only remaining resources:

```bash
curl -X POST http://127.0.0.1:8099/api/proxmox/remaining-resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

List VMs for a specific node:

```bash
curl -X POST "http://127.0.0.1:8099/api/proxmox/list-vms?node=pve-node-01" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-proxmox-cluster",
    "url": "https://10.0.0.10:8006",
    "node": "pve-node-01",
    "verify_ssl": false,
    "username": "root@pam",
    "password": "change-me"
  }'
```

## How provisioning works

1. The client authenticates to Proxmox VE using either `/access/ticket` or an API token.
2. For each VM, it asks Proxmox for the next free VMID.
3. It clones the source template with `/nodes/{node}/qemu/{template}/clone`.
4. It waits for the asynchronous Proxmox task to finish.
5. It updates CPU, memory, disk, and NICs through `/config` and `/resize`.
6. It applies basic cloud-init network settings with `ipconfigN`.
7. It optionally starts the VM.

## Notes

- The `overview` command uses Proxmox cluster and resource endpoints to summarize:
  - `clusters`: cluster metadata and summary
  - `servers`: Proxmox nodes with per-node usage and remaining resources
  - `vms`: VM inventory with per-VM usage and allocated resources
  - `usage`: cluster-wide physical and VM-estate usage
  - `remaining_resources`: cluster-wide and per-server remaining capacity
- The standalone HTTP server sends permissive CORS headers so a browser-based UI can call it directly during testing.
- This integration focuses on the Proxmox VE side of provisioning.
- For multi-homed guests, cloud-init can set multiple IPs, but only one interface should own the default gateway. Use `default_gateway: true` on the interface that should carry it.
- If your template does not support cloud-init, VM creation still works, but guest-side IP setup will need to happen inside the VM.
