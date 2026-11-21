"""Write credentials with private permissions and atomic replacement."""

import os
import secrets
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_private_text(path: Path, content: str, *, create_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if create_only:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def session_secret(path: Path) -> str:
    configured = os.environ.get("AGENT_SECRET_KEY", "").strip()
    if configured:
        return configured
    if not path.is_file():
        write_private_text(path, secrets.token_hex(32), create_only=True)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("The agent session secret file is empty.")
    return value
