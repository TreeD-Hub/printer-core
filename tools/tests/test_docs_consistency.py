from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> None:
    loader = (ROOT / "loader/loader.sh").read_text(encoding="utf-8")
    match = re.search(r"^STEPS=\(\n(.*?)^\)", loader, re.MULTILINE | re.DOTALL)
    if match is None:
        fail("loader/loader.sh does not contain the expected STEPS array")
    steps_block = match.group(1)
    runtime_steps = re.findall(r'^\s*"([^"]+)"', steps_block, re.MULTILINE)

    ownership = (ROOT / "docs/config-ownership.md").read_text(encoding="utf-8")
    documented_block = ownership.split("Порядок шагов:", 1)[1].split("\n## ", 1)[0]
    documented_steps = re.findall(r'^\d+\. `([^`]+)`$', documented_block, re.MULTILINE)
    if runtime_steps != documented_steps:
        fail(
            "docs/config-ownership.md step order differs from loader/loader.sh:\n"
            f"runtime:    {runtime_steps}\n"
            f"documented: {documented_steps}"
        )

    markdown_files = [
        ROOT / "README.md",
        *sorted((ROOT / "docs").glob("*.md")),
        ROOT / "klipper/profiles/treed_v2_corexy_v1/README.md",
        ROOT / "klipper/profiles/treed_v2_corexy_v1/macros-usage.md",
    ]
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for markdown_file in markdown_files:
        content = markdown_file.read_text(encoding="utf-8")
        for target in link_pattern.findall(content):
            target = target.strip().split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (markdown_file.parent / target).resolve().is_file():
                fail(f"broken Markdown link in {markdown_file.relative_to(ROOT)}: {target}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required_legacy_note = "legacy compatibility asset names"
    if required_legacy_note not in readme:
        fail("README.md must identify retained release asset names as legacy compatibility asset names")
    for asset in ("treed-mainshellos-source.zip", "treed-mainshellos-release.json"):
        if asset not in readme:
            fail(f"README.md is missing the compatibility asset name: {asset}")

    print(f"OK: {len(runtime_steps)} loader steps, Markdown links, and legacy asset note")


if __name__ == "__main__":
    main()
