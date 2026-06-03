# Katana Amari Adapter

This is the small HTTP service Katana calls when creating Amarisoft network slices.
Run it on the Amarisoft host or on a management host that can safely edit/reload the
Amarisoft RAN and CORE configuration.

The adapter is dependency-free Python and starts in dry-run mode by default.
Dry-run mode accepts slice requests, stores them, and proves Katana can reach the
Amari box without changing Amarisoft config.

Active mode is Katana-managed: Katana sends desired slice state, the adapter
stores it, renders the managed Amarisoft config blocks, backs up the previous
config files, and runs a configured restart command.

## Run on the Amarisoft box

Copy this `amari-adapter` directory to `10.45.101.53`, then run two dry-run adapters:

```bash
sudo mkdir -p /opt/katana/amari-adapter /var/lib/katana-amari-adapter
sudo cp server.py amari_config_manager.py *.sh /opt/katana/amari-adapter/
sudo chmod +x /opt/katana/amari-adapter/*.sh /opt/katana/amari-adapter/amari_config_manager.py

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

## Move from dry-run to Katana-managed changes

Use active mode only after confirming the active Amarisoft config paths. For the
`amari-classic` host inspected during integration, the active files were:

```text
RAN:   /root/lteenb-linux-2025-09-19/config/gnb-sa.cfg
CORE:  /root/ltemme-linux-2025-09-19/config/mme-ims.cfg
UE DB: /root/ltemme-linux-2025-09-19/config/ue_db-ims.cfg
```

Run the adapter with `--active` and the Katana config-manager hooks:

```bash
sudo AMARI_RESTART_CMD="/bin/systemctl restart lte" \
  python3 /opt/katana/amari-adapter/server.py \
  --component ran \
  --port 8081 \
  --state-file /var/lib/katana-amari-adapter/ran-slices.json \
  --active \
  --apply-cmd /opt/katana/amari-adapter/apply-ran-slice.sh \
  --delete-cmd /opt/katana/amari-adapter/delete-ran-slice.sh

sudo AMARI_RESTART_CMD="/bin/systemctl restart lte" \
  python3 /opt/katana/amari-adapter/server.py \
  --component core \
  --port 8082 \
  --state-file /var/lib/katana-amari-adapter/core-slices.json \
  --active \
  --apply-cmd /opt/katana/amari-adapter/apply-core-slice.sh \
  --delete-cmd /opt/katana/amari-adapter/delete-core-slice.sh
```

The command receives the slice JSON on stdin and these environment variables:

```text
AMARI_ACTION      apply or delete
AMARI_COMPONENT   ran or core
AMARI_SLICE_ID    Katana slice id
AMARI_SLICE_JSON  full JSON payload
```

The bundled hooks render these managed config surfaces:

```text
RAN gnb-sa.cfg:   plmn_list[].nssai
CORE mme-ims.cfg: AMF nssai and matching PDN/APN slices
UE DB:            validated only; missing subscribers fail explicitly
```

The hooks do not invent SIM credentials. If Katana requests a subscriber that is
not present in the UE DB, the apply fails and reports the missing IMSI.
