"""Cloud backup of the data directory through rclone: AWS S3, Cloudflare R2, Google Drive or any rclone
remote, encrypted (rclone crypt) or plain. The method follows garmin-archive and healthkit-archive.

<target>/encrypted/  or  <target>/plain/
  documents/<doc id>/<doc id>.<sha256[:16]>.tar   one archive per version of a document: the scans,
                                                  document.pdf, meta.json, extraction.json, review.json
  extras/people.<sha16>.toml                      people.toml
  extras/load-manifest.<sha16>.json               fhir/load-manifest.json
  manifest.json                                   the current version of every document and extra file

A file on a target is never overwritten and never deleted: a changed document gets a new name, a removed
document only leaves manifest.json. manifest.json is written last and names only files the target holds,
so a run that fails part way leaves the previous, complete backup. <data dir>/backup/state.json only caches
what was hashed and uploaded; without it the next run finds everything again in the target's listing.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import store

log = logging.getLogger("paper-archive")
FORMAT = "paper-archive/backup/1"
SETTINGS = ("PAPER_AWS_ACCESS_KEY_ID", "PAPER_AWS_SECRET_ACCESS_KEY", "PAPER_AWS_BUCKET", "PAPER_AWS_REGION",
            "PAPER_R2_ACCOUNT_ID", "PAPER_R2_ACCESS_KEY_ID", "PAPER_R2_SECRET_ACCESS_KEY", "PAPER_R2_BUCKET",
            "PAPER_GDRIVE_TOKEN", "PAPER_GDRIVE_CLIENT_ID", "PAPER_GDRIVE_CLIENT_SECRET", "PAPER_GDRIVE_FOLDER",
            "PAPER_BACKUP_RCLONE_REMOTE", "PAPER_BACKUP_KEY", "PAPER_BACKUP_PLAIN", "PAPER_RCLONE_BIN")
TARGET_NAMES = ("aws", "r2", "gdrive", "rclone")
EXTRAS = {"people.toml": "extras/people.{key}.toml", "fhir/load-manifest.json": "extras/load-manifest.{key}.json"}
DOCUMENT_FILES = ("document.pdf", "meta.json", "extraction.json", "review.json")
ORIGINAL_RE = re.compile(r"original/[A-Za-z0-9._-]+")
DOC_ID_RE = re.compile(r"d_[0-9a-f]{16}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
KEY_RE = re.compile(r"[a-z2-7]{52}")
RETRIES = ("--retries", "3", "--low-level-retries", "20")
NOT_FOUND = (3, 4)               # rclone: directory / file not found


class BackupError(Exception):
    """A target could not be read or written; the next run tries again (exit code 1)."""


# ---------------------------------------------------------------- configuration


def settings() -> dict:
    """The backup settings from the environment and <repo>/.env."""
    return {name: value for name in SETTINGS if (value := store.env(name))}


@dataclass
class Target:
    name: str
    remote: str                               # rclone remote:path (or a local folder) that holds plain/ and encrypted/
    options: dict = field(default_factory=dict)   # rclone backend options, set in rclone's environment
    private_config: bool = True               # False: a remote of the owner's own rclone configuration


@dataclass
class Mode:
    key: str | None                           # None: plain

    @property
    def folder(self) -> str:
        return "encrypted" if self.key else "plain"


def _need(env: dict, name: str, keys: list[str]) -> list[str]:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise store.ArchiveError(f"{name} backup: set {', '.join(missing)} "
                                 f"(or remove the other PAPER_{name.upper()}_* settings)")
    return [env[k] for k in keys]


def _bucket_path(value: str, var: str) -> str:
    path = value.strip().strip("/")
    if not path or ":" in path:
        raise store.ArchiveError(f"{var} must be bucket or bucket/prefix")
    return path


def targets_from_env(env: dict, only: str | None = None) -> dict[str, Target]:
    """A target is on when its credentials are set. `only`: build and check just that one."""
    def on(name: str, prefix: str) -> bool:
        return only in (None, name) and any(v and k.startswith(prefix) for k, v in env.items())

    out = {}
    if on("aws", "PAPER_AWS_"):
        key, secret, bucket = _need(env, "aws", ["PAPER_AWS_ACCESS_KEY_ID", "PAPER_AWS_SECRET_ACCESS_KEY",
                                                 "PAPER_AWS_BUCKET"])
        out["aws"] = Target("aws", "paperaws:" + _bucket_path(bucket, "PAPER_AWS_BUCKET"), {
            "TYPE": "s3", "PROVIDER": "AWS", "ENV_AUTH": "false", "ACCESS_KEY_ID": key, "SECRET_ACCESS_KEY": secret,
            "REGION": env.get("PAPER_AWS_REGION") or "us-east-1", "NO_CHECK_BUCKET": "true"})
    if on("r2", "PAPER_R2_"):
        account, key, secret, bucket = _need(env, "r2", ["PAPER_R2_ACCOUNT_ID", "PAPER_R2_ACCESS_KEY_ID",
                                                         "PAPER_R2_SECRET_ACCESS_KEY", "PAPER_R2_BUCKET"])
        if not re.fullmatch(r"[0-9a-f]{32}", account):
            raise store.ArchiveError("PAPER_R2_ACCOUNT_ID is the 32 hex characters shown in the R2 overview")
        out["r2"] = Target("r2", "paperr2:" + _bucket_path(bucket, "PAPER_R2_BUCKET"), {
            "TYPE": "s3", "PROVIDER": "Cloudflare", "ENV_AUTH": "false", "ACCESS_KEY_ID": key,
            "SECRET_ACCESS_KEY": secret, "ENDPOINT": f"https://{account}.r2.cloudflarestorage.com",
            "REGION": "auto", "ACL": "private", "NO_CHECK_BUCKET": "true"})
    if on("gdrive", "PAPER_GDRIVE_"):
        token, cid, csecret = _need(env, "gdrive", ["PAPER_GDRIVE_TOKEN", "PAPER_GDRIVE_CLIENT_ID",
                                                    "PAPER_GDRIVE_CLIENT_SECRET"])
        try:
            refreshable = bool(json.loads(token).get("refresh_token"))
        except (ValueError, AttributeError):
            refreshable = False
        if not refreshable:
            raise store.ArchiveError("PAPER_GDRIVE_TOKEN must be the JSON token printed by `rclone authorize "
                                     "drive`, with a refresh_token")
        folder = (env.get("PAPER_GDRIVE_FOLDER") or "paper-archive").strip().strip("/")
        if not folder or ":" in folder:
            raise store.ArchiveError("PAPER_GDRIVE_FOLDER must be a folder path in My Drive")
        out["gdrive"] = Target("gdrive", "papergdrive:" + folder, {
            "TYPE": "drive", "SCOPE": "drive", "TOKEN": token, "CLIENT_ID": cid, "CLIENT_SECRET": csecret})
    if only in (None, "rclone") and env.get("PAPER_BACKUP_RCLONE_REMOTE"):
        remote = env["PAPER_BACKUP_RCLONE_REMOTE"]
        if not remote_name(remote):                                # a local folder
            remote = str(Path(remote).expanduser().resolve())
        out["rclone"] = Target("rclone", remote.rstrip("/") or "/", private_config=False)
    return out


def new_key() -> str:
    """A random 256-bit key as 52 base32 characters."""
    return base64.b32encode(secrets.token_bytes(32)).decode().rstrip("=").lower()


def mode_from_env(env: dict) -> Mode:
    """Encrypted or plain must be chosen: a missing setting never means a readable upload."""
    key = env.get("PAPER_BACKUP_KEY", "")
    plain = env.get("PAPER_BACKUP_PLAIN", "").lower() in ("1", "true", "yes")
    if key and plain:
        raise store.ArchiveError("PAPER_BACKUP_KEY and PAPER_BACKUP_PLAIN are both set: keep one")
    if key:
        if not KEY_RE.fullmatch(key):
            raise store.ArchiveError("PAPER_BACKUP_KEY is not a key made by `backup new-key` (52 characters a-z, 2-7)")
        return Mode(key)
    if plain:
        return Mode(None)
    raise store.ArchiveError("choose how the backup is stored: PAPER_BACKUP_KEY=<key> (encrypted; make a key "
                             "with `backup new-key`) or PAPER_BACKUP_PLAIN=true (readable by the provider)")


# ---------------------------------------------------------------- rclone


def remote_name(remote: str) -> str | None:
    name, sep, _ = remote.partition(":")
    return name if sep and "/" not in name else None


def rjoin(remote: str, rel: str) -> str:
    return remote + rel if remote.endswith(":") else f"{remote}/{rel}"


class Rclone:
    """Every call to rclone. Credentials and the key reach it in its environment or on stdin only."""

    def __init__(self, root: Path, env: dict, bin_: str | None = None):
        self.bin = bin_ or env.get("PAPER_RCLONE_BIN") or "rclone"
        if not shutil.which(self.bin):
            raise store.ArchiveError(f"rclone ({self.bin}) not found: install it (https://rclone.org/install/) "
                                     "or set PAPER_RCLONE_BIN")
        self.dir = root / "backup"
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._obscured: dict[str, str] = {}

    def _env(self, target: Target, mode: Mode | None) -> dict:
        env = dict(os.environ)
        if target.private_config:
            # rclone saves a renewed Google Drive token into its configuration file: keep that in our own.
            config = self.dir / "rclone.conf"
            if not config.exists():
                os.close(os.open(config, os.O_CREAT | os.O_WRONLY, 0o600))
            env["RCLONE_CONFIG"] = str(config)
            section = f"RCLONE_CONFIG_{remote_name(target.remote).upper()}_"
            env.update({section + k: v for k, v in target.options.items()})
        if mode and mode.key:
            section = f"RCLONE_CONFIG_{self._crypt(target).upper()}_"
            env.update({section + "TYPE": "crypt", section + "REMOTE": rjoin(target.remote, mode.folder),
                        section + "PASSWORD": self._obscure(mode.key), section + "FILENAME_ENCRYPTION": "standard",
                        section + "DIRECTORY_NAME_ENCRYPTION": "true"})
        return env

    @staticmethod
    def _crypt(target: Target) -> str:
        return f"paper{target.name}crypt"

    def _obscure(self, key: str) -> str:
        if key not in self._obscured:
            result = subprocess.run([self.bin, "obscure", "-"], input=key, capture_output=True, text=True, timeout=60)
            if result.returncode != 0 or not result.stdout.strip():
                raise store.ArchiveError("rclone could not prepare the encryption key (rclone obscure failed)")
            self._obscured[key] = result.stdout.strip()
        return self._obscured[key]

    def where(self, target: Target, mode: Mode) -> str:
        """The remote that holds documents/, extras/ and manifest.json."""
        return self._crypt(target) + ":" if mode.key else rjoin(target.remote, mode.folder)

    def run(self, target: Target, mode: Mode | None, *args: str, timeout: int = 3600):
        try:
            return subprocess.run([self.bin, *args, *RETRIES], capture_output=True, text=True, timeout=timeout,
                                  env=self._env(target, mode))
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", f"rclone timed out after {timeout} s")

    @staticmethod
    def error(result) -> str:
        lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
        return f"rclone exited {result.returncode}: {' | '.join(lines[-2:])}"[:400]

    def _list(self, target: Target, mode: Mode | None, remote: str) -> dict[str, int]:
        result = self.run(target, mode, "lsjson", "--recursive", "--files-only", "--no-modtime", "--no-mimetype",
                          remote, timeout=600)
        if result.returncode in NOT_FOUND:
            return {}
        if result.returncode != 0:
            raise BackupError(f"cannot list {target.name}: {self.error(result)}")
        try:
            return {entry["Path"]: int(entry["Size"]) for entry in json.loads(result.stdout or "[]")}
        except (ValueError, KeyError, TypeError):
            raise BackupError(f"cannot list {target.name}: rclone printed no file list") from None

    def listing(self, target: Target, mode: Mode) -> dict[str, int]:
        """Path -> size of every file of the backup. A wrong key shows as an empty folder: refuse it."""
        files = self._list(target, mode, self.where(target, mode))
        if mode.key and not files and self._list(target, None, rjoin(target.remote, mode.folder)):
            raise store.ArchiveError(f"{target.name}: {mode.folder}/ holds files that PAPER_BACKUP_KEY cannot read. "
                                     "It is not the key this backup was made with; nothing was changed")
        return files

    def _copy(self, target: Target, mode: Mode, src: str, dest: str, rels: list[str]) -> str | None:
        fd, listing = tempfile.mkstemp(prefix=".files-from-", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(rel + "\n" for rel in rels))
            result = self.run(target, mode, "copy", src, dest, "--files-from-raw", listing, "--no-traverse",
                              "--ignore-times", timeout=4 * 3600)
        finally:
            os.unlink(listing)
        return None if result.returncode == 0 else self.error(result)

    def upload(self, target: Target, mode: Mode, src: Path, rels: list[str]) -> str | None:
        return self._copy(target, mode, str(src), self.where(target, mode), rels)

    def download(self, target: Target, mode: Mode, dest: Path, rels: list[str]) -> str | None:
        dest.mkdir(parents=True, exist_ok=True)
        return self._copy(target, mode, self.where(target, mode), str(dest), rels)


# ---------------------------------------------------------------- what to back up


@dataclass
class Item:
    kind: str                     # document | extra
    key: str                      # document id, or the extra file's path in the data directory
    rel: str                      # path on the target
    sha256: str
    bytes: int
    state: str = ""               # a document's state when it was archived

    def entry(self) -> dict:
        out = {"file": self.rel, "sha256": self.sha256, "bytes": self.bytes}
        return {**out, "state": self.state} if self.kind == "document" else out


def document_rel(doc_id: str, sha: str) -> str:
    return f"documents/{doc_id}/{doc_id}.{sha[:16]}.tar"


def document_files(doc: store.Document) -> list[str]:
    """The files of a document that cannot be made again, in archive order."""
    originals = [o["file"] for o in doc.meta["originals"]]
    bad = [f for f in originals if not ORIGINAL_RE.fullmatch(f)]
    if bad:
        raise BackupError(f"{doc.id}: meta.json names an unexpected file ({bad[0]})")
    return sorted(originals + [f for f in DOCUMENT_FILES if (doc.path / f).is_file()])


def fingerprint(doc: store.Document) -> list:
    out = []
    for rel in document_files(doc):
        stat = (doc.path / rel).stat()
        out.append([rel, stat.st_size, stat.st_mtime_ns])
    return out


def build_archive(doc: store.Document, target: Path) -> tuple[str, int]:
    """A reproducible tar of the document: the same files always give the same bytes. Returns sha256, size."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tarfile.open(tmp, "w", format=tarfile.PAX_FORMAT) as tar:
        for rel in document_files(doc):
            data = (doc.path / rel).read_bytes()
            info = tarfile.TarInfo(f"{doc.id}/{rel}")
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    os.replace(tmp, target)
    return store.sha256(target), target.stat().st_size


