#!/usr/bin/env python3
"""
add_games.py — Append new games from a markdown list into a GAMES JS file,
               then download each new game's files into <games_dir>/<id>/.

Usage:
    python add_games.py <games.js> <games.md> [games_dir]
    python add_games.py <games.js> <games.md> [games_dir] --debug

    games_dir  — root folder where game files are saved (default: ./games)
                 Each game lands in <games_dir>/<id>/

Requires gh_download.py to be in the same directory.

Markdown format (single port):
  - [Title](https://github.com/owner/repo) - port by [name](url)

Markdown format (multiple ports):
  - [Title](url1), [2](url2), [3](url3) - Ports by [porter1](url), [porter2](url)

URL selection priority:
  1. Any URL whose path contains 'genizy' (bread's account)
  2. The last URL listed (highest number)
"""

import re
import sys
import importlib.util
from pathlib import Path


BREAD_GITHUB = "genizy"


# ── Load gh_download ──────────────────────────────────────────────────────────

def load_gh_download():
    here    = Path(__file__).parent
    gh_path = here / "gh_download.py"
    if not gh_path.exists():
        sys.exit(
            f"gh_download.py not found at {gh_path}\n"
            "Place gh_download.py in the same directory as this script."
        )
    spec   = importlib.util.spec_from_file_location("gh_download", gh_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_js_games(js_text: str) -> tuple[list[str], int]:
    titles = re.findall(r'title:\s*"([^"]+)"', js_text)
    ids    = [int(m) for m in re.findall(r'\bid:\s*(\d+)', js_text)]
    return titles, (max(ids) if ids else 0)


def pick_url(urls: list[str]) -> tuple[str, str]:
    """
    Given a list of repo URLs, return (chosen_url, reason).
    Priority: bread's URL (contains BREAD_GITHUB) > last in list.
    """
    for url in urls:
        if BREAD_GITHUB in url:
            return url, f"bread's port ({url})"
    return urls[-1], f"highest-numbered port ({urls[-1]})"


def parse_md_games(md_text: str, debug: bool = False) -> list[dict]:
    """
    Parse each game line, handling both single and multiple port URLs.
    Returns list of {title, repo_url, porter, chosen_reason}.
    """
    games = []

    for line in md_text.splitlines():
        line = line.strip().replace("\u00a0", " ").replace("\u200b", "")
        if not line or not line.startswith("-"):
            continue

        if debug:
            print(f"  [debug] raw : {repr(line)}")

        # Must have "port by" or "ports by"
        porter_match = re.search(r'[Pp]orts?(?:ed)?(?: by the| by) (.+)$', line)
        if not porter_match:
            if debug:
                print("  [debug] match: None (no 'port by' found)")
            continue

        porter_section = porter_match.group(1)
        # Collect porter names — prefer non-github links first, then github
        porter_names = re.findall(r'\[([^\]]+)\]\([^)]+\)', porter_section)

        # Title is always the first [text](url) on the line
        title_match = re.search(r'-\s+\[(?P<title>[^\]]+)\]', line)
        if not title_match:
            if debug:
                print("  [debug] match: None (no title found)")
            continue
        title = title_match.group("title")

        # Collect all github.com URLs from the part before "port by"
        pre_porter = line[:porter_match.start()]
        repo_urls = re.findall(
            r'_*(?P<url>https://github\.com/[^_)\s]+?)_*\)',
            pre_porter
        )

        if not repo_urls:
            if debug:
                print("  [debug] match: None (no github URLs found)")
            continue

        chosen_url, reason = pick_url(repo_urls)
        porter = ", ".join(porter_names) if porter_names else "unknown"

        if debug:
            print(f"  [debug] title: {title}")
            print(f"  [debug] urls : {repo_urls}")
            print(f"  [debug] chose: {chosen_url} — {reason}")
            print(f"  [debug] porter: {porter}")

        games.append({
            "title":          title,
            "repo_url":       chosen_url,
            "porter":         porter,
            "chosen_reason":  reason,
        })

    return games


# ── JS entry builder ──────────────────────────────────────────────────────────

def make_entry(game: dict, game_id: int) -> str:
    return (
        f"  {{\n"
        f'id: {game_id},\n'
        f'title: "{game["title"]}",\n'
        f'description: "By: {game["porter"]}",\n'
        f'thumbnail: "/thumbs/{game_id}.png",\n'
        f'url: "/games/{game_id}/index.html",\n'
        f"  }}"
    )


# ── JS injector ───────────────────────────────────────────────────────────────

def inject_games(js_text: str, new_entries: list[str]) -> str:
    close = js_text.rfind("];")
    if close == -1:
        sys.exit("Could not find closing `];` in the JS file.")
    insert_block = ",\n".join(new_entries) + ",\n"
    return js_text[:close] + insert_block + js_text[close:]


# ── Downloader ────────────────────────────────────────────────────────────────

from pathlib import Path
import urllib.error

def download_game(gh, repo_url: str, game_id: int, games_dir: Path) -> bool:
    dest = games_dir / str(game_id)
    dest.mkdir(parents=True, exist_ok=True)

    try:
        info = gh.parse_github_url(repo_url)

        owner  = info["owner"]
        repo   = info["repo"]
        branch = info["branch"] or gh.default_branch(owner, repo)

        print(f"  Owner: {owner}  Repo: {repo}  Branch: {branch}")

        if info["is_folder"]:
            ok = gh.download_folder(owner, repo, branch, info["folder"], dest)
        else:
            ok = gh.download_repo(owner, repo, branch, dest)

        return ok if ok is not None else True

    # ✅ THIS is the important part
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("  ⚠ Skipped (404 not found)")
        else:
            print(f"  ⚠ Skipped (HTTP {e.code})")

    except Exception as e:
        print(f"  ⚠ Skipped (error: {e})")

    # 🧹 cleanup empty folder
    try:
        if dest.exists() and not any(dest.iterdir()):
            dest.rmdir()
    except Exception:
        pass

    return False

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args  = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug = "--debug" in sys.argv

    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    js_path   = Path(args[0])
    md_path   = Path(args[1])
    games_dir = Path(args[2]) if len(args) >= 3 else Path("games")

    if not js_path.exists():
        sys.exit(f"JS file not found: {js_path}")
    if not md_path.exists():
        sys.exit(f"MD file not found: {md_path}")

    gh = load_gh_download()

    js_text = js_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")

    existing_titles, max_id = parse_js_games(js_text)
    md_games = parse_md_games(md_text, debug=debug)

    if not md_games:
        print("No games found. Re-run with --debug to inspect each line, e.g.:")
        print(f"  python {sys.argv[0]} {args[0]} {args[1]} --debug")
        sys.exit(1)

    new_entries = []
    to_download = []
    next_id = max_id + 1

    for game in md_games:
        if game["title"] in existing_titles:
            print(f"  skip  \"{game['title']}\" (already in JS)")
            continue
        new_entries.append(make_entry(game, next_id))
        to_download.append((game, next_id))
        print(f"  add   \"{game['title']}\" → id {next_id}  [{game['chosen_reason']}]")
        next_id += 1

    if not new_entries:
        print("Nothing to add.")
        return

    updated = inject_games(js_text, new_entries)
    js_path.write_text(updated, encoding="utf-8")
    print(f"\n\u2713 {len(new_entries)} game(s) added to {js_path}\n")

    for game, game_id in to_download:
        print(f"\u2500\u2500 Downloading \"{game['title']}\" (id {game_id}) \u2500\u2500")
        download_game(gh, game["repo_url"], game_id, games_dir)
        print()

    print("\u2713 All done.")


if __name__ == "__main__":
    main()