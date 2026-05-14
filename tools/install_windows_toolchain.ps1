param(
  [string]$InstallRoot = "D:\codex游戏\toolchains",
  [string]$ProjectRoot = "D:\codex游戏\shinsekai_mvp"
)

$ErrorActionPreference = "Stop"

function Add-UserPath([string]$PathToAdd) {
  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  $parts = @()
  if ($current) {
    $parts = $current -split ";" | Where-Object { $_ -ne "" }
  }
  if ($parts -notcontains $PathToAdd) {
    $next = (@($PathToAdd) + $parts) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $next, "User")
  }
  $env:Path = "$PathToAdd;$env:Path"
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$downloadDir = Join-Path $InstallRoot "downloads"
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

Write-Host "[1/4] Installing portable Node.js with npm..."
$nodeIndex = Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/latest-v24.x/"
$nodeZipName = [regex]::Match($nodeIndex.Content, 'node-v[^"]+-win-x64\.zip').Value
if (-not $nodeZipName) {
  throw "Cannot find Node.js win-x64 zip from latest-v24.x."
}
$nodeZip = Join-Path $downloadDir $nodeZipName
$nodeUrl = "https://nodejs.org/dist/latest-v24.x/$nodeZipName"
$nodeExtractRoot = Join-Path $InstallRoot "node"
if (Test-Path (Join-Path $nodeExtractRoot "npm.cmd")) {
  Write-Host "Using existing Node.js at $nodeExtractRoot"
} else {
  if (-not (Test-Path $nodeZip)) {
    Invoke-WebRequest -UseBasicParsing $nodeUrl -OutFile $nodeZip
  }
  Expand-Archive -Force $nodeZip -DestinationPath $InstallRoot
  $nodeDir = Get-ChildItem -Path $InstallRoot -Directory -Filter "node-v*-win-x64" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $nodeDir) {
    throw "Node.js extraction failed."
  }
  Rename-Item -Force $nodeDir.FullName $nodeExtractRoot
}
Add-UserPath $nodeExtractRoot

Write-Host "[2/4] Installing Rust toolchain with rustup..."
$rustup = Join-Path $downloadDir "rustup-init.exe"
if (-not (Test-Path $rustup)) {
  Invoke-WebRequest -UseBasicParsing "https://win.rustup.rs/x86_64" -OutFile $rustup
}
& $rustup -y --default-toolchain stable
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
Add-UserPath $cargoBin

Write-Host "[3/4] Installing Tauri shell npm dependencies..."
$shellDir = Join-Path $ProjectRoot "agent_companion\shell"
Push-Location $shellDir
try {
  & (Join-Path $nodeExtractRoot "npm.cmd") install
} finally {
  Pop-Location
}

Write-Host "[4/4] Versions:"
& (Join-Path $nodeExtractRoot "node.exe") --version
& (Join-Path $nodeExtractRoot "npm.cmd") --version
& (Join-Path $cargoBin "rustc.exe") --version
& (Join-Path $cargoBin "cargo.exe") --version

Write-Host "Done. Open a new PowerShell window to pick up persistent PATH changes."
