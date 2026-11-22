#!/usr/bin/env python3
"""Record response bodies from the runtime's actual Threads route methods.
Usage: python3 tools/record-thread-contract.py <runtime-repo> <output.json>
Runs only extracted pure route/record/engine code in TMPDIR, with synthetic
workspaces. No broker service, credentials, model, or real notes are accessed.
"""
import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import tempfile

REV = '34b5668'
repo, output = sys.argv[1:]
scratch = Path(tempfile.mkdtemp(prefix='runtime-contract-', dir=os.environ.get('TMPDIR')))
def source(path):
    return subprocess.check_output(['git', '-C', repo, 'show', f'{REV}:{path}'], text=True)
for name in ('loam_threads', 'loam_thread_engine'):
    (scratch / f'{name}.py').write_text(source(f'lib/{name}.py'))
os.environ.update(LOAM_VAULT=str(scratch / 'notes'), LOAM_AI_LABS=str(scratch / 'personas'), LOAM_THREADS_STATE=str(scratch / 'state'))
for who in ('sprout', 'quill'):
    directory = scratch / 'personas' / who
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'agent.json').write_text(json.dumps({'name': who.capitalize()}))
(scratch / 'notes' / '15_CONVERSATIONS').mkdir(parents=True, exist_ok=True)
(scratch / 'notes' / '00_INBOX' / 'from-agents').mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(scratch))
import loam_threads as threads
import loam_thread_engine as engine_mod
# Replace the human-author constant through its use in post_person; keep the
# recording portable without embedding an installation's human identity.
engine_tree = ast.parse(source('lib/loam_thread_engine.py'))
post = next(n for n in ast.walk(engine_tree) if isinstance(n, ast.FunctionDef) and n.name == 'post_person')
append = next(n for n in ast.walk(post) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'append')
setattr(threads, append.args[1].attr, 'you')
# Use the real route implementation, without importing the broker's host setup.
tree = ast.parse(source('bin/session-broker'))
methods = []
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        methods += [m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in ('threads_route', 'thread_stream')]
assert len(methods) == 2
ns = {'Path': Path, 'asyncio': asyncio, 'threads_rec': threads, 'cfg': types.SimpleNamespace(roster_cards=lambda: [{'id': p, 'name': p.capitalize(), 'emoji': None} for p in threads.roster()])}
class BrokerError(Exception):
    def __init__(self, status, message): self.status, self.message = status, message
ns['BrokerError'] = BrokerError
# The response body and SSE events are captured before HTTP encoding.
ns['response'] = lambda status, body: (status, body)
exec(compile(ast.Module(body=methods, type_ignores=[]), 'runtime-threads-routes', 'exec'), ns)
class StopStream(Exception): pass
class Writer:
    def __init__(self): self.result = None; self.events = []
    def write(self, value): self.result = value
    async def drain(self): pass
class Server:
    threads_route = ns['threads_route']
    thread_stream = ns['thread_stream']
    def _count(self, value, name): return int(value) if value else None
    def _open_stream(self, writer): pass
    async def _send_event(self, writer, event):
        writer.events.append(event)
        if event['type'] == 'message.done': raise StopStream()
class FakeSession:
    id = 'synthetic-session'
    def save(self): pass
    def watch(self, queue): self.queue = queue
    def unwatch(self, queue): pass
    async def send_message(self, text):
        events = [
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Looking together.'}}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Looking together.'}, {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'notes.md'}}]}},
            {'type': 'assistant', 'parent_tool_use_id': 'subagent-tool', 'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'questions.md'}}]}},
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'I gathered the open questions.'}}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'I gathered the open questions.'}]}},
            {'type': 'result'},
        ]
        for event in events: self.queue.put_nowait(event)
class FakeBroker:
    async def start_session(self, *args, **kwargs): return FakeSession()
async def main():
    s = Server(); s.threads = engine_mod.ThreadEngine(FakeBroker())
    s.threads.schedule = lambda turn: None
    recordings = {'runtime_commit': REV, 'environment': 'isolated synthetic workspaces; actual route methods and record/engine modules', 'responses': {}}
    async def call(label, method, tail, body=None, params=None):
        w = Writer()
        await s.threads_route(method, tail, params or {}, body or {}, w)
        status, result = w.result
        recordings['responses'][label] = {'status': status, 'body': result}
        return result
    await call('list', 'GET', [])
    row = await call('create', 'POST', [], {'persona': 'sprout', 'title': 'A little room'})
    tid = row['id']
    await call('message', 'POST', [tid, 'messages'], {'text': 'A small next step'})
    queue = s.threads.listen(tid)
    await s.threads.run_turn(engine_mod.Turn(tid, 'sprout', 0, 'synthetic contract recording'))
    engine_events = []
    while not queue.empty(): engine_events.append(queue.get_nowait())
    s.threads.unlisten(tid, queue)
    recordings['engine_events'] = engine_events
    await call('detail', 'GET', [tid], params={'limit': '1'})
    await call('history', 'GET', [tid], params={'before': 'm2', 'limit': '1'})
    await call('patch', 'PATCH', [tid], {'engines': {'sprout': 'codex'}, 'seen': True})
    w = Writer()
    try: await s.thread_stream(tid, 'm0', w)
    except StopStream: pass
    recordings['responses']['events'] = {'status': 200, 'events': w.events}
    Path(output).write_text(json.dumps(recordings, ensure_ascii=False, indent=2) + '\n')
    print(f'Recorded {len(recordings["responses"])} runtime responses at {REV}')
asyncio.run(main())