class Staging:
    """The files of one run, built once and shared by the targets."""

    def __init__(self, root: Path, state: dict):
        self.root, self.cache = root, state.setdefault("archives", {})
        self.dir = Path(tempfile.mkdtemp(prefix=".staging-", dir=root / "backup"))
        self.docs = {doc.id: doc for doc in store.documents(root)}

    def items(self) -> tuple[list[Item], dict[str, str]]:
        """What the backup should hold now, and the documents that could not be read."""
        items, errors = [], {}
        for doc in self.docs.values():
            try:
                mark = fingerprint(doc)
                cached = self.cache.get(doc.id)
                if not (isinstance(cached, dict) and cached.get("fingerprint") == mark
                        and SHA_RE.fullmatch(str(cached.get("sha256"))) and isinstance(cached.get("bytes"), int)):
                    sha, size = build_archive(doc, self.dir / "new.tar")
                    target = self.dir / document_rel(doc.id, sha)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(self.dir / "new.tar", target)
                    cached = self.cache[doc.id] = {"fingerprint": mark, "sha256": sha, "bytes": size}
                items.append(Item("document", doc.id, document_rel(doc.id, cached["sha256"]), cached["sha256"],
                                  cached["bytes"], doc.state()))
            except (OSError, KeyError, ValueError, BackupError) as exc:
                errors[doc.id] = str(exc)
        for gone in set(self.cache) - set(self.docs):
            del self.cache[gone]
        for path, pattern in EXTRAS.items():
            source = self.root / path
            if source.is_file():
                sha = store.sha256(source)
                items.append(Item("extra", path, pattern.format(key=sha[:16]), sha, source.stat().st_size))
        return items, errors

    def ensure(self, item: Item) -> None:
        """Put the item's file into the staging folder, exactly as it was hashed."""
        target = self.dir / item.rel
        if target.is_file():
            return
        if item.kind == "document":
            sha, _ = build_archive(self.docs[item.key], target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / item.key, target)
            sha = store.sha256(target)
        if sha != item.sha256:
            target.unlink()
            raise BackupError(f"{item.key} changed while it was backed up; the next run takes it")

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------- state and manifest


