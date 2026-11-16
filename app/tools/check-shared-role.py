#!/usr/bin/env python3
"""Compare a rendered role with its shared source without running provision.
Usage: python3 tools/check-shared-role.py <runtime-repository> [runtime-ref]
Only reads git objects; checks this repo's role.md, and writes nothing.
"""
import ast
from pathlib import Path
import re
import subprocess
import sys

repo = sys.argv[1]
ref = sys.argv[2] if len(sys.argv) > 2 else 'HEAD'
def source(path):
    return subprocess.check_output(['git', '-C', repo, 'show', f'{ref}:{path}'], text=True)
renderer = source('bin/provision')
role = source('engineer/role.md')
# Evaluate only the pure renderer's constants and helpers. Importing provision
# itself would initialize the runtime's host configuration.
names = {'CraftError', '_split_sections', '_paragraphs', 'render_craft'}
nodes = []
for node in ast.parse(renderer).body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
        nodes.append(node)
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith('CRAFT_') for t in node.targets):
        nodes.append(node)
namespace = {'re': re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'shared-role-renderer', 'exec'), namespace)
rendered = (Path(__file__).resolve().parent.parent / 'role.md').read_text()
mark = namespace['CRAFT_MARK_RE'].search(rendered)
assert mark, 'role.md has no render stamp'
assert rendered == namespace['render_craft'](role, *mark.groups(), harness_tracked=True), 'role.md differs from the shared render'
print(f'PASS: role.md is byte-identical to shared render {mark.group(1)} dated {mark.group(2)} (tracked harness)')
for path in ('bin/provision', 'engineer/role.md'):
    blob = subprocess.check_output(['git', '-C', repo, 'rev-parse', f'{ref}:{path}'], text=True).strip()
    print(f'{path} source blob: {blob}')
