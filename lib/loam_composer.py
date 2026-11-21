"""The owner's keyboard preference, shared by all chat composers."""
import os
import tempfile

from loam_appearance import path as settings_path


def path():
    return settings_path().with_name("composer-send-key")


def read() -> dict:
    try:
        value = path().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = "enter"
    if value not in ("enter", "modifier"):
        raise ValueError("The saved send key is invalid.")
    return {"send_key": value}


def update(patch: dict) -> dict:
    value = patch.get("send_key")
    if patch.keys() != {"send_key"} or value not in ("enter", "modifier"):
        raise ValueError("Choose enter or modifier for send_key.")
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                     prefix=".composer-", delete=False) as fh:
        temporary = fh.name
        try:
            fh.write(value + "\n")
            fh.close()
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {"send_key": value}
