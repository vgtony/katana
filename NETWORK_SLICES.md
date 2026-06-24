# Generic Network Slice Adapters

Katana has two slice APIs:

- `/api/amarisoft-slices` is the existing Amarisoft-specific helper. It expects one RAN target and one CORE target.
- `/api/network-slices` is the generic adapter API. It can send one slice intent to any list of RAN, CORE, transport, or lab-specific adapters.

## Adapter Contract

Every target adapter must expose:

```text
POST /slice
DELETE /slice/<slice_id>
```

Katana sends a normalized JSON payload containing `slice_id`, `s_nssai`, `plmn`, `dnn`, `qos`, `subscribers`, `target_type`, `target_id`, `driver`, and `target_config`.

The adapter owns the technology-specific work. For example, an Amarisoft adapter can edit Amarisoft config files, while a Free5GC adapter can patch Kubernetes resources or call a Free5GC API.

## Register Targets

Register one target for each independently managed RAN or CORE environment.

```bash
curl -s -X POST http://localhost:8000/api/network-targets \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "ran-a-amari",
    "name": "RAN A Amarisoft",
    "role": "ran",
    "driver": "amarisoft-ran",
    "url": "http://10.50.101.62:8081",
    "config": {
      "lab": "callbox-mini"
    }
  }'
```

```bash
curl -s -X POST http://localhost:8000/api/network-targets \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "core-a-amari",
    "name": "CORE A Amarisoft",
    "role": "core",
    "driver": "amarisoft-core",
    "url": "http://10.50.101.62:8082",
    "config": {
      "lab": "callbox-mini"
    }
  }'
```

```bash
curl -s -X POST http://localhost:8000/api/network-targets \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "core-b-free5gc",
    "name": "CORE B Free5GC",
    "role": "core",
    "driver": "free5gc-k8s",
    "url": "http://free5gc-adapter.example:8082",
    "config": {
      "namespace": "free5gc"
    }
  }'
```

## Create Slices

Create a slice for RAN A and Amarisoft CORE:

```bash
curl -s -X POST http://localhost:8000/api/network-slices \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "amari-native-slice",
    "s_nssai": {"sst": 9, "sd": "999999"},
    "plmn": {"mcc": "001", "mnc": "01"},
    "dnn": "internet",
    "targets": [
      {"target_id": "ran-a-amari"},
      {"target_id": "core-a-amari"}
    ]
  }'
```

Create a slice for RAN B and Free5GC CORE:

```bash
curl -s -X POST http://localhost:8000/api/network-slices \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "free5gc-slice",
    "s_nssai": {"sst": 1, "sd": "010203"},
    "plmn": {"mcc": "001", "mnc": "01"},
    "dnn": "internet",
    "targets": [
      {"target_id": "ran-b-amari"},
      {"target_id": "core-b-free5gc"}
    ]
  }'
```

Use `dry_run: true` to store and preview the request without calling the adapters.

## Delete Slices

Deleting a generic slice calls `DELETE /slice/<slice_id>` on every target stored in the slice record, then removes the Katana record:

```bash
curl -s -X DELETE http://localhost:8000/api/network-slices/<slice_id>
```

Each adapter decides how deletion maps to its backend. If multiple Katana slice records point to the same backend SST, SD, and DNN, the adapter should preserve the backend config until the last equivalent record is removed.
