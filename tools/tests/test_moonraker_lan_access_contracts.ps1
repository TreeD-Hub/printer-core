param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

function Read-RepoFile {
  param([string]$Path)

  Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot $Path) -Raw
}

$coreConfig = Read-RepoFile "moonraker/base/00-core.conf"
$nginxConfig = Read-RepoFile "loader/steps/mainsail-web.sh"

if ($coreConfig -notmatch '(?m)^\s*192\.168\.0\.0/24\s*$') {
  throw "FAIL: Moonraker must trust the printer local subnet for proxied Mainsail/Fluidd clients"
}

if ($coreConfig -match '(?m)^\s*192\.168\.0\.0/16\s*$') {
  throw "FAIL: Moonraker must not trust the wider 192.168.0.0/16 range"
}

if ($nginxConfig -notmatch 'proxy_set_header X-Real-IP \\$remote_addr;') {
  throw "FAIL: nginx must forward the real client IP to Moonraker"
}

if ($nginxConfig -notmatch '(?s)location \^~ /server/treed/ \{.*?allow 127\.0\.0\.1;.*?deny all;') {
  throw "FAIL: private host endpoints must remain localhost-only"
}

Write-Host "OK: Moonraker LAN access contracts passed"
