#!/usr/bin/env python3
"""Create a deterministic, checksum-locked Agent Fleet runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import zipfile
from typing import Any


APPLICATION_ID = "com.yaakovch.fleet"
PREFIX = "/data/data/com.yaakovch.fleet/files/usr"
REPOSITORY = "https://github.com/yaakovch/agent-fleet-termux-packages"
COMMIT = re.compile(r"^[a-f0-9]{40}$")


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_path(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo: pathlib.Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *arguments], text=True).strip()


def deb_fields(path: pathlib.Path) -> dict[str, str]:
    output = subprocess.check_output(
        ["dpkg-deb", "--field", str(path), "Package", "Version", "Architecture", "Source", "Homepage", "Description"],
        text=True,
    )
    values: dict[str, str] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith(" ") and current:
            values[current] += " " + line.strip()
        elif ": " in line:
            current, value = line.split(": ", 1)
            values[current] = value.strip()
    for field in ("Package", "Version", "Architecture"):
        if not values.get(field):
            raise ValueError(f"{path.name} is missing Debian field {field}")
    return values


def recipe_metadata(repo: pathlib.Path, source: str) -> tuple[str, str]:
    source = source.split(" ", 1)[0]
    candidates = [repo / directory / source / "build.sh" for directory in ("packages", "root-packages", "x11-packages")]
    recipe = next((candidate for candidate in candidates if candidate.is_file()), None)
    if recipe is None:
        return "NOASSERTION", "NOASSERTION"
    text = recipe.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^TERMUX_PKG_LICENSE=["\']?([^"\'\n]+)', text, re.MULTILINE)
    return (match.group(1).strip() if match else "NOASSERTION"), recipe.relative_to(repo).as_posix()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def add(archive: zipfile.ZipFile, name: str, payload: bytes, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    archive.writestr(info, payload)


def spdx(lock: dict[str, Any]) -> dict[str, Any]:
    packages = []
    relationships = []
    for item in lock["packages"]:
        identifier = "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", item["name"])
        packages.append({
            "SPDXID": identifier,
            "name": item["name"],
            "versionInfo": item["version"],
            "downloadLocation": lock["bundleUrl"],
            "filesAnalyzed": False,
            "checksums": [{"algorithm": "SHA256", "checksumValue": item["sha256"]}],
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": item["license"],
            "copyrightText": "NOASSERTION",
            "sourceInfo": f"Recipe {item['recipe']}; upstream {item['homepage']}",
        })
        relationships.append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": identifier,
        })
    lock_digest = digest_bytes(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode())
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Agent-Fleet-Termux-{lock['architecture']}",
        "documentNamespace": f"https://agent-fleet.local/sbom/{lock_digest}",
        "creationInfo": {"created": "1970-01-01T00:00:00Z", "creators": ["Tool: package-runtime.py"]},
        "packages": packages,
        "relationships": relationships,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("aarch64", "x86_64"), required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--bootstrap", type=pathlib.Path, required=True)
    parser.add_argument("--packages", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    repo = pathlib.Path(__file__).resolve().parents[2]
    upstream_commit = (repo / "agent-fleet/upstream-commit.txt").read_text().strip()
    fork_commit = os.environ.get("GITHUB_SHA") or git(repo, "rev-parse", "HEAD")
    if not COMMIT.fullmatch(upstream_commit) or not COMMIT.fullmatch(fork_commit):
        raise SystemExit("runtime source commits must be full 40-character Git commit IDs")
    if not args.bootstrap.is_file():
        raise SystemExit(f"missing bootstrap: {args.bootstrap}")
    roots = sorted(set((repo / "agent-fleet/runtime-roots.txt").read_text().split()))
    bundle_name = f"agent-fleet-runtime-{args.architecture}.zip"
    bundle_url = f"{REPOSITORY}/releases/download/{args.release_tag}/{bundle_name}"

    records = []
    payloads: list[tuple[str, pathlib.Path]] = []
    names: set[str] = set()
    for path in sorted(args.packages.glob("*.deb")):
        fields = deb_fields(path)
        if fields["Architecture"] not in (args.architecture, "all") or fields["Package"].endswith("-static"):
            continue
        if fields["Package"] in names:
            raise SystemExit(f"duplicate package output: {fields['Package']}")
        names.add(fields["Package"])
        safe_name = path.name.replace(":", "_")
        archive_name = f"packages/{safe_name}"
        license_name, recipe = recipe_metadata(repo, fields.get("Source", fields["Package"]))
        records.append({
            "name": fields["Package"],
            "version": fields["Version"],
            "architecture": fields["Architecture"],
            "file": safe_name,
            "sha256": digest_path(path),
            "size": path.stat().st_size,
            "sourcePackage": fields.get("Source", fields["Package"]).split(" ", 1)[0],
            "recipe": recipe,
            "homepage": fields.get("Homepage", "NOASSERTION"),
            "license": license_name,
            "description": fields.get("Description", ""),
        })
        payloads.append((archive_name, path))
    if not records or not set(roots).issubset(names):
        missing = sorted(set(roots) - names)
        raise SystemExit(f"built package closure is incomplete: {missing}")

    lock = {
        "schemaVersion": 2,
        "applicationId": APPLICATION_ID,
        "prefix": PREFIX,
        "architecture": args.architecture,
        "repository": REPOSITORY,
        "bundleUrl": bundle_url,
        "upstreamCommit": upstream_commit,
        "forkCommit": fork_commit,
        "rootPackages": roots,
        "totalSize": sum(item["size"] for item in records),
        "packages": records,
    }
    lock_payload = json_bytes(lock)
    sbom_payload = json_bytes(spdx(lock))
    bootstrap_payload = args.bootstrap.read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    bundle = args.output / bundle_name
    with zipfile.ZipFile(bundle, "w", allowZip64=True) as archive:
        add(archive, "agent-fleet-package-lock-v2.json", lock_payload)
        add(archive, "agent-fleet-packages.spdx.json", sbom_payload)
        add(archive, args.bootstrap.name, bootstrap_payload)
        for archive_name, path in payloads:
            add(archive, archive_name, path.read_bytes())

    descriptor = {
        "schemaVersion": 1,
        "applicationId": APPLICATION_ID,
        "prefix": PREFIX,
        "architecture": args.architecture,
        "upstreamCommit": upstream_commit,
        "forkCommit": fork_commit,
        "releaseTag": args.release_tag,
        "url": bundle_url,
        "file": bundle.name,
        "sha256": digest_path(bundle),
        "size": bundle.stat().st_size,
        "bootstrapFile": args.bootstrap.name,
        "bootstrapSha256": digest_bytes(bootstrap_payload),
        "bootstrapSize": len(bootstrap_payload),
        "packageLockSha256": digest_bytes(lock_payload),
        "packageLockSize": len(lock_payload),
        "sbomSha256": digest_bytes(sbom_payload),
        "sbomSize": len(sbom_payload),
        "packageCount": len(records),
        "packagePayloadSize": lock["totalSize"],
    }
    descriptor_path = args.output / f"agent-fleet-runtime-{args.architecture}.json"
    descriptor_path.write_bytes(json_bytes(descriptor))
    print(json.dumps({"bundle": str(bundle), "descriptor": str(descriptor_path), "packages": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
