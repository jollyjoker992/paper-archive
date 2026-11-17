"""Load the mapped resources into a FHIR server (Medplum). Same method as garmin-archive's loader:
one transaction of conditional updates per document, so loading again changes nothing."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import MAPPING_VERSION, store

log = logging.getLogger("paper-archive")
QUOTA_WINDOW_S = 60          # Medplum's FHIR quota resets every minute
QUOTA_RETRIES = 10


class LoadError(Exception):
    """A request to the FHIR server failed for good."""


class RateLimited(LoadError):
    pass


def outcome_text(payload) -> str:
    if isinstance(payload, dict) and payload.get("resourceType") == "OperationOutcome":
        return "; ".join(f"{i.get('details', {}).get('text', i.get('code'))}"
                         + (f" @ {i['expression'][0]}" if i.get("expression") else "")
                         for i in payload.get("issue", []))[:500]
    return str(payload)[:300]


def _decode(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")[:500]


class FhirClient:
    """OAuth2 client-credentials FHIR client with retries and backoff."""

    def __init__(self, url: str, client_id: str, secret: str, *, max_attempts: int = 5, timeout: float = 60.0,
                 sleep=time.sleep):
        self.url = url.rstrip("/")
        self.client_id, self.secret = client_id, secret
        self.max_attempts, self.timeout, self._sleep = max_attempts, timeout, sleep
        self._token = ""
        self.requests = 0

    def _token_request(self) -> str:
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                       "client_secret": self.secret}).encode()
        status, body = self._send("POST", "/oauth2/token", form, "application/x-www-form-urlencoded",
                                  "application/json", auth=False)
        if status != 200:
            raise LoadError(f"token request failed: HTTP {status} {body!r:.200}")
        return body["access_token"]

    def request(self, method: str, path: str, body=None, *, raw: bytes | None = None,
                content_type: str = "application/fhir+json", ok=(200, 201)):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        status, payload = self._send(method, path, data, content_type, "application/fhir+json")
        if status not in ok:
            raise LoadError(f"{method} {path.split('?')[0]}: HTTP {status}: {outcome_text(payload)}")
        return status, payload

    def _send(self, method, path, data, content_type, accept, auth=True):
        refreshed, attempt = False, 0
        while True:
            attempt += 1
            headers = {"Accept": accept, "User-Agent": f"paper-archive/{MAPPING_VERSION}"}
            if data is not None:
                headers["Content-Type"] = content_type
            if auth:
                self._token = self._token or self._token_request()
                headers["Authorization"] = f"Bearer {self._token}"
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
            self.requests += 1
            delay = min(60.0, 2.0 ** attempt)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    return response.status, _decode(response.read())
            except urllib.error.HTTPError as exc:
                status, payload = exc.code, _decode(exc.read())
                if status == 401 and auth and not refreshed:
                    refreshed, self._token = True, ""
                    attempt -= 1
                    continue
                if status != 429 and status < 500:
                    return status, payload
                if status == 429:
                    after = exc.headers.get("Retry-After", "")
                    delay = float(after) if after.isdigit() else QUOTA_WINDOW_S
                reason = f"HTTP {status}: {outcome_text(payload)}"
            except (urllib.error.URLError, OSError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
            if attempt >= self.max_attempts:
                raise LoadError(f"{method} {path.split('?')[0]}: {reason} after {attempt} attempts")
            log.warning("%s %s: %s (attempt %d/%d), retrying in %.0fs", method, path.split("?")[0], reason,
                        attempt, self.max_attempts, delay)
            self._sleep(delay)


def ident_key(resource: dict) -> str:
    ident = resource["identifier"]
    ident = ident[0] if isinstance(ident, list) else ident          # Composition has a single identifier
    return f"{resource['resourceType']}|{ident['system']}|{ident['value']}"


def conditional_url(key: str) -> str:
    rtype, system, value = key.split("|", 2)
    return f"{rtype}?identifier={urllib.parse.quote(system, safe=':/')}|{urllib.parse.quote(value, safe=':/')}"


def transaction(resources: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "transaction", "entry": [
        {"resource": r, "request": {"method": "PUT", "url": conditional_url(ident_key(r))}} for r in resources]}


class Loader:
    def __init__(self, client: FhirClient, root: Path, project_id: str, sleep=time.sleep):
        self.client, self.root, self.project_id, self._sleep = client, root, project_id, sleep
        self.manifest_path = root / "fhir" / "load-manifest.json"
        self.manifest = {"project_id": project_id, "shared": {}, "documents": {}}
        if self.manifest_path.is_file():
            saved = json.loads(self.manifest_path.read_text())
            if saved.get("project_id") == project_id:
                self.manifest = saved
            else:
                log.warning("Server project changed (%s -> %s): everything will be loaded again",
                            saved.get("project_id"), project_id)

    def save(self) -> None:
        self.manifest["updated_at"] = store.now_iso()
        store.write_json(self.manifest_path, self.manifest)

    def load_batch(self, name: str, ndjson: Path, previous: dict, keep: frozenset = frozenset()) -> dict:
        resources = [json.loads(line) for line in ndjson.read_text(encoding="utf-8").splitlines() if line.strip()]
        binaries = dict(previous.get("binaries") or {})
        uploaded: list[str] = []
        for r in resources:
            if r["resourceType"] == "DocumentReference":
                self._attach_binary(r, binaries, uploaded)
        try:
            _, result = self.client.request("POST", "/fhir/R4", transaction(resources))
        except LoadError:
            for binary_id in uploaded:                      # do not leave unreferenced uploads behind
                self._delete(f"/fhir/R4/Binary/{binary_id}")
            raise
        statuses = [e.get("response", {}).get("status", "") for e in (result or {}).get("entry", [])]
        if any(s.startswith("429") for s in statuses):
            raise RateLimited(f"{name}: FHIR quota exhausted")
        if len(statuses) != len(resources) or not all(s.startswith("2") for s in statuses):
            bad = [f"{ident_key(r).split('|')[-1]}={st}" for r, st in zip(resources, statuses) if not st.startswith("2")]
            raise LoadError(f"{name}: transaction returned {len(statuses)} result(s) for {len(resources)} "
                            f"resource(s); failed entries: {bad[:10] or 'none listed'}")
        keys = sorted(ident_key(r) for r in resources)
        for key in sorted(set(previous.get("identifiers") or []) - set(keys) - keep):
            self._delete(f"/fhir/R4/{conditional_url(key)}")
            log.info("%s: deleted %s, no longer produced by the mapping", name, key)
        for doc_key, old in (previous.get("binaries") or {}).items():
            if binaries.get(doc_key, {}).get("id") != old["id"]:
                self._delete(f"/fhir/R4/Binary/{old['id']}")
        created = sum(1 for s in statuses if s.startswith("201"))
        return {"status": "ok", "sha256": hashlib.sha256(ndjson.read_bytes()).hexdigest(), "loaded_at": store.now_iso(),
                "resources": len(resources), "created": created, "updated": len(resources) - created,
                "identifiers": keys, "binaries": binaries}

    def load_with_quota(self, name: str, ndjson: Path, previous: dict, keep: frozenset = frozenset()) -> dict:
        for attempt in range(1, QUOTA_RETRIES + 1):
            try:
                return self.load_batch(name, ndjson, previous, keep)
            except RateLimited as exc:
                if attempt == QUOTA_RETRIES:
                    raise
                log.warning("%s; waiting %ds (attempt %d/%d)", exc, QUOTA_WINDOW_S, attempt, QUOTA_RETRIES)
                self._sleep(QUOTA_WINDOW_S)
        raise AssertionError("unreachable")

    def _attach_binary(self, doc: dict, binaries: dict, uploaded: list[str]) -> None:
        attachment = doc["content"][0]["attachment"]
        ext = [e for e in attachment.get("extension", []) if e["url"].endswith("/archive-path")]
        if not ext:
            return
        key = ident_key(doc)
        known = binaries.get(key)
        if known and known.get("hash") == attachment.get("hash"):
            attachment["url"] = f"Binary/{known['id']}"
            return
        path = self.root / ext[0]["valueString"]
        if not path.is_file():
            raise LoadError(f"{key}: {path} is missing")
        if base64.b64encode(hashlib.sha1(path.read_bytes()).digest()).decode() != attachment.get("hash"):
            raise LoadError(f"{key}: {path} does not match the mapped hash; map again")
        _, binary = self.client.request("POST", "/fhir/R4/Binary", raw=path.read_bytes(),
                                        content_type=attachment.get("contentType", "application/octet-stream"))
        attachment["url"] = f"Binary/{binary['id']}"
        binaries[key] = {"id": binary["id"], "hash": attachment["hash"], "bytes": path.stat().st_size}
        uploaded.append(binary["id"])

    def _delete(self, path: str) -> None:
        try:
            self.client.request("DELETE", path, ok=(200, 204, 404, 410))
        except LoadError as exc:
            log.warning("cleanup failed: %s", exc)


def load_all(root: Path, client: FhirClient, project_id: str, force: bool = False, sleep=time.sleep) -> dict:
    """Load shared.ndjson, then every mapped document that changed. Returns counts and failures."""
    fhir_dir = root / "fhir"
    if not (fhir_dir / "manifest.json").is_file():
        raise store.ArchiveError("nothing mapped yet: run map first")
    mapped = json.loads((fhir_dir / "manifest.json").read_text())["documents"]
    loader = Loader(client, root, project_id, sleep)
    result = {"loaded": [], "unchanged": [], "removed": [], "failed": {}}
    batches = [("shared", fhir_dir / "shared.ndjson", loader.manifest["shared"])]
    batches += [(doc.id, doc.path / "fhir.ndjson", loader.manifest["documents"].get(doc.id, {}))
                for doc in store.documents(root) if doc.id in mapped and (doc.path / "fhir.ndjson").is_file()]
    # A resource can move between documents (a vital sign printed on several papers belongs to the first):
    # nothing that any current batch still produces is ever deleted.
    keep = frozenset(ident_key(json.loads(line)) for _, path, _ in batches
                     for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    try:
        for name, path, previous in batches:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if not force and previous.get("status") == "ok" and previous.get("sha256") == digest:
                result["unchanged"].append(name)
                continue
            try:
                entry = loader.load_with_quota(name, path, previous, keep)
            except LoadError as exc:
                result["failed"][name] = str(exc)
                if name == "shared":
                    break                                    # without the Patient nothing else can resolve
                loader.manifest["documents"][name] = {**previous, "status": "failed", "error": str(exc)}
                continue
            if name == "shared":
                loader.manifest["shared"] = entry
            else:
                loader.manifest["documents"][name] = entry
            result["loaded"].append(name)
        # A document that lost its approval (or its folder) is taken off the server.
        current = {name for name, _, _ in batches}
        for name in sorted(set(loader.manifest["documents"]) - current):
            if "shared" in result["failed"]:
                break
            old = loader.manifest["documents"].pop(name)
            for key in set(old.get("identifiers") or []) - keep:
                loader._delete(f"/fhir/R4/{conditional_url(key)}")
            for binary in (old.get("binaries") or {}).values():
                loader._delete(f"/fhir/R4/Binary/{binary['id']}")
            result["removed"].append(name)
    finally:
        loader.save()
    result["requests"] = client.requests
    return result
