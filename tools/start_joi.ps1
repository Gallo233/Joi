[CmdletBinding()]
param(
  [int]$Port = 8765,
  [switch]$ReuseCore
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ReleaseShellExe = Join-Path $ProjectRoot "agent_companion\shell\src-tauri\target\release\joi-shell.exe"
$DebugShellExe = Join-Path $ProjectRoot "agent_companion\shell\src-tauri\target\debug\joi-shell.exe"
$ShellDir = Join-Path $ProjectRoot "agent_companion\shell"
$LogDir = Join-Path $ProjectRoot "logs"
$NodeBin = "D:\codex游戏\toolchains\node"
$Npm = Join-Path $NodeBin "npm.cmd"
$LocalCargoBin = Join-Path $ProjectRoot "..\toolchains\rust\cargo\bin"
$UserCargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
$CargoBin = if (Test-Path (Join-Path $LocalCargoBin "cargo.exe")) { $LocalCargoBin } else { $UserCargoBin }

function Test-LocalPort {
  param([int]$PortToCheck)
  $client = $null
  try {
    $client = [System.Net.Sockets.TcpClient]::new()
    $connect = $client.BeginConnect("127.0.0.1", $PortToCheck, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne(200, $false)) {
      return $false
    }
    $client.EndConnect($connect)
    return $true
  } catch {
    return $false
  } finally {
    if ($client) {
      $client.Close()
    }
  }
}

function Stop-CoreOnPort {
  param([int]$PortToStop)
  try {
    $connections = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $PortToStop -State Listen -ErrorAction SilentlyContinue
  } catch {
    $connections = @()
  }
  foreach ($connection in $connections) {
    try {
      $process = Get-Process -Id $connection.OwningProcess -ErrorAction Stop
      if ($process.ProcessName -notmatch '^(python|pythonw)$') {
        continue
      }
      Stop-Process -Id $process.Id -Force -ErrorAction Stop
    } catch {
      continue
    }
  }
  for ($index = 0; $index -lt 25; $index += 1) {
    if (-not (Test-LocalPort -PortToCheck $PortToStop)) {
      return
    }
    Start-Sleep -Milliseconds 200
  }
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Python)) {
  Write-Host "Missing .venv\Scripts\python.exe. Run dependency setup first."
  Read-Host "Press Enter to exit"
  exit 1
}

if (-not $ReuseCore) {
  Stop-CoreOnPort -PortToStop $Port
}

if (-not (Test-LocalPort -PortToCheck $Port)) {
  $stdout = Join-Path $LogDir "joi_core.out.log"
  $stderr = Join-Path $LogDir "joi_core.err.log"
  $coreArgs = @(
    "-m",
    "agent_companion.core.main",
    "--serve",
    "--workspace",
    $ProjectRoot,
    "--port",
    "$Port"
  )

  Start-Process `
    -FilePath $Python `
    -ArgumentList $coreArgs `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

  for ($index = 0; $index -lt 50; $index += 1) {
    if (Test-LocalPort -PortToCheck $Port) {
      break
    }
    Start-Sleep -Milliseconds 200
  }
}

if (Test-Path $ReleaseShellExe) {
  Start-Process -FilePath $ReleaseShellExe -WorkingDirectory (Split-Path -Parent $ReleaseShellExe)
  exit 0
}

if (Test-Path $Npm) {
  $env:Path = "$NodeBin;$CargoBin;$env:Path"
  Start-Process -FilePath $Npm -ArgumentList @("run", "tauri", "dev") -WorkingDirectory $ShellDir
  exit 0
}

if (Test-Path $DebugShellExe) {
  Write-Host "Found a debug shell, but no npm was available to launch its dev server."
  Write-Host "Build the release shell first so the desktop shortcut does not depend on 127.0.0.1:5173."
}

Write-Host "Missing built shell and npm. Build the shell first or install the Windows toolchain."
Read-Host "Press Enter to exit"
exit 1
