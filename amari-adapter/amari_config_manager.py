#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


MANAGED_PREFIX = "/* KATANA MANAGED"
MANAGED_SUFFIX = "*/"


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def load_stdin_payload():
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def desired_payloads(state_file, action, current_slice_id, stdin_payload):
    state = load_json(state_file)
    payloads = []
    for slice_id, record in state.items():
        if action == "delete" and slice_id == current_slice_id:
            continue
        payload = record.get("payload", record)
        if payload:
            payloads.append(payload)

    if action != "delete" and stdin_payload:
        slice_id = stdin_payload.get("slice_id")
        if slice_id and not any(p.get("slice_id") == slice_id for p in payloads):
            payloads.append(stdin_payload)

    return payloads


def normalize_sd(value):
    if value in (None, ""):
        return None
    text = str(value).lower().replace("0x", "")
    if not re.fullmatch(r"[0-9a-f]{6}", text):
        raise ValueError(f"invalid SD '{value}'; expected 6 hex digits")
    return text


def normalize_slices(payloads):
    slices = []
    seen = set()
    for payload in payloads:
        s_nssai = payload.get("s_nssai") or {}
        if "sst" not in s_nssai:
            raise ValueError(f"slice {payload.get('slice_id')} is missing s_nssai.sst")
        sst = int(s_nssai["sst"])
        sd = normalize_sd(s_nssai.get("sd"))
        dnn = payload.get("dnn")
        if not dnn:
            raise ValueError(f"slice {payload.get('slice_id')} is missing dnn")

        key = (sst, sd, dnn)
        if key in seen:
            continue
        seen.add(key)
        slices.append(
            {
                "slice_id": payload.get("slice_id"),
                "name": payload.get("name"),
                "sst": sst,
                "sd": sd,
                "dnn": dnn,
                "qos": payload.get("qos") or {},
                "subscribers": payload.get("subscribers") or [],
            }
        )
    return slices


def matching_close(text, open_pos, open_ch, close_ch):
    depth = 0
    string = None
    escape = False
    line_comment = False
    block_comment = False
    i = open_pos
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if line_comment:
            if ch == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string:
                string = None
            i += 1
            continue

        if ch in ("'", '"'):
            string = ch
        elif ch == "/" and nxt == "/":
            line_comment = True
            i += 1
        elif ch == "/" and nxt == "*":
            block_comment = True
            i += 1
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1

    raise ValueError(f"could not find matching '{close_ch}'")


def managed_block(name, assignment):
    return f"{MANAGED_PREFIX} {name} START {MANAGED_SUFFIX}\n{assignment}\n{MANAGED_PREFIX} {name} END {MANAGED_SUFFIX}"


def replace_managed(text, name, assignment):
    start = f"{MANAGED_PREFIX} {name} START {MANAGED_SUFFIX}"
    end = f"{MANAGED_PREFIX} {name} END {MANAGED_SUFFIX}"
    start_pos = text.find(start)
    if start_pos == -1:
        return None
    end_pos = text.find(end, start_pos)
    if end_pos == -1:
        raise ValueError(f"managed block {name} has no end marker")
    end_pos += len(end)
    return text[:start_pos] + managed_block(name, assignment) + text[end_pos:]


def replace_array_assignment(text, key, assignment, name, commented=False):
    replaced = replace_managed(text, name, assignment)
    if replaced is not None:
        return replaced

    if commented:
        match = re.search(r"/\*\s*" + re.escape(key) + r"\s*:\s*\[", text)
        if not match:
            raise ValueError(f"could not find commented {key} array")
        array_open = text.find("[", match.start())
        array_close = matching_close(text, array_open, "[", "]")
        comment_close = text.find("*/", array_close)
        if comment_close == -1:
            raise ValueError(f"commented {key} array has no closing comment")
        return text[: match.start()] + managed_block(name, assignment) + text[comment_close + 2 :]

    match = re.search(r"\b" + re.escape(key) + r"\s*:\s*\[", text)
    if not match:
        raise ValueError(f"could not find {key} array")
    array_open = text.find("[", match.start())
    array_close = matching_close(text, array_open, "[", "]")
    end = array_close + 1
    if end < len(text) and text[end] == ",":
        end += 1
    return text[: match.start()] + managed_block(name, assignment) + text[end:]


def snssai_entries(slices, indent):
    lines = []
    for item in slices:
        lines.append(f"{indent}{{")
        lines.append(f"{indent}  sst: {item['sst']},")
        if item["sd"]:
            lines.append(f"{indent}  sd: 0x{item['sd']},")
        lines.append(f"{indent}}},")
    return "\n".join(lines)


def render_nssai_assignment(slices, indent):
    body = snssai_entries(slices, indent + "  ")
    if not body:
        body = indent + "  /* no Katana slices configured */"
    return f"{indent}nssai: [\n{body}\n{indent}],"


def render_pdn_slices_assignment(slices, indent):
    lines = [f"{indent}slices: ["]
    for item in slices:
        five_qi = int(item["qos"].get("five_qi", 9))
        priority = int(item["qos"].get("priority_level", 15))
        lines.extend(
            [
                f"{indent}  {{",
                f"{indent}    snssai: {{",
                f"{indent}      sst: {item['sst']},",
            ]
        )
        if item["sd"]:
            lines.append(f"{indent}      sd: 0x{item['sd']},")
        lines.extend(
            [
                f"{indent}    }},",
                f"{indent}    qos_flows: [",
                f"{indent}      {{",
                f'{indent}        "5qi": {five_qi},',
                f"{indent}        priority_level: {priority},",
                f'{indent}        pre_emption_capability: "shall_not_trigger_pre_emption",',
                f'{indent}        pre_emption_vulnerability: "not_pre_emptable",',
                f"{indent}      }},",
                f"{indent}    ],",
                f"{indent}  }},",
            ]
        )
    if not slices:
        lines.append(f"{indent}  /* no Katana slices configured */")
    lines.append(f"{indent}],")
    return "\n".join(lines)


