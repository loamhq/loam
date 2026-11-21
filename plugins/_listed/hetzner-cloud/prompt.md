Use `hetzner_api` with the project name, HTTP method, API path and optional
JSON body to work with Hetzner Cloud. This reaches servers, SSH keys,
firewalls, volumes, primary IPs, snapshots and the rest of the Cloud API.
Use `GET /server_types` and `GET /pricing` to compare locations. To create a
server, `POST /servers` takes `ssh_keys` and cloud-init in `user_data`.
Name `personal` for the shared Settings key or the name saved under
Hetzner Cloud's Project keys. When the owner gives you a token for a named
project, save it with `settings` action `do`, setting `keys.project.set`
and the project name as `target`. Use `keys.set` with target `hetzner`
for the shared key. The server running Loam cannot be deleted, rebuilt or
powered off with this tool. Use headroom for resizing it.
Where MCP is unavailable, run `loam hetzner PROJECT METHOD PATH`, with
`--body-stdin` for a JSON body.
