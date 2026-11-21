---
id: hetzner-cloud
name: Hetzner Cloud
version: 1.0.0
api: 0
kind: [extension]
default_on: false
description: Let your agents and engineers manage Hetzner Cloud projects.
keys:
  - provider: hetzner
    why: Connects agents and engineers to your personal Hetzner Cloud project
touches:
  network:
    - host: api.hetzner.cloud
      why: Reads and manages resources in the Hetzner Cloud project you choose
    - host: 169.254.169.254
      why: Reads the server ID from Hetzner metadata to protect the Loam host
  record: none
  memory: none
  server: false
contributes:
  agent.tool:
    - name: hetzner-cloud
      run: '"$LOAM_LIB/../bin/mcp-python" tool.py'
      lanes: [persona, engineer]
  agent.prompt:
    - lanes: [persona, engineer]
      file: prompt.md
---

Give your agents and engineers access to the Hetzner Cloud projects you connect.

## What you can do

Compare server types and prices by location, then manage servers, SSH keys,
firewalls, disks, IP addresses and snapshots in the project you choose.

## How to use it

1. In Hetzner Cloud Console, create a token in each project you want to connect.
   Choose read/write to let agents make changes.
2. In **Settings › Connections › API keys › Hetzner Cloud**, save your personal
   project's token as the shared key. Use **Add project** for each other project.
3. Turn this plugin on and ask an agent to work in the project by name.

## What you need, and what leaves your server

Each project needs its own token. Loam checks it with Hetzner when you save it.
Each request and the selected project's token go to Hetzner Cloud.

---

## Technical details

`hetzner_api(project, method, path, body?)` accepts a path under the Hetzner
Cloud API's `/v1`. The optional body is a JSON object. Responses contain the
HTTP status and JSON. The project name `personal` routes to the shared
Settings token. Other names route to their own Settings tokens.

The tool compares server actions with the host ID from Hetzner metadata.
Deleting, rebuilding, shutting down or powering off the Loam host is
refused. Resizing the Loam host goes through headroom.

The same interface is available as `loam hetzner PROJECT METHOD PATH`.
Use `--body-stdin` to send a JSON object on stdin.
[Hetzner Cloud API reference](https://docs.hetzner.cloud/reference/cloud)
