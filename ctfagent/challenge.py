"""Challenge loader. Splits challenge.json into a PUBLIC brief (for the solver) and a
private path (for the controller/evaluator).

Assumed layout (NYU CTF Bench / CTFTiny style):
    <challenge_dir>/challenge.json   {name, category, description, flag, files[],
                                       box?, internal_port?, compose?}
    <challenge_dir>/<contestant files listed in "files">
    <challenge_dir>/docker-compose.yml or Dockerfile   (server, never copied to solver)
Adjust `load` if the course Implementation Guide uses a different schema.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .common import ROOT, SafeError, sha256

PUBLIC_FIELDS = ("name", "category", "description")


@dataclass
class PublicChallenge:
    challenge_id: str
    name: str
    category: str
    description: str
    files: list = field(default_factory=list)          # basenames inside /work
    endpoints: list = field(default_factory=list)      # [{"host":..., "port":...}]

    def brief(self) -> dict:
        return {"name": self.name, "category": self.category, "description": self.description,
                "files_in_workdir": self.files, "allowed_endpoints": self.endpoints}


@dataclass
class ChallengeSpec:
    """Controller-only view: includes paths to server build files and the answer key."""
    public: PublicChallenge
    directory: Path
    answer_key: tuple          # (path, challenge_id or None) - controller/evaluator only
    file_paths: list
    compose: bool
    has_dockerfile: bool
    box: str | None
    port: int | None


def challenge_id_for(directory: Path) -> str:
    try:
        return str(directory.resolve().relative_to((ROOT / "challenges").resolve()))
    except ValueError:
        return directory.name


def load(directory) -> ChallengeSpec:
    directory = Path(directory).resolve()
    cj = directory / "challenge.json"
    if cj.exists():
        data = json.loads(cj.read_text(encoding="utf-8"))
        base = directory
        key = (cj, None)
    elif (directory / "context.json").exists():
        # Course CTFTiny package: context.json + files/ + inventory.json, no flag.
        # The answer key (if any) lives in private/flags.json, keyed by challenge_id.
        data = json.loads((directory / "context.json").read_text(encoding="utf-8"))
        base = directory / "files"
        key = (ROOT / "private/flags.json", data.get("challenge_id", directory.name))
    else:
        raise SafeError(f"challenge_metadata_missing: {directory}")
    inventory = {}
    if (directory / "inventory.json").exists():
        inventory = {i["filename"]: i.get("sha256") for i in json.loads((directory / "inventory.json").read_text())}
    file_paths = []
    for rel in data.get("files", []) or []:
        p = (base / rel).resolve()
        if directory not in p.parents or not p.is_file():
            raise SafeError(f"contestant_file_invalid: {rel}")
        if p.name in {"challenge.json", "docker-compose.yml", "Dockerfile", "context.json", "inventory.json"}:
            raise SafeError(f"contestant_file_is_server_only: {rel}")
        if inventory.get(rel) and sha256(p.read_bytes()) != inventory[rel]:
            raise SafeError(f"contestant_file_hash_mismatch: {rel}")
        file_paths.append(p)
    box = data.get("box") or data.get("host")
    port = data.get("internal_port") or data.get("port")
    port = int(port) if port not in (None, "") else None
    endpoints = [{"host": box, "port": port}] if box and port else []
    public = PublicChallenge(
        challenge_id=str(data.get("challenge_id") or challenge_id_for(directory)),
        name=str(data.get("name", directory.name)),
        category=str(data.get("category", "unknown")),
        description=str(data.get("description", "")),
        files=[p.name for p in file_paths],
        endpoints=endpoints,
    )
    return ChallengeSpec(public=public, directory=directory, answer_key=key, file_paths=file_paths,
                         compose=bool(data.get("compose")) or (directory / "docker-compose.yml").exists(),
                         has_dockerfile=(directory / "Dockerfile").exists(), box=box, port=port)


def read_list(path) -> list[Path]:
    """Read a challenge list file (one dir per line, relative to repo root; # comments)."""
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            p = Path(line)
            out.append(p if p.is_absolute() else ROOT / p)
    return out
