#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class SliceStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def all(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get(self, slice_id):
        return self.all().get(slice_id)

    def upsert(self, slice_id, record):
        data = self.all()
        data[slice_id] = record
        self._write(data)

    def delete(self, slice_id):
        data = self.all()
        record = data.pop(slice_id, None)
        self._write(data)
        return record

    def _write(self, data):
        tmp_path = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp_path.replace(self.path)


class AdapterHandler(BaseHTTPRequestHandler):
    server_version = 'KatanaAmariAdapter/0.1'

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            return self._json(
                {
                    'ok': True,
                    'component': self.server.component,
                    'dry_run': self.server.dry_run,
                    'time': time.time(),
                }
            )
        if path == '/slices':
            return self._json(list(self.server.store.all().values()))
        if path.startswith('/slice/'):
            slice_id = path.rsplit('/', 1)[-1]
            record = self.server.store.get(slice_id)
            if not record:
                return self._json({'error': f'slice {slice_id} not found'}, status=404)
            return self._json(record)
        return self._json({'error': 'not found'}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/slice':
            return self._json({'error': 'not found'}, status=404)

        payload = self._read_json()
        if payload is None:
            return self._json({'error': 'request body must be JSON'}, status=400)

        slice_id = payload.get('slice_id')
        if not slice_id:
            return self._json({'error': 'missing required field: slice_id'}, status=400)

        now = time.time()
        record = {
            'slice_id': slice_id,
            'component': self.server.component,
            'status': 'planned' if self.server.dry_run else 'applying',
            'payload': payload,
            'created_at': now,
            'updated_at': now,
        }
        self.server.store.upsert(slice_id, record)

        command_result = None
        if self.server.dry_run:
            record['message'] = 'dry-run only; no Amarisoft config was changed'
        else:
            if not self.server.apply_cmd:
                record['status'] = 'error'
                record['error'] = 'adapter is not in dry-run mode and no apply command is configured'
                self.server.store.upsert(slice_id, record)
                return self._json(record, status=501)
            command_result = self._run_command('apply', self.server.apply_cmd, payload)
            record['command_result'] = command_result
            record['status'] = 'running' if command_result['ok'] else 'error'

        record['updated_at'] = time.time()
        self.server.store.upsert(slice_id, record)
        return self._json(record, status=201 if record['status'] != 'error' else 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith('/slice/'):
            return self._json({'error': 'not found'}, status=404)

        slice_id = path.rsplit('/', 1)[-1]
        record = self.server.store.get(slice_id)
        if not record:
            return self._json({'error': f'slice {slice_id} not found'}, status=404)

        command_result = None
        if not self.server.dry_run and self.server.delete_cmd:
            command_result = self._run_command('delete', self.server.delete_cmd, record.get('payload', {}))
            if not command_result['ok']:
                record['status'] = 'error'
                record['command_result'] = command_result
                record['updated_at'] = time.time()
                self.server.store.upsert(slice_id, record)
                return self._json(record, status=500)

        deleted = self.server.store.delete(slice_id)
        return self._json(
            {
                'deleted': True,
                'slice_id': slice_id,
                'component': self.server.component,
                'dry_run': self.server.dry_run,
                'record': deleted,
                'command_result': command_result,
            }
        )

    def _read_json(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return None
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            return None

    def _run_command(self, action, command, payload):
        env = os.environ.copy()
        env.update(
            {
                'AMARI_ACTION': action,
                'AMARI_COMPONENT': self.server.component,
                'AMARI_SLICE_ID': payload.get('slice_id', ''),
                'AMARI_SLICE_JSON': json.dumps(payload, sort_keys=True),
            }
        )
        try:
            completed = subprocess.run(
                shlex.split(command),
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=self.server.command_timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'ok': False, 'error': str(exc)}

        return {
            'ok': completed.returncode == 0,
            'returncode': completed.returncode,
            'stdout': completed.stdout,
            'stderr': completed.stderr,
        }

    def _json(self, data, status=200):
        body = json.dumps(data, indent=2, sort_keys=True).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print('%s - - [%s] %s' % (self.address_string(), self.log_date_time_string(), fmt % args))


class AdapterServer(ThreadingHTTPServer):
    def __init__(self, address, handler, args):
        super().__init__(address, handler)
        self.component = args.component
        self.dry_run = args.dry_run
        self.apply_cmd = args.apply_cmd
        self.delete_cmd = args.delete_cmd
        self.command_timeout = args.command_timeout
        self.store = SliceStore(args.state_file)


def parse_args():
    parser = argparse.ArgumentParser(description='Katana adapter for Amarisoft RAN/CORE slice intents')
    parser.add_argument('--host', default=os.getenv('AMARI_ADAPTER_HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.getenv('AMARI_ADAPTER_PORT', '8081')))
    parser.add_argument('--component', choices=('ran', 'core'), default=os.getenv('AMARI_COMPONENT', 'ran'))
    parser.add_argument('--state-file', default=os.getenv('AMARI_STATE_FILE', '/var/lib/katana-amari-adapter/slices.json'))
    parser.add_argument('--apply-cmd', default=os.getenv('AMARI_APPLY_CMD'))
    parser.add_argument('--delete-cmd', default=os.getenv('AMARI_DELETE_CMD'))
    parser.add_argument('--command-timeout', type=int, default=int(os.getenv('AMARI_COMMAND_TIMEOUT', '120')))
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=os.getenv('AMARI_DRY_RUN', 'true').lower() in ('1', 'true', 'yes', 'on'),
        help='store slice requests without changing Amarisoft configuration',
    )
    parser.add_argument(
        '--active',
        action='store_false',
        dest='dry_run',
        help='run apply/delete commands instead of dry-run storage only',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    server = AdapterServer((args.host, args.port), AdapterHandler, args)
    print(
        f'Katana Amari adapter listening on {args.host}:{args.port} '
        f'component={args.component} dry_run={args.dry_run}'
    )
    server.serve_forever()


if __name__ == '__main__':
    main()