def state_path(root: Path) -> Path:
    return root / "backup" / "state.json"


def load_state(root: Path) -> dict:
    try:
        state = json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or not isinstance(state.get("archives", {}), dict) \
            or not isinstance(state.get("targets", {}), dict):
        state = {}
    state.setdefault("archives", {})
    state.setdefault("targets", {})
    return state


def entries(manifest, kind: str) -> dict[str, dict]:
    """The well-formed entries of a manifest. Nothing in it is trusted as a path: the file name must be the
    one this tool gives that document or extra file with that sha256."""
    out = {}
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get(kind), dict):
        return out
    for key, entry in manifest[kind].items():
        if not (isinstance(entry, dict) and isinstance(entry.get("bytes"), int)
                and SHA_RE.fullmatch(str(entry.get("sha256")))):
            continue
        if kind == "documents":
            expected = document_rel(key, entry["sha256"]) if DOC_ID_RE.fullmatch(key) else None
        else:
            expected = EXTRAS[key].format(key=entry["sha256"][:16]) if key in EXTRAS else None
        if expected and entry.get("file") == expected:
            out[key] = entry
    return out


def read_manifest(rclone: Rclone, target: Target, mode: Mode, folder: Path) -> dict | None:
    """manifest.json of a target, or None when it has none or it cannot be read."""
    shutil.rmtree(folder, ignore_errors=True)
    if rclone.download(target, mode, folder, ["manifest.json"]) is not None:
        return None
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- backup


