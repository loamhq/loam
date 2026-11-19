#!/usr/bin/env python3
"""Drive both production entry points on a throwaway install with a stream-json CLI."""
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STUB = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
def emit(**data):
    print(json.dumps(data), flush=True)
for line in sys.stdin:
    m = json.loads(line)
    if m['type'] == 'user':
        with Path(os.environ['TMPDIR'], 'reads').open('a') as f:
            print(os.getpid(), file=f)
        emit(type='system', subtype='init')
        emit(type='stream_event',event={'type':'content_block_delta','delta':{'type':'text_delta','text':'one '}})
        emit(type='control_request',request_id='probe-permission',
             request={'subtype':'can_use_tool','tool_name':'Read','input':{'file_path':'README.md'}})
    if m['type'] == 'control_response':
        emit(type='assistant',message={'content':[{'type':'text','text':'one complete answer'}]})
        emit(type='result',num_turns=1)
        gate = Path(os.environ['TMPDIR'], 'notify')
        while not gate.exists():
            time.sleep(.025)
        emit(type='assistant',message={'content':[{'type':'text','text':'background finished'}]})
        emit(type='result',num_turns=2)
        gate.with_suffix('.sent').touch()
"""


def wait(check):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        try:
            value = check()
        except (OSError, ValueError):
            value = None
        if value:
            return value
        time.sleep(.025)
    raise AssertionError(f"timed out at {check.__code__.co_firstlineno}")


def check(d):
    suite = runpy.run_path(str(ROOT / 'tests/run-all'))
    suite['MACHINE']['paths']['home'] = str(d)
    env = suite['sealed_env'](d)
    cli = d / '.local/bin/claude'
    cli.parent.mkdir(parents=True)
    cli.write_text(STUB)
    cli.chmod(0o700)
    with socket.socket() as pick:
        pick.bind(('127.0.0.1', 0))
        port = pick.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    token_file = d / 'state/broker/token'
    processes = []

    def launch(name, *args):
        proc = subprocess.Popen([sys.executable, ROOT / 'bin' / name, *args],
                                env=env, cwd=d, stdin=subprocess.DEVNULL)
        processes.append(proc)
        return proc

    def request(path, method='GET', data=None):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={'Authorization': 'Bearer ' + token_file.read_text()})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def api():
        proc = launch('session-broker', '--session-socket', 'socket/host', '--addr', '127.0.0.1', '--port', str(port))
        wait(lambda: request('/up')['ok'])
        return proc

    try:
        host = launch('session-host', '--socket', 'socket/host')
        first = api()
        tid = request('/v1/threads', 'POST', {'persona':'iris'})['id']
        path = '/v1/threads/' + tid
        request(path + '/messages', 'PUT', {'text':'probe', 'submission_id':'probe-once'})
        reads = d / 'tmp/reads'
        child = int(wait(lambda: reads.read_text().strip()))
        row = wait(lambda: next((r for r in request('/v1/sessions/live')['sessions']
                                 if r['agent'] == 'iris'), None))
        sid = row['id']
        first.terminate()
        assert first.wait(timeout=8) == 0
        os.kill(child, 0)
        assert host.poll() is None
        second = api()
        info = request('/v1/sessions/' + sid)
        assert info['archived'] and info['state'] == 'running', info
        request('/v1/sessions/' + sid + '/permissions/probe-permission', 'POST', {'behavior':'deny'})
        def answer():
            rows = request(path)['messages']
            return rows if len(rows) == 2 else None
        rows = wait(answer)
        assert [(r['id'],r['text']) for r in rows] == [('m1','probe'),('m2','one complete answer')]
        assert reads.read_text().splitlines() == [str(child)]
        second.terminate()
        assert second.wait(timeout=8) == 0
        (d / 'tmp/notify').touch()
        wait(lambda: (d / 'tmp/notify.sent').exists())
        third = api()
        def background():
            rows = request(path)['messages']
            return rows if len(rows) == 3 else None
        rows = wait(background)
        assert rows[-1]['text'] == 'background finished'
        assert reads.read_text().splitlines() == [str(child)]
        request('/v1/sessions/'+sid, 'DELETE', {})
        wait(lambda: not Path('/proc',str(child)).exists())
        print(f'PASS host {host.pid}, child {child}, API SIGTERM {first.pid} -> {second.pid}, '
              'permission answered after reconnect, one input and reply, '
              f'background reply across second SIGTERM -> {third.pid}, explicit stop reaped child')
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        check(Path(tmp))