def find_pdn_object(text, dnn):
    match = re.search(r'access_point_name\s*:\s*"' + re.escape(dnn) + r'"', text)
    if not match:
        raise ValueError(f'PDN/APN "{dnn}" not found in core config')
    obj_start = text.rfind("{", 0, match.start())
    if obj_start == -1:
        raise ValueError(f'could not find object for PDN/APN "{dnn}"')
    obj_end = matching_close(text, obj_start, "{", "}")
    return obj_start, obj_end


def replace_pdn_slices(text, dnn, slices):
    obj_start, obj_end = find_pdn_object(text, dnn)
    obj_text = text[obj_start : obj_end + 1]
    assignment = render_pdn_slices_assignment(slices, "      ")
    name = f"PDN {dnn} SLICES"

    replaced = replace_managed(obj_text, name, assignment)
    if replaced is None:
        try:
            replaced = replace_array_assignment(obj_text, "slices", assignment, name, commented=True)
        except ValueError:
            insertion = "\n\n" + managed_block(name, assignment) + "\n"
            replaced = obj_text[: obj_text.rfind("}")] + insertion + obj_text[obj_text.rfind("}") :]

    return text[:obj_start] + replaced + text[obj_end + 1 :]


def known_imsis(ue_db_file):
    if not ue_db_file:
        return set()
    try:
        text = Path(ue_db_file).read_text()
    except OSError as exc:
        raise ValueError(f"could not read UE DB {ue_db_file}: {exc}") from exc
    return set(re.findall(r'imsi\s*:\s*"([^"]+)"', text))


def validate_subscribers(slices, ue_db_file):
    requested = sorted({imsi for item in slices for imsi in item["subscribers"]})
    if not requested:
        return
    existing = known_imsis(ue_db_file)
    missing = [imsi for imsi in requested if imsi not in existing]
    if missing:
        raise ValueError(
            "subscriber(s) missing from UE DB; add SIM credentials first: "
            + ", ".join(missing)
        )


def backup_file(path, backup_dir=None):
    source = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if backup_dir:
        target_dir = Path(backup_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.name}.{stamp}.bak"
    else:
        target = source.with_name(f"{source.name}.{stamp}.katana.bak")
    shutil.copy2(source, target)
    return str(target)


def write_if_changed(path, new_text, backup_dir=None):
    target = Path(path)
    old_text = target.read_text()
    if old_text == new_text:
        return {"changed": False, "backup": None}
    backup = backup_file(target, backup_dir)
    tmp = target.with_name(f".{target.name}.katana.tmp")
    tmp.write_text(new_text)
    tmp.replace(target)
    return {"changed": True, "backup": backup}


def run_restart(command):
    if not command:
        return {"ok": True, "skipped": True, "message": "no restart command configured"}
    completed = subprocess.run(
        shlex.split(command),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def apply_ran(args, slices):
    text = Path(args.config_file).read_text()
    assignment = render_nssai_assignment(slices, "      ")
    new_text = replace_array_assignment(text, "nssai", assignment, "RAN NSSAI", commented=False)
    return write_if_changed(args.config_file, new_text, args.backup_dir)


def apply_core(args, slices):
    validate_subscribers(slices, args.ue_db_file)
    text = Path(args.config_file).read_text()
    text = replace_array_assignment(
        text,
        "nssai",
        render_nssai_assignment(slices, "  "),
        "CORE AMF NSSAI",
        commented=True,
    )

    by_dnn = {}
    for item in slices:
        by_dnn.setdefault(item["dnn"], []).append(item)
    for dnn, dnn_slices in by_dnn.items():
        text = replace_pdn_slices(text, dnn, dnn_slices)

    return write_if_changed(args.config_file, text, args.backup_dir)


def parse_args():
    parser = argparse.ArgumentParser(description="Render Amarisoft config from Katana Amari slice state")
    parser.add_argument("--component", choices=("ran", "core"), required=True)
    parser.add_argument("--action", choices=("apply", "delete"), default=os.getenv("AMARI_ACTION", "apply"))
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--ue-db-file")
    parser.add_argument("--backup-dir")
    parser.add_argument("--restart-cmd", default=os.getenv("AMARI_RESTART_CMD", ""))
    return parser.parse_args()


def main():
    args = parse_args()
    stdin_payload = load_stdin_payload()
    payloads = desired_payloads(
        args.state_file,
        args.action,
        os.getenv("AMARI_SLICE_ID", stdin_payload.get("slice_id")),
        stdin_payload,
    )
    slices = normalize_slices(payloads)

    if args.component == "ran":
        result = apply_ran(args, slices)
    else:
        result = apply_core(args, slices)

    restart = run_restart(args.restart_cmd) if result["changed"] else {"ok": True, "skipped": True}
    output = {
        "ok": bool(restart["ok"]),
        "component": args.component,
        "action": args.action,
        "slice_count": len(slices),
        "config_file": args.config_file,
        "changed": result["changed"],
        "backup": result["backup"],
        "restart": restart,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
