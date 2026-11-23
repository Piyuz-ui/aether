<#
  AETHER - use this PC's GPU from anywhere (college, cafe, phone hotspot).

  Uses Tailscale: a free, encrypted private network for YOUR devices only. Nothing is opened to
  the internet and your router isn't touched. Only devices signed in to your Tailscale account
  can reach this PC.

  Run in PowerShell opened with "Run as administrator":
      powershell -ExecutionPolicy Bypass -File setup-remote-access.ps1

  What it does (it asks before each change):
    1. Installs Tailscale and signs this PC in (a browser window opens for you to log in).
    2. Lets your Tailscale devices (and only them) reach Ollama (11434) and AETHER's hardware
       monitor (11435).
    3. Keeps the PC awake while it's plugged in, so it's there when you need it.
    4. Prints this PC's Tailscale address to add in AETHER on your Mac.

  Run setup-ollama-pc.ps1 first (it makes Ollama listen on the network).

  Undo:  Remove-NetFirewallRule -DisplayName "AETHER via Tailscale (your devices only)"
         powercfg /change standby-timeout-ac 30
#>
$ErrorActionPreference = "Stop"
$RuleName = "AETHER via Tailscale (your devices only)"

function Say($t)  { Write-Host "  $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [ok] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Ask($q)  { $a = Read-Host "  $q [Y/n]"; return ($a -eq "" -or $a -match "^[Yy]") }

Write-Host ""
Write-Host "  A E T H E R  - use this PC's GPU from anywhere" -ForegroundColor Cyan
Write-Host ""

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Warn "Please run this in PowerShell opened with 'Run as administrator'."; exit 1 }

# --- 0. Ollama must listen on the network --------------------------------------
if ([Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User") -notlike "0.0.0.0*") {
    Warn "Ollama isn't set to listen on the network yet. Run setup-ollama-pc.ps1 first, then this."
    exit 1
}

# --- 1. Tailscale ---------------------------------------------------------------
function Find-Tailscale {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path $p) { return $p }
    return $null
}
$ts = Find-Tailscale
if (-not $ts) {
    Say "Tailscale isn't installed. It's free for personal use: https://tailscale.com"
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Tailscale now with winget?")) {
        winget install --id Tailscale.Tailscale -e --source winget --accept-source-agreements --accept-package-agreements
        $ts = Find-Tailscale
    }
    if (-not $ts) { Warn "Install it from https://tailscale.com/download/windows, then run this script again."; exit 1 }
}
Ok "Tailscale found."

function Get-TsIp { try { return ((& $ts ip -4) | Select-Object -First 1).Trim() } catch { return $null } }
$ip = Get-TsIp
if (-not ($ip -like "100.*")) {
    Say "Signing this PC in to Tailscale. A browser window will open: log in with the SAME account"
    Say "you'll use on your Mac (Google, Microsoft, GitHub or Apple)."
    & $ts up --unattended | Out-Host
    for ($i = 0; $i -lt 120 -and -not ($ip -like "100.*"); $i++) { Start-Sleep -Seconds 2; $ip = Get-TsIp }
}
if (-not ($ip -like "100.*")) { Warn "This PC isn't signed in to Tailscale yet. Open the Tailscale app, log in, then run this again."; exit 1 }
Ok "Signed in. This PC's Tailscale address: $ip"

# --- 2. firewall: only your Tailscale devices ------------------------------------
# 100.64.0.0/10 is Tailscale's private address range: nobody outside your account has these.
if (Ask "Let your Tailscale devices (only them) reach Ollama and the hardware monitor?") {
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort 11434, 11435 `
        -RemoteAddress "100.64.0.0/10" -Action Allow -Profile Any | Out-Null
    Ok "Firewall: ports 11434 and 11435 open to your Tailscale devices only."
}

# --- 3. stay awake while plugged in -------------------------------------------------
if (Ask "Keep this PC awake while it's plugged in (the screen can still turn off)?") {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Ok "The PC won't sleep while plugged in."
}

# --- 4. check and print the address ----------------------------------------------------
$name = $null
try { $name = ((& $ts status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".") } catch { }
$okOllama = $false
try { Invoke-RestMethod "http://${ip}:11434/api/version" -TimeoutSec 5 | Out-Null; $okOllama = $true } catch { }
Write-Host ""
if ($okOllama) { Ok "Ollama answers on the Tailscale address." }
else { Warn "Ollama didn't answer on $ip. Make sure the Ollama app is running." }
Write-Host ""
Write-Host "  Done. On your Mac:" -ForegroundColor Green
Write-Host "   1. Install Tailscale (tailscale.com/download) and log in with the same account." -ForegroundColor Green
Write-Host "   2. AETHER > Settings > Computers on your network > pc > Remote address:" -ForegroundColor Green
Write-Host "        $ip" -ForegroundColor Green
if ($name) { Write-Host "      (or its name: $name)" -ForegroundColor Green }
Write-Host ""
Say "Before you leave: plug the PC in, keep it logged in (Ollama starts when you log in), and"
Say "check Windows Update won't restart it (Settings > Windows Update > Advanced options > Active hours)."
Write-Host ""
