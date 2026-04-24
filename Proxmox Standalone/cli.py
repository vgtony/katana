import argparse
import json
import sys
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


def load_yaml(path):
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def print_json(payload):
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser():
    parser = argparse.ArgumentParser(description="Standalone Proxmox VE integration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    test_parser = subparsers.add_parser("test", help="Test Proxmox VE connectivity")
    test_parser.add_argument("--cluster-file", required=True, help="Path to cluster YAML file")

    list_parser = subparsers.add_parser("list-vms", help="List VMs on the configured Proxmox node")
    list_parser.add_argument("--cluster-file", required=True, help="Path to cluster YAML file")
    list_parser.add_argument("--node", help="Override the node from the cluster YAML")

    overview_parser = subparsers.add_parser(
        "overview",
        help="Show cluster, node/server, VM, and resource usage overview",
    )
    overview_parser.add_argument("--cluster-file", required=True, help="Path to cluster YAML file")

    provision_parser = subparsers.add_parser("provision", help="Provision VMs from YAML")
    provision_parser.add_argument("--cluster-file", required=True, help="Path to cluster YAML file")
    provision_parser.add_argument("--vm-file", required=True, help="Path to VM definition YAML file")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        from proxmox_standalone.client import ProxmoxVEClient
        from proxmox_standalone.provisioner import ProxmoxProvisioner
        from proxmox_standalone.reporting import build_cluster_overview

        cluster_config = load_yaml(args.cluster_file)
        client = ProxmoxVEClient.from_config(cluster_config)

        if args.command == "test":
            payload = {
                "cluster": cluster_config.get("name"),
                "version": client.version(),
                "nodes": client.list_nodes(),
            }
            print_json(payload)
            return 0

        if args.command == "list-vms":
            node = args.node or cluster_config["node"]
            print_json({
                "cluster": cluster_config.get("name"),
                "node": node,
                "vms": client.list_vms(node),
            })
            return 0

        if args.command == "overview":
            print_json(build_cluster_overview(client, cluster_name=cluster_config.get("name", "")))
            return 0

        if args.command == "provision":
            vm_config = load_yaml(args.vm_file)
            provisioner = ProxmoxProvisioner(client, cluster_config)
            print_json(provisioner.provision_from_config(vm_config))
            return 0

        parser.error(f"Unsupported command: {args.command}")
        return 2
    except Exception as exc:
        print_json({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
