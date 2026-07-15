# Unified NEST Infrastructure Registration

## Summary

Extend `katana slice add -f slice.yaml` so one main YAML can optionally register missing OpenStack or Kubernetes infrastructure before creating the slice.

The workflow remains simple:

1. Katana identifies the platform from the referenced NSDs.
2. It reuses compatible registered infrastructure when available.
3. If an `infrastructure` section is supplied and its ID is missing, Katana registers it first.
4. Katana deploys through the existing slice lifecycle.

## Public configuration

Existing NEST files remain valid. Kubernetes can be registered inline with the slice request:

```yaml
infrastructure:
  id: edge-k8s-1
  type: kubernetes
  location: edge
  nfvo_id: osm-1
  credentials_file: ./creds.yaml
  k8s_version: v1.30.7
```

OpenStack uses the same structure with `type: openstack`, a standard `clouds.yaml` credential file, and an optional `cloud` selector.

- Credential paths are relative to the main slice YAML and are resolved by the CLI.
- Credentials are removed before GST/NEST mapping and are never stored with or queued as part of the slice.
- Missing locations are created automatically.
- Existing matching IDs are reused; conflicting IDs return HTTP 409.
- Automatically registered infrastructure persists after slice deletion.
- OSM/NFVO registration remains a prerequisite.
- Version one supports one deployment platform per slice and rejects mixed or hybrid workloads.

## Implementation

- Resolve credential files in the CLI while preserving raw NEST compatibility.
- Use a reusable NBI registration helper for the slice, VIM, and Kubernetes entry points.
- Classify imported OSM descriptors as OpenStack, Kubernetes, mixed, or unknown.
- Select a single target by explicit preference or by platform, location, and NFVO; never silently choose among multiple classified targets.
- Keep OpenStack tenant/quota provisioning and use the Kubernetes cluster's persistent OSM backing account.
- Share OSM instantiate, status, and terminate operations, while skipping VM/VNFR processing for Kubernetes.
- Never delete Kubernetes clusters or persistent infrastructure during slice cleanup.
- Keep the NBI and manager Kubernetes ID indexes aligned.

## Verification

- Test raw NEST compatibility and relative credential resolution.
- Test credential isolation from mapped and queued slice data.
- Test idempotent registration, conflicts, and location creation.
- Test VDU, KDU, hybrid, and unknown descriptor classification.
- Test deterministic target selection, missing targets, and ambiguity.
- Test platform-specific provisioning and persistent Kubernetes cleanup.
- Run focused `unittest` coverage and Python compilation checks without adding dependencies.

## Assumptions

- One slice uses one platform in version one.
- Other required deployment locations must already have compatible infrastructure.
- Credentials are referenced from files rather than written inline in the main NEST.
- Kubernetes defaults to namespace `default` and Helm chart v3 deployment.
- Registration is synchronous and slice deployment remains asynchronous through Kafka.
- No database migration or new dependency is introduced.