def backup_target(rclone: Rclone, target: Target, mode: Mode, staging: Staging, items: list[Item], tstate: dict) -> dict:
    listed = rclone.listing(target, mode)
    missing = [i for i in items if listed.get(i.rel) != i.bytes]
    errors, ready = [], []
    for item in missing:
        try:
            staging.ensure(item)
            ready.append(item)
        except (OSError, BackupError) as exc:
            errors.append(str(exc))
    if ready:
        failed = rclone.upload(target, mode, staging.dir, [i.rel for i in ready])
        listed = rclone.listing(target, mode)              # only what is listed now has arrived
        if failed:
            errors.append(failed)
    uploaded = [i for i in ready if listed.get(i.rel) == i.bytes]

    previous = tstate.get("manifest")
    if previous is None and "manifest.json" in listed:     # the state was lost: the target knows its last versions
        previous = read_manifest(rclone, target, mode, staging.dir / f".manifest-{target.name}")
        tstate["manifest_bytes"] = listed["manifest.json"]
    manifest = {"format": FORMAT, "documents": {}, "extras": {}}
    for item in items:
        kind = "documents" if item.kind == "document" else "extras"
        old = entries(previous, kind).get(item.key)
        if listed.get(item.rel) == item.bytes:
            manifest[kind][item.key] = item.entry()
        elif old and listed.get(old["file"]) == old["bytes"]:
            manifest[kind][item.key] = old                 # until the new version is there, the last one counts
    same = previous is not None and all(entries(previous, k) == manifest[k] for k in ("documents", "extras"))
    if same and listed.get("manifest.json") == tstate.get("manifest_bytes"):
        manifest = previous
    else:
        manifest["updated_at"] = store.now_iso()
        folder = staging.dir / f".manifest-{target.name}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        store.write_json(folder / "manifest.json", manifest)
        failed = rclone.upload(target, mode, folder, ["manifest.json"])
        if failed:
            raise BackupError(f"manifest.json not written, the target still shows the previous backup ({failed})")
        tstate["manifest_bytes"] = (folder / "manifest.json").stat().st_size
    pending = len(missing) - len(uploaded)
    tstate.update({"manifest": manifest, "checked_at": store.now_iso(), "pending": pending,
                   "error": f"{pending} file(s) not uploaded; retried next run ({'; '.join(errors)[:300]})"
                   if pending else ""})
    if uploaded:
        tstate["uploaded_at"] = tstate["checked_at"]
    return {"documents": sum(1 for i in items if i.kind == "document"),
            "backed_up": sum(1 for i in items if i.kind == "document" and listed.get(i.rel) == i.bytes),
            "uploaded": len(uploaded), "pending": pending, "error": tstate["error"]}


