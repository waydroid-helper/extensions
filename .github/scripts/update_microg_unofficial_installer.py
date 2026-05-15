#!/usr/bin/env python3

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


OWNER = "micro5k"
REPO = "microg-unofficial-installer"
RELEASES_API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases"
ASSET_PATTERN = re.compile(
    r"^microg-unofficial-installer-(?P<asset_version>.+)-oss-by-ale5000-signed\.zip$"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = REPO_ROOT / "microg" / "microg-unofficial-installer"
def fetch_releases():
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "extensions-microg-unofficial-installer-updater",
        },
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def list_existing_template_names():
    if not PROJECT_DIR.exists():
        return []
    return sorted(
        path.name for path in PROJECT_DIR.iterdir() if path.is_dir() and (path / "EXTENSION").is_file()
    )


def choose_fallback_template_dir():
    candidates = list_existing_template_names()
    if not candidates:
        raise RuntimeError("No existing microg-unofficial-installer template directory was found")
    return PROJECT_DIR / candidates[-1]


def download_file(url, destination):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "extensions-microg-unofficial-installer-updater"},
    )
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def sha256sum(file_path):
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_module_version(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        content = archive.read("module.prop").decode("utf-8", "replace")
    for line in content.splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"Unable to read module version from {zip_path}")


def detect_zip_layout(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    return {
        "has_files_dir": any(name.startswith("files/") for name in names),
        "has_origin_sysconfig": any(name.startswith("origin/etc/sysconfig/") for name in names),
    }


def validate_template_compatibility(template_dir, zip_layout):
    extension_text = (template_dir / "EXTENSION").read_text()
    expects_files_dir = "$srcdir/files/" in extension_text
    handles_origin_sysconfig = "origin/etc/sysconfig" in extension_text

    if not zip_layout["has_files_dir"] and expects_files_dir:
        raise RuntimeError(
            f"Template {template_dir.name} expects files/ but the new archive does not provide it"
        )
    if zip_layout["has_origin_sysconfig"] and not handles_origin_sysconfig:
        raise RuntimeError(
            f"Template {template_dir.name} does not handle origin/etc/sysconfig from the new archive"
        )


def build_directory_name(release_tag, asset_version, module_version):
    # Tagged releases map to the human-readable version exposed by module.prop.
    # Nightly releases are mutable, so we keep the asset suffix to preserve a
    # unique, append-only history for each snapshot.
    if release_tag == "nightly":
        return asset_version
    return module_version


def replace_first_match(text, pattern, replacement):
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Failed to update pattern: {pattern}")
    return updated


def update_extension_file(extension_path, module_version, download_url, sha256):
    content = extension_path.read_text()
    content = replace_first_match(content, r'^version="[^"]+"$', f'version="{module_version}"')
    content = replace_first_match(
        content,
        r'^source=\(".*"\)$',
        f'source=("${{name}}-${{version}}.zip::{download_url}")',
    )
    content = replace_first_match(
        content,
        r'^sha256sums=\("[0-9a-f]+"\)$',
        f'sha256sums=("{sha256}")',
    )
    extension_path.write_text(content)


def generate_metainfo(extension_path, metainfo_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "extract_meta.py"), str(extension_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    metainfo_path.write_text(result.stdout)


def create_version_dir(template_dir, directory_name, module_version, download_url, sha256):
    destination = PROJECT_DIR / directory_name
    if destination.exists():
        return None

    shutil.copytree(template_dir, destination)
    extension_path = destination / "EXTENSION"
    metainfo_path = destination / "metainfo.json"

    update_extension_file(extension_path, module_version, download_url, sha256)
    generate_metainfo(extension_path, metainfo_path)
    return destination


def known_directory_names():
    if not PROJECT_DIR.exists():
        return set()
    return {path.name for path in PROJECT_DIR.iterdir() if path.is_dir()}


def predicted_directory_name_for_asset(release_tag, asset_version):
    return asset_version if release_tag == "nightly" else release_tag


def build_release_asset_list(releases):
    items = []
    for release in releases:
        if release.get("draft"):
            continue
        release_tag = release["tag_name"]
        for asset in release.get("assets", []):
            asset_name = asset["name"]
            match = ASSET_PATTERN.match(asset_name)
            if not match:
                continue
            asset_version = match.group("asset_version")
            items.append(
                {
                    "release_tag": release_tag,
                    "asset_name": asset_name,
                    "asset_version": asset_version,
                    "predicted_directory_name": predicted_directory_name_for_asset(
                        release_tag, asset_version
                    ),
                    "asset_url": asset["browser_download_url"],
                }
            )
    return items


def choose_template_dir_for_index(index, release_assets, existing_names):
    # New versions should inherit from the nearest older version that already
    # exists in the repository instead of a hardcoded template. This keeps the
    # generated EXTENSION aligned with the most recent upstream packaging rules.
    for candidate in release_assets[index + 1 :]:
        candidate_name = candidate["predicted_directory_name"]
        if candidate_name in existing_names:
            return PROJECT_DIR / candidate_name
    return choose_fallback_template_dir()


def main():
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    releases = fetch_releases()
    created = []
    existing_names = known_directory_names()
    release_assets = build_release_asset_list(releases)

    with tempfile.TemporaryDirectory(prefix="microg-unofficial-installer-") as tmpdir:
        temp_root = Path(tmpdir)

        for index, asset in enumerate(release_assets):
            release_tag = asset["release_tag"]
            asset_version = asset["asset_version"]
            predicted_directory_name = asset["predicted_directory_name"]

            if predicted_directory_name in existing_names:
                continue

            asset_url = asset["asset_url"]
            local_zip = temp_root / asset["asset_name"]
            download_file(asset_url, local_zip)

            module_version = read_module_version(local_zip)
            directory_name = build_directory_name(release_tag, asset_version, module_version)
            if directory_name in existing_names:
                continue

            template_dir = choose_template_dir_for_index(index, release_assets, existing_names)
            validate_template_compatibility(template_dir, detect_zip_layout(local_zip))
            sha256 = sha256sum(local_zip)

            created_dir = create_version_dir(
                template_dir,
                directory_name,
                module_version,
                asset_url,
                sha256,
            )
            if created_dir is not None:
                created.append(created_dir.relative_to(REPO_ROOT).as_posix())
                existing_names.add(directory_name)
                print(f"Created {created_dir.relative_to(REPO_ROOT)}")

    if not created:
        print("No new microg-unofficial-installer releases found.")
    else:
        print("New directories created:")
        for item in created:
            print(f"- {item}")


if __name__ == "__main__":
    main()
