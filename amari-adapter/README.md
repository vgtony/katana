# Katana Amari Adapter

This is the small HTTP service Katana calls when creating Amarisoft network slices.
Run it on the Amarisoft host or on a management host that can safely edit/reload the
Amarisoft RAN and CORE configuration.

The adapter is dependency-free Python and starts in dry-run mode by default.
Dry-run mode accepts slice requests, stores them, and proves Katana can reach the
Amari box without changing Amarisoft config.

## Run on the Amarisoft box

Copy this `amari-adapter` directory to `10.45.101.53`, then run two adapters:

```bash
sudo mkdir -p /opt/katana/amari-adapter /var/lib/katana-amari-adapter
sudo cp server.py /opt/katana/amari-adapter/server.py

sudo python3 /opt/katana/amari-adapter/server.py   --component ran   --port 8081   --state-file /var/lib/katana-amari-adapter/ran-slices.json   --dry-run

sudo python3 /opt/katana/amari-adapter/server.py   --component core   --port 8082   --state-file /var/lib/katana-amari-adapter/core-slices.json   --dry-run
```

Open another terminal and test locally:

```bash
curl http://127.0.0.1:8081/health
curl http://127.0.0.1:8082/health
```

Then test from the Katana host:

```bash
curl http://10.45.101.53:8081/health
curl http://10.45.101.53:8082/health
```

## Register in Katana

```bash
curl -X POST http://<katana-nbi-ip>:8000/api/ems   -H 'Content-Type: application/json'   -d '{"id":"amari-ran","type":"amarisoft-ems","url":"http://10.45.101.53:8081"}'

curl -X POST http://<katana-nbi-ip>:8000/api/ems   -H 'Content-Type: application/json'   -d '{"id":"amari-core","type":"amarisoft-ems","url":"http://10.45.101.53:8082"}'
```

## Move from dry-run to real changes

The adapter intentionally does not hard-code Amarisoft config paths because labs vary.
When you are ready, create scripts that update/reload your exact Amarisoft files and
run the adapter with `--active`:

```bash
sudo python3 /opt/katana/amari-adapter/server.py   --component ran   --port 8081   --active   --apply-cmd /opt/katana/amari-adapter/apply-ran-slice.sh   --delete-cmd /opt/katana/amari-adapter/delete-ran-slice.sh
```

The command receives the slice JSON on stdin and these environment variables:

```text
AMARI_ACTION      apply or delete
AMARI_COMPONENT   ran or core
AMARI_SLICE_ID    Katana slice id
AMARI_SLICE_JSON  full JSON payload
```

Until those scripts exist, keep `--dry-run` enabled.
