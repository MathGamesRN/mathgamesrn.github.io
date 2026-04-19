#!/usr/bin/env python3
"""
gh_download.py — Download a GitHub repo or a specific subfolder.

Usage:
    python gh_download.py <github_url> [output_dir]

Examples:
    python gh_download.py https://github.com/owner/repo
    python gh_download.py https://github.com/owner/repo/tree/main/src/utils
    python gh_download.py https://github.com/owner/repo/tree/main/src/utils ./my_output

Authentication (avoids API rate limits):
    Set the GITHUB_TOKEN environment variable to a personal access token:
        export GITHUB_TOKEN=ghp_yourtoken   # Linux/macOS
        set GITHUB_TOKEN=ghp_yourtoken      # Windows CMD
    Tokens can be created at: https://github.com/settings/tokens
    No special scopes are needed for public repos.
"""

import os
import re
import sys
import json
import zipfile
import shutil
import tempfile
import urllib.request
import urllib.error
from pathlib import Path


# ── URL parsing ──────────────────────────────────────────────────────────────

def parse_github_url(url: str) -> dict:
    """
    Parse a GitHub URL and return its components.

    Supported forms:
      https://github.com/owner/repo
      https://github.com/owner/repo/tree/<branch>
      https://github.com/owner/repo/tree/<branch>/path/to/folder
    """
    url = url.rstrip("/")

    # Full-repo pattern (no /tree/ segment)
    repo_only = re.fullmatch(
        r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)",
        url,
    )
    if repo_only:
        return {
            "owner":     repo_only.group("owner"),
            "repo":      repo_only.group("repo"),
            "branch":    None,
            "folder":    None,
            "is_folder": False,
        }

    # Repo + branch (+ optional subfolder)
    tree_pattern = re.fullmatch(
        r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
        r"/tree/(?P<branch>[^/]+)(?:/(?P<folder>.+))?",
        url,
    )
    if tree_pattern:
        folder = tree_pattern.group("folder")
        return {
            "owner":     tree_pattern.group("owner"),
            "repo":      tree_pattern.group("repo"),
            "branch":    tree_pattern.group("branch"),
            "folder":    folder,
            "is_folder": folder is not None,
        }

    raise ValueError(
        f"Unrecognised GitHub URL format: {url}\n"
        "Expected: https://github.com/owner/repo[/tree/branch[/path]]"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_headers() -> dict:
    """Return request headers, including a Bearer token if GITHUB_TOKEN is set."""
    headers = {"User-Agent": "gh-download/1.0"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def http_get(url: str, *, as_bytes: bool = False):
    # Percent-encode any characters in the path that are invalid in URLs
    # (e.g. spaces in filenames like "Clip_Secret Tape 1.webm").
    # Split off the scheme+host so we don't encode the slashes between segments.
    from urllib.parse import urlsplit, urlunsplit, quote
    parts = urlsplit(url)
    safe_path = quote(parts.path, safe="/:@!$&'()*+,;=")
    url = urlunsplit(parts._replace(path=safe_path))

    req = urllib.request.Request(url, headers=_build_headers())
    with urllib.request.urlopen(req) as resp:
        return resp.read() if as_bytes else resp.read().decode()


def default_branch(owner: str, repo: str) -> str:
    """Ask the GitHub API for the repo's default branch."""
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        data = json.loads(http_get(api))
        return data["default_branch"]
    except Exception:
        return "main"   # sensible fallback


# ── Download strategies ───────────────────────────────────────────────────────

def _download_zip(owner: str, repo: str, branch: str) -> bytes:
    """Download the full repo zip and return the raw bytes."""
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    print(f"  Fetching zip from {zip_url} …")
    return http_get(zip_url, as_bytes=True)


def download_repo(owner: str, repo: str, branch: str, dest: Path) -> None:
    """Download the entire repo as a zip archive and extract it."""
    print(f"Downloading full repo '{owner}/{repo}' (branch: {branch}) …")
    zip_bytes = _download_zip(owner, repo, branch)

    zip_path = dest / f"{repo}-{branch}.zip"
    zip_path.write_bytes(zip_bytes)

    print(f"Extracting to '{dest}' …")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        zip_path.unlink()

        extracted = Path(tmp) / f"{repo}-{branch}"
        for item in extracted.iterdir():
            target = dest / item.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.move(str(item), dest)

    print(f"✓ Repo contents saved to: {dest}")


def _folder_via_api(
    owner: str, repo: str, branch: str, folder: str, dest: Path
) -> None:
    """
    Download a subfolder using the Git Trees API (requires API access).
    Raises urllib.error.HTTPError if rate-limited or forbidden.
    """
    tree_url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/git/trees/{branch}?recursive=1"
    )
    print("  Trying GitHub Trees API …")
    tree_data = json.loads(http_get(tree_url))

    if tree_data.get("truncated"):
        print("  Warning: tree was truncated by GitHub (very large repo). "
              "Some files may be missing.")

    prefix = folder.rstrip("/") + "/"
    blobs  = [
        item for item in tree_data.get("tree", [])
        if item["type"] == "blob" and item["path"].startswith(prefix)
    ]

    if not blobs:
        raise FileNotFoundError(
            f"No files found under '{folder}' on branch '{branch}'."
        )

    dest.mkdir(parents=True, exist_ok=True)

    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
    print(f"  Downloading {len(blobs)} file(s) …")

    for item in blobs:
        rel_path  = item["path"][len(prefix):]
        file_dest = dest / rel_path
        file_dest.parent.mkdir(parents=True, exist_ok=True)
        raw_url = raw_base + item["path"]
        try:
            file_dest.write_bytes(http_get(raw_url, as_bytes=True))
            print(f"    ✓ {rel_path}")
        except urllib.error.HTTPError as exc:
            print(f"    ✗ {rel_path} — {exc}")

    print(f"\n✓ Folder saved to: {dest}")


def _folder_via_zip(
    owner: str, repo: str, branch: str, folder: str, dest: Path
) -> None:
    """
    Fallback: download the full repo zip, extract only the requested subfolder.
    No API calls — works even when rate-limited.
    """
    print("  Falling back to full-repo zip extraction …")
    zip_bytes = _download_zip(owner, repo, branch)

    # The zip root is  <repo>-<branch>/
    zip_prefix = f"{repo}-{branch}/{folder.rstrip('/')}/"

    dest.mkdir(parents=True, exist_ok=True)

    extracted = 0
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "repo.zip"
        zip_path.write_bytes(zip_bytes)

        with zipfile.ZipFile(zip_path) as zf:
            members = [m for m in zf.namelist() if m.startswith(zip_prefix)
                       and not m.endswith("/")]
            if not members:
                raise FileNotFoundError(
                    f"Folder '{folder}' not found in the zip archive."
                )
            for member in members:
                rel_path  = member[len(zip_prefix):]
                file_dest = dest / rel_path
                file_dest.parent.mkdir(parents=True, exist_ok=True)
                file_dest.write_bytes(zf.read(member))
                print(f"    ✓ {rel_path}")
                extracted += 1

    print(f"\n✓ {extracted} file(s) saved to: {dest}")


def download_folder(
    owner: str, repo: str, branch: str, folder: str, dest: Path
) -> None:
    """
    Download only a subfolder. Tries the GitHub API first; if rate-limited
    or unauthenticated, falls back to downloading the full zip and extracting
    just the requested folder — no API quota consumed.
    """
    print(f"Target folder : {folder}\n")
    try:
        _folder_via_api(owner, repo, branch, folder, dest)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            print(
                f"  API rate limit hit (HTTP {exc.code}).\n"
                "  Tip: set GITHUB_TOKEN env var to increase your quota.\n"
                "  See script docstring for details.\n"
            )
            _folder_via_zip(owner, repo, branch, folder, dest)
        else:
            raise exc
    except FileNotFoundError as exc:
        sys.exit(str(exc))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    github_url = sys.argv[1]
    dest       = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(".")
    dest.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        print("ℹ Using GITHUB_TOKEN for authentication.")

    try:
        info = parse_github_url(github_url)
    except ValueError as exc:
        raise exc

    owner  = info["owner"]
    repo   = info["repo"]
    branch = info["branch"] or default_branch(owner, repo)

    print(f"Owner  : {owner}")
    print(f"Repo   : {repo}")
    print(f"Branch : {branch}")

    if info["is_folder"]:
        print(f"Folder : {info['folder']}")
        print()
        download_folder(owner, repo, branch, info["folder"], dest)
    else:
        print()
        download_repo(owner, repo, branch, dest)


if __name__ == "__main__":
    main()