def backup(root: Path, targets: dict[str, Target], mode: Mode, rclone: Rclone) -> dict:
    """Upload what each target lacks. Returns {"mode", "targets": {name: counts or error}, "unreadable": {...}}."""
    state = load_state(root)
    staging = Staging(root, state)
    report = {"mode": mode.folder, "targets": {}, "unreadable": {}}
    try:
        items, report["unreadable"] = staging.items()
        for name, target in targets.items():
            tstate = state["targets"].setdefault(f"{name}/{mode.folder}", {})
            try:
                report["targets"][name] = backup_target(rclone, target, mode, staging, items, tstate)
            except BackupError as exc:
                tstate.update({"checked_at": store.now_iso(), "error": str(exc)})
                report["targets"][name] = {"error": str(exc)}
                log.warning("%s: %s", name, exc)
    finally:
        staging.close()
        store.write_json(state_path(root), state)
    return report


def failed(report: dict) -> bool:
    return bool(report["unreadable"]) or any(t.get("error") for t in report["targets"].values())


def status(root: Path, env: dict) -> dict:
    """What the last runs left, from state.json alone: no network, nothing changed."""
    out = {"targets": {}, "error": ""}
    try:
        targets, mode = targets_from_env(env), mode_from_env(env)
    except store.ArchiveError as exc:
        out["error"] = str(exc)
        return out
    out["mode"] = mode.folder
    state = load_state(root)
    current = {}
    for doc in store.documents(root):
        cached = state["archives"].get(doc.id)
        try:
            current[doc.id] = cached["sha256"] if cached and cached.get("fingerprint") == fingerprint(doc) else None
        except (OSError, KeyError, BackupError):
            current[doc.id] = None
    for name in targets:
        tstate = state["targets"].get(f"{name}/{mode.folder}")
        if not tstate:
            out["targets"][name] = {"documents": len(current), "backed_up": 0, "up_to_date": False, "never_run": True}
            continue
        held = entries(tstate.get("manifest"), "documents")
        done = sum(1 for doc_id, sha in current.items() if sha and held.get(doc_id, {}).get("sha256") == sha)
        out["targets"][name] = {"documents": len(current), "backed_up": done,
                                "up_to_date": done == len(current) and not tstate.get("error"),
                                "checked_at": tstate.get("checked_at"), "uploaded_at": tstate.get("uploaded_at"),
                                "error": tstate.get("error", "")}
    return out


