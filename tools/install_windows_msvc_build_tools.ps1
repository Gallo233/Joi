param(
  [string]$DownloadDir = "D:\codex游戏\toolchains\downloads",
  [string]$InstallPath = "C:\BuildTools"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
$installer = Join-Path $DownloadDir "vs_BuildTools.exe"
if (-not (Test-Path $installer)) {
  Invoke-WebRequest -UseBasicParsing "https://aka.ms/vs/17/release/vs_BuildTools.exe" -OutFile $installer
}

$args = @(
  "--quiet",
  "--wait",
  "--norestart",
  "--nocache",
  "--installPath", $InstallPath,
  "--add", "Microsoft.VisualStudio.Workload.VCTools",
  "--includeRecommended"
)

$process = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
  throw "Visual Studio Build Tools installer failed with exit code $($process.ExitCode)."
}

Write-Host "Visual Studio Build Tools installation finished with exit code $($process.ExitCode)."
