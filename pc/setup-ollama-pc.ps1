<#
  AETHER - share this PC's GPU with AETHER over your home network.

  Run on the Windows PC, in PowerShell opened with "Run as administrator":
      powershell -ExecutionPolicy Bypass -File setup-ollama-pc.ps1

  What it does (it asks before each change):
    1. Checks Ollama and your NVIDIA GPU.
    2. Makes Ollama listen on your network (OLLAMA_HOST=0.0.0.0:11434, for your user).
    3. Allows port 11434 through Windows Firewall for PRIVATE networks only.
    4. Restarts Ollama and downloads models that fit a 16 GB GPU (e.g. RTX 5080).
    5. Prints the address to type into AETHER: Settings > Computers on your network.

  Undo:  Remove-NetFirewallRule -DisplayName "Ollama for AETHER (private network)"
         [Environment]::SetEnvironmentVariable("OLLAMA_HOST", $null, "User")   then restart Ollama.

  Ollama has no password. Only use this on a home network you trust, and never forward
  port 11434 on your router (that would expose it to the internet).
#>
$ErrorActionPreference = "Stop"
$RuleName = "Ollama for AETHER (private network)"
$Port = 11434

function Say($t)  { Write-Host "  $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [ok] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Ask($q)  { $a = Read-Host "  $q [Y/n]"; return ($a -eq "" -or $a -match "^[Yy]") }

Write-Host ""
Write-Host "  A E T H E R  - GPU PC setup" -ForegroundColor Cyan
Write-Host ""

# --- 0. administrator (needed for the firewall rule) -------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Warn "Please run this in PowerShell opened with 'Run as administrator' (needed for the firewall)."
    exit 1
}

# --- 1. Ollama and the GPU ---------------------------------------------------
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Warn "Ollama isn't installed."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install it now with winget?")) {
        winget install --id Ollama.Ollama -e --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    }
    if (-not $ollama) { Warn "Install it from https://ollama.com/download, then run this script again."; exit 1 }
}
Ok "Ollama found: $((& ollama --version) -join ' ')"

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) -join "; "
    Ok "GPU: $gpu"
} else {
    Warn "nvidia-smi not found. Install the latest NVIDIA driver so Ollama can use the GPU."
}

# --- 2. listen on the network ------------------------------------------------
$current = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User")
if ($current -eq "0.0.0.0:$Port") {
    Ok "Ollama is already set to listen on your network."
} elseif (Ask "Let Ollama accept connections from your home network (OLLAMA_HOST=0.0.0.0:$Port)?") {
    [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:$Port", "User")
    Ok "Set OLLAMA_HOST for your user."
} else {
    Warn "Skipped. AETHER won't be able to reach this PC until Ollama listens on the network."
}

# --- 3. firewall (private networks only) -------------------------------------
if (Ask "Allow port $Port through Windows Firewall on PRIVATE networks only?") {
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $Port `
        -Action Allow -Profile Private | Out-Null
    Ok "Firewall rule added (private networks only)."
}
$public = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" }
foreach ($p in $public) {
    Warn "Your network '$($p.Name)' is set to Public, so the firewall rule won't apply on it."
    if (Ask "Mark '$($p.Name)' as a Private (home) network? Only do this for your own home network.") {
        Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
        Ok "'$($p.Name)' is now Private."
    }
}

# --- 4. restart Ollama with the new setting ----------------------------------
Say "Restarting Ollama..."
Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
$app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (Test-Path $app) {
    $env:OLLAMA_HOST = "0.0.0.0:$Port"
    Start-Process $app
} else {
    $env:OLLAMA_HOST = "0.0.0.0:$Port"
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
}
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/version" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 1 }
}
if ($up) { Ok "Ollama is running." } else { Warn "Ollama didn't come back. Open the Ollama app from the Start menu." }

# --- 5. models for a 16 GB GPU -----------------------------------------------
$models = @(
    @{ name = "qwen3:14b";   why = "main brain: reliable tool calling (~9 GB)" },
    @{ name = "gpt-oss:20b"; why = "stronger reasoning, tool calling (~14 GB)" },
    @{ name = "qwen3-vl:8b"; why = "sees images: screenshots, timetables (~6 GB)" }
)
foreach ($m in $models) {
    if (Ask "Download $($m.name) - $($m.why)?") {
        & ollama pull $m.name
        if ($LASTEXITCODE -ne 0) { Warn "Couldn't download $($m.name) (the name may have changed). Skipping." }
    }
}

# --- 6. the address to type into AETHER --------------------------------------
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and
    $_.InterfaceAlias -notmatch "vEthernet|Loopback|VirtualBox|VMware|WSL"
} | Select-Object -ExpandProperty IPAddress
Write-Host ""
if ($ips) {
    Write-Host "  Done. In AETHER on your Mac: Settings > Computers on your network" -ForegroundColor Green
    Write-Host "  Name: pc     Address: $($ips | Select-Object -First 1)" -ForegroundColor Green
    if (@($ips).Count -gt 1) { Say "(Other addresses on this PC: $(($ips | Select-Object -Skip 1) -join ', '))" }
} else {
    Warn "Couldn't find this PC's network address. Run 'ipconfig' and use the IPv4 Address."
}
Say "Tip: in Windows power settings, stop the PC sleeping while you use AETHER."
Write-Host ""
