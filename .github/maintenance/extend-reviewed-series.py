"""Append a small exact Git series while reusing previously verified source data.

No source edits are executed here. The normal publisher still checks every blob,
tree, commit header and remote branch before testing/publishing the merged series.
"""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys


def extend(path: Path, output: Path) -> None:
    manifest = json.loads(path.read_text())
    if "extension" not in manifest:
        output.write_bytes(path.read_bytes())
        return
    extension = manifest.pop("extension")
    pieces = []
    for item in manifest["parts"]:
        if not re.fullmatch(r"\.github/maintenance/parts/[0-9]+\.txt", item["path"]):
            raise ValueError("invalid original part path")
        data = Path(item["path"]).read_bytes()
        if len(data) > 65536 or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("original part checksum mismatch")
        pieces.append(data.strip())
    packed = base64.b64decode(b"".join(pieces), validate=True)
    if len(packed) > 4000000 or hashlib.sha256(packed).hexdigest() != manifest["sha256"]:
        raise ValueError("original series checksum mismatch")
    original = json.loads(gzip.decompress(packed))
    if (original["base"] != manifest["base"] or extension["base"] != original["head"]
            or extension["head"] != manifest["head"]
            or extension["schema"] != original["schema"]
            or extension["repository"] != original["repository"]):
        raise ValueError("extension does not continue the reviewed series")
    parent = original["head"]
    for commit in extension["commits"]:
        if commit["parent"] != parent:
            raise ValueError("noncontiguous extension")
        parent = commit["sha"]
    if parent != extension["head"]:
        raise ValueError("wrong extension head")
    original["commits"].extend(extension["commits"])
    original["head"] = extension["head"]
    packed = gzip.compress(json.dumps(original, separators=(",", ":")).encode(), mtime=0)
    encoded = base64.b64encode(packed)
    if len(encoded) > 56 * 60000:
        raise ValueError("extended submission is too large")
    manifest["parts"] = []
    for index, start in enumerate(range(0, len(encoded), 60000)):
        chunk = encoded[start:start + 60000]
        item = Path(f".github/maintenance/parts/{200 + index}.txt")
        if item.exists():
            raise ValueError("temporary part path already exists")
        item.write_bytes(chunk)
        manifest["parts"].append({"path": str(item), "sha256": hashlib.sha256(chunk).hexdigest()})
    manifest["sha256"] = hashlib.sha256(packed).hexdigest()
    output.write_text(json.dumps(manifest, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    extend(Path(sys.argv[1]), Path(sys.argv[2]))