# ---------------------------------------------------------------- restore


def extract_document(archive: Path, doc_id: str, folder: Path) -> None:
    """Unpack a document archive into `folder`. Only the files a document has, only under its own name."""
    seen = set()
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            prefix, _, rel = member.name.partition("/")
            if not member.isfile() or prefix != doc_id or rel in seen \
                    or not (rel in DOCUMENT_FILES or ORIGINAL_RE.fullmatch(rel)):
                raise BackupError(f"{doc_id}: the archive holds something that is not a document file ({member.name!r})")
            seen.add(rel)
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    if "meta.json" not in seen:
        raise BackupError(f"{doc_id}: the archive has no meta.json")


def restore(target: Target, mode: Mode, out: Path, rclone: Rclone) -> dict:
    """Write the backup of one target into a new data directory. Every file is checked against the sha256
    in the target's manifest.json. Returns {"documents": [...], "extras": [...], "errors": {...}}."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise store.ArchiveError(f"{out} is not an empty folder; restore into a new folder")
    listed = rclone.listing(target, mode)
    if "manifest.json" not in listed:
        raise BackupError(f"{target.name}: no backup found in {mode.folder}/ (no manifest.json)")
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".restore-", dir=out))
    report = {"documents": [], "extras": [], "errors": {}}
    try:
        manifest = read_manifest(rclone, target, mode, work / "manifest")
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise BackupError(f"{target.name}: manifest.json cannot be read or is not format {FORMAT}")
        wanted = {("documents", k): e for k, e in entries(manifest, "documents").items()}
        wanted.update({("extras", k): e for k, e in entries(manifest, "extras").items()})
        for kind in ("documents", "extras"):
            for key in set(manifest.get(kind) or {}) - {k for group, k in wanted if group == kind}:
                report["errors"][str(key)] = "its entry in manifest.json is not valid"
        failed_copy = rclone.download(target, mode, work / "files", [e["file"] for e in wanted.values()])
        for (kind, key), entry in sorted(wanted.items()):
            source = work / "files" / entry["file"]
            try:
                if not source.is_file():
                    raise BackupError(f"not downloaded ({failed_copy or 'missing on the target'})")
                if store.sha256(source) != entry["sha256"]:
                    raise BackupError("sha256 differs from the backup's manifest.json")
                if kind == "extras":
                    (out / key).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, out / key)
                    report["extras"].append(key)
                else:
                    partial = out / "documents" / f".{key}.partial"
                    try:
                        extract_document(source, key, partial)
                        os.replace(partial, out / "documents" / key)
                    finally:
                        shutil.rmtree(partial, ignore_errors=True)
                    report["documents"].append(key)
            except (BackupError, OSError, tarfile.TarError) as exc:
                report["errors"][key] = str(exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report
