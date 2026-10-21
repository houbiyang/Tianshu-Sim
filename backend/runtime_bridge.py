"""Local native-plugin bridge. Each tool reads an immutable operation snapshot.

The control channel never acquires the model lock, so cancellation can interrupt
an in-flight SDK run. Tokens stay in process environment, outside public traces.
"""
from __future__ import annotations

import copy
import json
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_contexts = {}
_lock = threading.Lock()
_server = None
_token = secrets.token_urlsafe(32)
_control_url = None
_dispatcher = None


def register_dispatcher(dispatcher):
    global _dispatcher
    _dispatcher = dispatcher


def bind(session_id, context):
    with _lock:
        _contexts[session_id] = copy.deepcopy(context)


def unbind(session_id):
    with _lock:
        _contexts.pop(session_id, None)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer ' + _token:
            self.send_error(401)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2_000_000:
                raise ValueError('invalid payload length')
            payload = json.loads(self.rfile.read(length))
            with _lock:
                context = copy.deepcopy(_contexts.get(payload.get('session_id')))
            if context is None or _dispatcher is None:
                raise ValueError('No active read-only operation for this runtime session')
            value = _dispatcher(context, payload)
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.send_error(400, str(exc)[:200])


def launch_env(ready_file):
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return {'GENTACTIX_CONTROL_TOKEN': _token,
            'GENTACTIX_TOOL_BRIDGE': f'http://127.0.0.1:{_server.server_port}',
            'GENTACTIX_CONTROL_FILE': str(ready_file)}


def connect(ready_file):
    global _control_url
    data = json.loads(Path(ready_file).read_text())
    _control_url = f'http://127.0.0.1:{int(data["port"])}'
    return control('/health')


def control(path, payload=None, timeout=5):
    if not _control_url:
        raise RuntimeError('Native DSH plugin is not connected')
    request = urllib.request.Request(_control_url + path,
        data=json.dumps(payload or {}).encode(),
        headers={'Authorization': 'Bearer ' + _token, 'Content-Type': 'application/json'})
    # Loopback traffic must not use corporate/desktop proxy environment.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get('error', str(exc))
        except Exception:
            detail = str(exc)
        raise RuntimeError(detail) from exc


def close():
    global _control_url
    _control_url = None
    with _lock:
        _contexts.clear()
