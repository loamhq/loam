# What this project knows (the runtime)

Started fresh for the first release, 2026-11-15. Loam was built for months
on one person's server before it was cut into this repository; that history
is not here. What it taught that a newcomer needs is below, and the rest is
in the code's comments and `docs/`. Add to this file as you learn by
building: what you found, which premise expired when, what a change costs.
Write for a reader you have never met.

## The shape

- **One config file.** `/etc/loam/loam.toml` (or `$LOAM_CONFIG`) holds every
  path, the user, the owner, the vault's folder names, the time zone and the
  commit identity, read through `lib/loam_config.py` (`docs/config.md`).
  Tests and rehearsals point `LOAM_CONFIG` at a scratch copy.
- **State is outside the checkout.** `paths.state`, `paths.logs` and the
  jobs live elsewhere, because `loam update` refuses a checkout with any
  file a release did not put there (`git status --porcelain --ignored`
  empty). Entry points set `sys.dont_write_bytecode` and units carry
  `PYTHONPYCACHEPREFIX` for the same reason.
- **Generated files have two homes**, the live file and its generator.
  `bin/check-drift` compares them; the generator is the truth.
- **What ships and what is the person's** is `docs/update.md`: an update
  replaces the checkout and `<vault>/System/Loam/` and nothing else.

## Proving things

- **A whole install can be rehearsed on a throwaway VM** with
  `install.sh --source <bundle> --name sam --base loam-proof.test --resolver system
  --local-certs --address <ip>`; `docs/install.md`, "How it was proved",
  has the recipe, including booting systemd in a PID namespace where the
  VM's PID 1 is not systemd.
- **A scratch install can run the real provision and a real broker** with a
  `LOAM_CONFIG` whose `paths.home` is scratch, and `XDG_RUNTIME_DIR` plus
  `DBUS_SESSION_BUS_ADDRESS` pointed at an empty folder, so no
  `systemctl --user` call reaches the real user manager.
- **Caddy orders `handle /path` ahead of `handle @named`** in one block,
  wherever they are written; check with `caddy adapt`, not by reading.
- **`PrivateTmp=yes` breaks anything that hands Docker a path in /tmp**: the
  daemon resolves it in the host's /tmp. No unit that runs `gen-deploy` may
  have it.
- **uv reads `uv.toml` from its working directory**: commands run as another
  user start in that user's home.
- **Redact by value shape, not by key name.** A redaction keyed on
  `token|key|secret` misses `pem`.
