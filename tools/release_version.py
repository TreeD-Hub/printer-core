"""Версия Core по заголовку слитого PR; required для автоматического выпуска.

GitHub API вызывается через gh. Draft закрепляет номер до сборки;
публикация и проверка assets остаются в release.yml.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


# Блок 1: Короткие названия PR и числовая версия без привязки к CI run.
PREFIXES = {"фикс": 2, "патч": 1, "релиз": 0}
VERSION_RE = re.compile(r"v?((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))")


def title_bump(title):
    match = re.match(r"^\s*(фикс|патч|релиз)(?=\s|:|$)", title, re.IGNORECASE)
    if match is None:
        raise ValueError("Начни название PR с фикс, патч или релиз; регистр и двоеточие не важны.")
    return PREFIXES[match.group(1).lower()]


def version_parts(value):
    match = VERSION_RE.fullmatch(value)
    return tuple(map(int, match.group(1).split("."))) if match else None


def select_version(bump, sha, releases, tags, head_tags, fallback):
    stable = [r for r in releases if not r.get("prerelease")
              and r["tag_name"].startswith("v") and version_parts(r["tag_name"]) is not None]
    same = [r for r in stable if r.get("target_commitish") == sha or r["tag_name"] in head_tags]
    published = [r for r in stable if not r.get("draft")]
    latest = max(published, key=lambda r: version_parts(r["tag_name"]), default=None)
    if same:
        chosen = max(same, key=lambda r: version_parts(r["tag_name"]))
        if chosen.get("draft") and latest and version_parts(chosen["tag_name"]) < version_parts(latest["tag_name"]):
            raise ValueError("Уже опубликован более новый выпуск; старый draft публиковать нельзя.")
        return chosen["tag_name"][1:], False, latest
    versions = [version_parts(tag) for tag in tags if tag.startswith("v") and version_parts(tag) is not None]
    versions.extend(version_parts(r["tag_name"]) for r in stable)
    base = max(versions, default=version_parts(fallback))
    if base is None:
        raise ValueError("VERSION должен быть X.Y.Z.")
    parts = list(base)
    parts[bump] += 1
    parts[bump + 1:] = [0] * (2 - bump)
    return ".".join(map(str, parts)), True, latest


# Блок 2: Только доверенный checkout и PR, который действительно слит в treed-v2.
def git(*args):
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True, encoding="utf-8").strip()


def github_pages(endpoint):
    pages = json.loads(gh("api", "--paginate", "--slurp", endpoint))
    return [row for page in pages for row in page]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-title", action="store_true")
    args = parser.parse_args(argv)
    if args.check_title:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        title_bump(event["pull_request"]["title"])
        print("Название PR принято.")
        return
    if os.environ["GITHUB_REF"] != "refs/heads/treed-v2":
        raise ValueError("Автоматический выпуск запускается только из treed-v2.")
    sha = os.environ["GITHUB_SHA"]
    if git("rev-parse", "HEAD") != sha:
        raise ValueError("Checkout не совпадает с commit выпуска.")
    repo = os.environ["GITHUB_REPOSITORY"]
    prs = github_pages(f"repos/{repo}/commits/{sha}/pulls?per_page=100")
    merged = [pr for pr in prs if pr.get("merged_at") and pr["base"]["ref"] == "treed-v2"
              and pr.get("merge_commit_sha") == sha]
    if len(merged) != 1:
        raise ValueError("Для commit нужен один слитый PR в treed-v2; прямой push не выпускается.")
    bump = title_bump(merged[0]["title"])
    releases = github_pages(f"repos/{repo}/releases?per_page=100")
    version, reserve, latest = select_version(
        bump, sha, releases, git("tag", "--list").splitlines(),
        git("tag", "--points-at", "HEAD").splitlines(), Path("VERSION").read_text().strip())
    tag = f"v{version}"
    already_published = any(r["tag_name"] == tag and not r.get("draft") for r in releases)
    if latest and not already_published:
        # Старый rerun не должен выпустить более старый код под новым номером.
        status = subprocess.run(["git", "merge-base", "--is-ancestor", latest["tag_name"], "HEAD"], check=False)
        if status.returncode:
            raise ValueError("Checkout не содержит последний опубликованный выпуск.")

    # Блок 3: Закрепляем номер до проверок; повтор использует тот же draft.
    if reserve:
        gh("release", "create", tag, "--repo", repo, "--target", sha, "--draft",
           "--title", f"printer-core {version}", "--notes", f"Commit: {sha}")
    Path("VERSION").write_text(version + "\n", encoding="utf-8")
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as handle:
        handle.write(f"MAIN_SHELL_VERSION={version}\nEXPECTED_TAG={tag}\n")
    print(f"PR #{merged[0]['number']}: {tag}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
