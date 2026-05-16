param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

function Read-RepoFile {
  param(
    [string]$Path
  )

  $fullPath = Join-Path $RepoRoot $Path
  if (-not (Test-Path -LiteralPath $fullPath)) {
    throw "FAIL: missing file ($Path)"
  }

  return Get-Content -Encoding UTF8 -LiteralPath $fullPath -Raw
}

function Assert-Contains {
  param(
    [string]$Path,
    [string]$Pattern,
    [string]$Message
  )

  $content = Read-RepoFile -Path $Path
  if ($content -notmatch $Pattern) {
    throw "FAIL: $Message ($Path)"
  }
}

Assert-Contains ".github/workflows/release-treed-v2-main.yml" 'branches:\s*\[\s*treed-v2_main\s*\]' "release workflow targets only treed-v2_main"
Assert-Contains ".github/workflows/release-treed-v2-main.yml" "major'.*minor'.*patch'" "release workflow validates required release labels"
Assert-Contains ".github/workflows/release-treed-v2-main.yml" 'extractReleaseDescription' "release workflow extracts release description from PR body"
Assert-Contains ".github/workflows/release-treed-v2-main.yml" 'releases/generate-notes' "release workflow requests GitHub-generated release notes for the explicit tag range"
Assert-Contains ".github/workflows/release-treed-v2-main.yml" 'idempotent' "release workflow documents idempotent rerun handling"
Assert-Contains ".github/workflows/release-treed-v2-main.yml" 'direct push' "release workflow fails explicitly on direct push without PR context"

Assert-Contains ".github/workflows/sync-release-labels.yml" 'workflow_dispatch' "label sync workflow supports manual run"
Assert-Contains ".github/workflows/sync-release-labels.yml" '\.github/labels\.json' "label sync workflow watches labels config"
Assert-Contains ".github/workflows/sync-release-labels.yml" 'issues:\s*write' "label sync workflow can manage repository labels"

Assert-Contains ".github/labels.json" '"name"\s*:\s*"major"' "labels config defines major label"
Assert-Contains ".github/labels.json" '"name"\s*:\s*"minor"' "labels config defines minor label"
Assert-Contains ".github/labels.json" '"name"\s*:\s*"patch"' "labels config defines patch label"

Assert-Contains ".github/pull_request_template.md" 'major' "PR template documents major label"
Assert-Contains ".github/pull_request_template.md" 'minor' "PR template documents minor label"
Assert-Contains ".github/pull_request_template.md" 'patch' "PR template documents patch label"
Assert-Contains ".github/pull_request_template.md" 'GitHub Release' "PR template documents the release description block purpose"

Assert-Contains ".github/workflows/README.md" 'release-treed-v2-main\.yml' "workflow README documents release workflow"
Assert-Contains ".github/workflows/README.md" 'sync-release-labels\.yml' "workflow README documents label sync workflow"
Assert-Contains "docs/release-treed-v2-main.md" 'treed-v2_main' "release doc is branch-specific"
Assert-Contains "docs/release-treed-v2-main.md" 'vX\.Y\.Z' "release doc describes semver tag format"
Assert-Contains "docs/release-treed-v2-main.md" 'direct push' "release doc explains direct push behavior"

Write-Output "PASS: release automation contracts"
