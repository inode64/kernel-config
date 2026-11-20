"""Snapshot download regressions using local archives instead of the network."""

import contextlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("download_kconfig", Path(__file__).resolve().parents[1] / "download-kconfig.py")
download = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download)


def release(version, moniker="longterm"):
    return {"version": version, "moniker": moniker,
            "source": f"https://cdn.kernel.org/linux-{version}.tar.xz"}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.release = release("6.12.100")
        self.root_name = "linux-6.12.100"
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def archive(self, mode="w:xz", extra=(), documentation=True):
        archive = self.root / "fixture.tar"
        entries = [("Kconfig", b"config ROOT\n"),
                   ("drivers/example/Kconfig.debug", b"config DEBUG\n"),
                   ("scripts/Kconfig.include", b"include\n"),
                   ("kernel/Kconfig.preempt", b"config PREEMPT\n"),
                   ("drivers/example/example.c", b"discard\n"),
                   ("discard/only.c", b"discard\n"),
                   ("empty/", None), ("Kconfig.directory/", None)]
        if documentation:
            entries.extend([("Documentation/index.rst", b"Docs\n"),
                            ("Documentation/images/logo.bin", bytes(range(256))),
                            ("Documentation/.hidden", b"hidden\n"),
                            ("Documentation/tool.sh", b"#!/bin/sh\nexit 0\n"),
                            ("Documentation/empty/", None)])
        with tarfile.open(archive, mode) as output:
            for name, content in entries:
                member = tarfile.TarInfo(f"{self.root_name}/{name}")
                member.mtime = 1700000000
                if content is None:
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o755
                    output.addfile(member)
                else:
                    member.size = len(content)
                    member.mode = 0o755 if name.endswith(".sh") else 0o644
                    output.addfile(member, io.BytesIO(content))
            for member in extra:
                output.addfile(member)
        return archive

    def run_download(self, archive):
        with archive.open("rb") as response:
            response.headers = {"Content-Length": str(archive.stat().st_size)}
            with patch.object(download.urllib.request, "urlopen", return_value=response) as urlopen:
                result = download.download_snapshot(self.release, self.output)
                request = urlopen.call_args.args[0]
                self.assertEqual(request.full_url, self.release["source"])
                self.assertEqual(request.get_header("User-agent"), "kernel-config-snapshots/1.0")
                return result

    def test_selects_numeric_latest_stable_and_mainline(self):
        releases = [release("6.12.9"), release("6.12.100"), release("6.18.5"),
                    release("6.18.10"), release("6.19.1", "stable"),
                    release("7.4-rc3", "mainline"), release("6.12.999-rc1", "stable")]
        self.assertEqual([item["version"] for item in download.select_releases(releases)],
                         ["6.12.100", "6.18.10", "7.4-rc3"])
        releases[-2] = release("7.4", "mainline")
        self.assertEqual(download.select_releases(releases)[-1]["version"], "7.4")

    def test_missing_branch_or_mainline_fails(self):
        for releases in ([release("6.18.1"), release("7.4", "mainline")],
                         [release("6.12.1"), release("6.18.1")]):
            with self.subTest(releases=releases), self.assertRaises(ValueError):
                download.select_releases(releases)

    def test_preserves_all_documentation_and_only_kconfigs_elsewhere(self):
        for mode in ("w:xz", "w:gz"):
            with self.subTest(mode=mode):
                self.output = self.root / mode.replace(":", "-")
                self.output.mkdir()
                link = tarfile.TarInfo(f"{self.root_name}/Documentation/index-link")
                link.type = tarfile.SYMTYPE
                link.linkname = "index.rst"
                archive = self.archive(mode, extra=[link])
                tree = self.run_download(archive)
                with tarfile.open(archive) as original:
                    for member in original:
                        relative = Path(member.name).relative_to(self.root_name)
                        target = tree / relative
                        if relative.parts[0] == "Documentation":
                            if member.isfile():
                                self.assertEqual(target.read_bytes(), original.extractfile(member).read())
                                self.assertEqual(target.stat().st_mode & 0o777, member.mode)
                                self.assertEqual(target.stat().st_mtime, member.mtime)
                            elif member.isdir():
                                self.assertTrue(target.is_dir())
                            elif member.issym():
                                self.assertEqual(str(target.readlink()), member.linkname)
                outside = {path.relative_to(tree).as_posix() for path in tree.rglob("*")
                           if path.is_file() and path.relative_to(tree).parts[0] != "Documentation"}
                self.assertEqual(outside, {"Kconfig", "drivers/example/Kconfig.debug",
                                           "scripts/Kconfig.include", "kernel/Kconfig.preempt"})
                for path in tree.rglob("*"):
                    if path.is_dir() and path.relative_to(tree).parts[0] != "Documentation":
                        self.assertTrue(any(path.iterdir()), path)
                self.assertEqual(list(self.output.iterdir()), [tree])

    def test_existing_directory_and_dangling_symlink_are_not_overwritten(self):
        target = self.output / self.root_name
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    target.symlink_to(self.root / "missing")
                else:
                    target.mkdir()
                    (target / "keep").write_text("original")
                with patch.object(download.urllib.request, "urlopen") as urlopen:
                    with self.assertRaises(FileExistsError):
                        download.download_snapshot(self.release, self.output)
                    urlopen.assert_not_called()
                if symlink:
                    self.assertTrue(target.is_symlink())
                    target.unlink()
                else:
                    self.assertEqual((target / "keep").read_text(), "original")
                    (target / "keep").unlink()
                    target.rmdir()

    def test_incomplete_archive_is_not_published(self):
        archive = self.archive()
        archive.write_bytes(archive.read_bytes()[:100])
        with self.assertRaises((tarfile.TarError, EOFError)):
            self.run_download(archive)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_missing_documentation_is_not_published(self):
        with self.assertRaisesRegex(ValueError, "lacks Kconfig or Documentation"):
            self.run_download(self.archive(documentation=False))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_archive_path_traversal_and_escaping_symlink_are_rejected(self):
        traversal = tarfile.TarInfo(f"{self.root_name}/../../Kconfig")
        symlink = tarfile.TarInfo(f"{self.root_name}/Documentation/escape")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "../../../../outside"
        for member in (traversal, symlink):
            with self.subTest(member=member.name), self.assertRaises((ValueError, tarfile.TarError)):
                self.run_download(self.archive(extra=[member]))
            self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse((self.root / "outside").exists())

    def test_main_preflights_all_destinations_before_downloading(self):
        releases = [release("6.12.100"), release("6.18.10"), release("7.4-rc3", "mainline")]
        (self.output / "linux-7.4-rc3").mkdir()
        metadata = io.BytesIO(json.dumps({"releases": releases}).encode())
        with patch.object(download.urllib.request, "urlopen", return_value=metadata) as urlopen:
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(download.main([str(self.output)]), 1)
            self.assertIn("Refusing to overwrite", errors.getvalue())
            urlopen.assert_called_once_with(download.RELEASES_URL, timeout=30)
        self.assertEqual([path.name for path in self.output.iterdir()], ["linux-7.4-rc3"])

    def test_main_downloads_all_three_releases_in_current_directory(self):
        releases = [release("6.12.100"), release("6.18.10"), release("7.4-rc3", "mainline")]
        responses = {download.RELEASES_URL: json.dumps({"releases": releases}).encode()}
        for item in releases:
            self.root_name = f"linux-{item['version']}"
            mode = "w:gz" if item["moniker"] == "mainline" else "w:xz"
            responses[item["source"]] = self.archive(mode).read_bytes()

        def urlopen(request, timeout):
            url = request if isinstance(request, str) else request.full_url
            response = io.BytesIO(responses[url])
            response.headers = {"Content-Length": str(len(responses[url]))}
            return response

        with (patch.object(download.urllib.request, "urlopen", side_effect=urlopen),
              patch.object(Path, "cwd", return_value=self.output)):
            self.assertEqual(download.main([]), 0)
        self.assertEqual({path.name for path in self.output.iterdir()},
                         {"linux-6.12.100", "linux-6.18.10", "linux-7.4-rc3"})
        for tree in self.output.iterdir():
            self.assertEqual((tree / "Kconfig").read_bytes(), b"config ROOT\n")
            self.assertEqual((tree / "Documentation/index.rst").read_bytes(), b"Docs\n")


if __name__ == "__main__":
    unittest.main()
