#!/usr/bin/env python3
"""Download current Linux 6.12, 6.18 and mainline Kconfig/Documentation snapshots."""

import argparse
import http.client
import json
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

RELEASES_URL = "https://www.kernel.org/releases.json"
BRANCHES = ("6.12", "6.18")


def select_releases(releases):
    selected = []
    for branch in BRANCHES:
        matches = [release for release in releases
                   if re.fullmatch(re.escape(branch) + r"\.\d+", release["version"])
                   and release["moniker"] in ("stable", "longterm")]
        if not matches:
            raise ValueError(f"No stable release found for {branch}.x")
        selected.append(max(matches, key=lambda release: int(release["version"].split(".")[-1])))
    mainline = [release for release in releases if release["moniker"] == "mainline"]
    if len(mainline) != 1:
        raise ValueError("Expected one mainline release in kernel.org metadata")
    selected.extend(mainline)
    for release in selected:
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:-rc\d+)?", release["version"]):
            raise ValueError(f"Invalid kernel version: {release['version']!r}")
        if not (release.get("source") or "").startswith("https://"):
            raise ValueError(f"Missing HTTPS source URL for {release['version']}")
    return selected


def keep_member(member, root_name):
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root_name:
        raise ValueError(f"Unexpected archive path: {member.name!r}")
    relative = path.parts[1:]
    if not relative:
        return False
    # Only Documentation retains explicit directories, including empty ones.
    return relative[0] == "Documentation" or (not member.isdir() and path.name.startswith("Kconfig"))


def check_destination(target):
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite {target}; choose another destination")


def download_snapshot(release, destination):
    root_name = f"linux-{release['version']}"
    target = destination / root_name
    check_destination(target)
    print(f"Downloading {release['version']}: {release['source']}", flush=True)
    # Stage on the destination filesystem so incomplete trees are never published.
    with tempfile.TemporaryDirectory(prefix=".kconfig-download-", dir=destination) as temporary:
        stage = Path(temporary)
        archive_path = stage / "source.tar"
        # git.kernel.org rejects the default Python urllib user agent for snapshots.
        request = urllib.request.Request(release["source"], headers={"User-Agent": "kernel-config-snapshots/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            with archive_path.open("wb") as output:
                shutil.copyfileobj(response, output)
            expected = response.headers.get("Content-Length")
            if expected is not None and archive_path.stat().st_size != int(expected):
                raise ValueError(f"Incomplete download for {release['version']}")
        print(f"Extracting Kconfig* and Documentation for {release['version']}...", flush=True)
        with tarfile.open(archive_path, "r:*") as archive:
            members = [member for member in archive if keep_member(member, root_name)]
            archive.extractall(stage / "tree", members=members, filter="data")
        tree = stage / "tree" / root_name
        if not (tree / "Kconfig").is_file() or not (tree / "Documentation").is_dir():
            raise ValueError(f"Archive lacks Kconfig or Documentation for {release['version']}")
        kconfigs = sum(not member.isdir() and PurePosixPath(member.name).name.startswith("Kconfig")
                       for member in members)
        documents = sum(not member.isdir() and PurePosixPath(member.name).parts[1] == "Documentation"
                        for member in members)
        check_destination(target)
        tree.rename(target)
    print(f"Created {target}: {kconfigs} Kconfig* files, {documents} Documentation files/links", flush=True)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path, default=Path.cwd(),
                        help="parent directory for linux-VERSION folders (default: current directory)")
    args = parser.parse_args(argv)
    try:
        if not hasattr(tarfile, "data_filter"):
            raise ValueError("Python 3.11.8+ with tarfile extraction filters is required")
        with urllib.request.urlopen(RELEASES_URL, timeout=30) as response:
            releases = select_releases(json.load(response)["releases"])
        destination = args.destination.expanduser().resolve()
        for release in releases:
            check_destination(destination / f"linux-{release['version']}")
        destination.mkdir(parents=True, exist_ok=True)
        for release in releases:
            download_snapshot(release, destination)
    except (OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError,
            urllib.error.URLError, http.client.HTTPException) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; temporary files removed.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
