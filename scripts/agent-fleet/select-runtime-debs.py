#!/usr/bin/env python3
"""Select the install-time package closure from a Termux build output."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path


PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
DEPENDENCY_NAME = re.compile(r"^\s*([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9_-]+)?")
MAXIMUM_PACKAGES = 128
MAXIMUM_PAYLOAD_SIZE = 96 * 1024 * 1024


@dataclass(frozen=True)
class Package:
    name: str
    path: Path
    essential: bool
    dependencies: tuple[tuple[str, ...], ...]
    provides: tuple[str, ...]


def names(value: str, separator: str) -> tuple[str, ...]:
    result = []
    for item in value.split(separator):
        match = DEPENDENCY_NAME.match(item)
        if match:
            result.append(match.group(1))
    return tuple(result)


def dependency_groups(value: str) -> tuple[tuple[str, ...], ...]:
    groups = []
    for item in value.split(","):
        alternatives = names(item, "|")
        if alternatives:
            groups.append(alternatives)
    return tuple(groups)


def read_package(path: Path, architecture: str) -> Package | None:
    completed = subprocess.run(
        ["dpkg-deb", "--field", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    fields = Parser().parsestr(completed.stdout)
    name = fields.get("Package", "")
    package_architecture = fields.get("Architecture", "")
    if not PACKAGE_NAME.fullmatch(name):
        raise ValueError(f"invalid package name in {path.name}")
    if package_architecture not in {architecture, "all"}:
        return None
    dependencies = dependency_groups(
        ",".join(filter(None, (fields.get("Pre-Depends"), fields.get("Depends"))))
    )
    return Package(
        name=name,
        path=path,
        essential=fields.get("Essential", "").lower() == "yes",
        dependencies=dependencies,
        provides=names(fields.get("Provides", ""), ","),
    )


def select(packages: dict[str, Package], roots: tuple[str, ...]) -> set[str]:
    providers: dict[str, list[str]] = {}
    for package in packages.values():
        for provided in package.provides:
            providers.setdefault(provided, []).append(package.name)

    missing_roots = sorted(set(roots) - set(packages))
    if missing_roots:
        raise ValueError(f"runtime roots are missing: {', '.join(missing_roots)}")

    selected: set[str] = set()
    pending = sorted(set(roots) | {item.name for item in packages.values() if item.essential})
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        package = packages[name]
        selected.add(name)
        for alternatives in package.dependencies:
            dependency = next((item for item in alternatives if item in packages), None)
            if dependency is None:
                candidates = sorted({candidate for item in alternatives for candidate in providers.get(item, [])})
                if len(candidates) != 1:
                    rendered = " | ".join(alternatives)
                    if not candidates:
                        raise ValueError(f"{name} has an unavailable dependency: {rendered}")
                    raise ValueError(f"{name} has an ambiguous virtual dependency: {rendered}")
                dependency = candidates[0]
            if dependency not in selected:
                pending.append(dependency)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("aarch64", "x86_64"), required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--roots", type=Path, required=True)
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args()

    roots = tuple(sorted(set(args.roots.read_text(encoding="utf-8").split())))
    if not roots or any(not PACKAGE_NAME.fullmatch(item) for item in roots):
        raise SystemExit("runtime roots file is empty or invalid")

    packages: dict[str, Package] = {}
    ignored: list[Path] = []
    for path in sorted(args.packages.glob("*.deb")):
        package = read_package(path, args.architecture)
        if package is None:
            ignored.append(path)
            continue
        if package.name in packages:
            raise SystemExit(f"duplicate package output: {package.name}")
        packages[package.name] = package
    if not packages:
        raise SystemExit("no package outputs were found")

    try:
        selected = select(packages, roots)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    excluded = sorted(set(packages) - selected)
    selected_size = sum(packages[name].path.stat().st_size for name in selected)
    if len(selected) > MAXIMUM_PACKAGES or selected_size > MAXIMUM_PAYLOAD_SIZE:
        raise SystemExit(
            f"runtime closure is unexpectedly large: {len(selected)} packages, {selected_size} bytes"
        )
    if args.prune:
        for name in excluded:
            packages[name].path.unlink()
        for path in ignored:
            path.unlink()

    print(json.dumps({
        "architecture": args.architecture,
        "roots": list(roots),
        "selectedPackages": len(selected),
        "selectedPayloadSize": selected_size,
        "excludedPackages": len(excluded) + len(ignored),
        "pruned": args.prune,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
