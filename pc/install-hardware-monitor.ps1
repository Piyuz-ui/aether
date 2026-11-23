<#
  AETHER - hardware monitor for a GPU PC.

  Lets AETHER (Settings > Hardware) show this PC's GPU, VRAM, GPU load, temperature, RAM and CPU,
  and how it's connected (cable or Wi-Fi, link speed, Wi-Fi signal).
  It's a small read-only PowerShell web server on port 11435 that answers GET /stats with numbers
  from nvidia-smi and Windows. It starts hidden when you log in.

  Run in PowerShell opened with "Run as administrator":
      powershell -ExecutionPolicy Bypass -File install-hardware-monitor.ps1

  Uninstall:
      Unregister-ScheduledTask -TaskName "AETHER hardware monitor" -Confirm:$false
      Remove-NetFirewallRule -DisplayName "AETHER hardware monitor (private network)"
      netsh http delete urlacl url=http://+:11435/
#>
$ErrorActionPreference = "Stop"
$Port = 11435
$TaskName = "AETHER hardware monitor"
$RuleName = "AETHER hardware monitor (private network)"
$Dir = Join-Path $env:LOCALAPPDATA "AETHER"
$Monitor = Join-Path $Dir "aether-stats.ps1"
$User = "$env:USERDOMAIN\$env:USERNAME"

function Ok($t)   { Write-Host "  [ok] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  A E T H E R  - hardware monitor" -ForegroundColor Cyan
Write-Host ""

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Warn "Please run this in PowerShell opened with 'Run as administrator'."; exit 1 }
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Warn "nvidia-smi wasn't found, so GPU numbers will be missing. Install the latest NVIDIA driver."
}

# --- 1. the monitor itself ---------------------------------------------------
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$code = @'
# AETHER hardware monitor: answers GET /stats with this PC's GPU, VRAM, RAM and CPU numbers.
# Installed by install-hardware-monitor.ps1. Read-only; serves nothing else.
$ErrorActionPreference = "Continue"
$Port = 11435
$Inv = [Globalization.CultureInfo]::InvariantCulture
function Num($s) {
    $d = 0.0
    if ([double]::TryParse(("$s").Trim(), [Globalization.NumberStyles]::Float, $Inv, [ref]$d)) { return $d }
    return $null
}
$CpuName = ((Get-CimInstance Win32_Processor | Select-Object -First 1).Name).Trim()
$Smi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source

function Get-Stats {
    $os = Get-CimInstance Win32_OperatingSystem
    $total = [double]$os.TotalVisibleMemorySize * 1024
    $free = [double]$os.FreePhysicalMemory * 1024
    $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    $gpus = @()
    if ($Smi) {
        $lines = & $Smi --query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,driver_version --format=csv,noheader,nounits
        foreach ($line in @($lines)) {
            $f = "$line" -split ",\s*"
            if ($f.Count -lt 7) { continue }
            $vt = Num $f[1]; $vu = Num $f[2]
            $gpus += [ordered]@{
                name          = $f[0].Trim()
                vram_total_gb = if ($vt -ne $null) { [math]::Round($vt / 1024, 1) } else { $null }
                vram_used_gb  = if ($vu -ne $null) { [math]::Round($vu / 1024, 1) } else { $null }
                util_pct      = Num $f[3]
                temp_c        = Num $f[4]
                power_w       = Num $f[5]
                driver        = $f[6].Trim()
            }
        }
    }
    # How this PC is connected: cable or Wi-Fi, link speed, and Wi-Fi signal (no network name).
    $network = $null
    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop | Sort-Object RouteMetric | Select-Object -First 1
        $ad = Get-NetAdapter -InterfaceIndex $route.ifIndex -ErrorAction Stop
        $isWifi = ("$($ad.PhysicalMediaType)" -match "802\.11") -or ("$($ad.InterfaceDescription)" -match "Wi-?Fi|Wireless|WLAN")
        $network = [ordered]@{
            type            = if ($isWifi) { "Wi-Fi" } else { "Ethernet" }
            link_mbps       = [math]::Round([double]$ad.ReceiveLinkSpeed / 1000000)
            adapter         = "$($ad.InterfaceDescription)"
            wifi_signal_pct = $null
        }
        if ($isWifi) {
            $sig = (netsh wlan show interfaces) | Select-String "^\s*Signal\s*:\s*(\d+)\s*%"
            if ($sig) { $network.wifi_signal_pct = [int]$sig.Matches[0].Groups[1].Value }
        }
    } catch { }

    return [ordered]@{
        hostname     = $env:COMPUTERNAME
        cpu          = $CpuName
        cpu_pct      = $cpu
        ram_total_gb = [math]::Round($total / 1GB, 1)
        ram_used_gb  = [math]::Round(($total - $free) / 1GB, 1)
        gpus         = @($gpus)
        network      = $network
    }
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:$Port/")
$listener.Start()
while ($listener.IsListening) {
    try {
        $ctx = $listener.GetContext()
        $res = $ctx.Response
        if ($ctx.Request.HttpMethod -eq "GET" -and $ctx.Request.Url.AbsolutePath -eq "/stats") {
            $body = Get-Stats | ConvertTo-Json -Depth 5 -Compress
            $res.StatusCode = 200
        } else {
            $body = '{"error":"not found"}'
            $res.StatusCode = 404
        }
        $res.ContentType = "application/json"
        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
        $res.Close()
    } catch {
        Start-Sleep -Milliseconds 200
    }
}
'@
Set-Content -Path $Monitor -Value $code -Encoding ASCII
Ok "Monitor written to $Monitor"

# --- 2. let it listen on port 11435 (your user, no admin needed at runtime) ---
& netsh http delete urlacl url="http://+:$Port/" 2>&1 | Out-Null
& netsh http add urlacl url="http://+:$Port/" user="$User" | Out-Null
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $Port `
    -Action Allow -Profile Private | Out-Null
Ok "Port $Port allowed on private networks only."

# --- 3. start it at login, hidden --------------------------------------------
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -like "*aether-stats.ps1*" } |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Monitor`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Shows this PC's GPU, VRAM and RAM in AETHER." | Out-Null
Start-ScheduledTask -TaskName $TaskName
Ok "Starts automatically when you log in (task: $TaskName)."

# --- 4. check it ---------------------------------------------------------------
$stats = $null
for ($i = 0; $i -lt 15; $i++) {
    try { $stats = Invoke-RestMethod "http://127.0.0.1:$Port/stats" -TimeoutSec 3; break } catch { Start-Sleep -Seconds 1 }
}
Write-Host ""
if ($stats) {
    foreach ($g in @($stats.gpus)) { Ok ("GPU: {0} - {1} of {2} GB VRAM in use" -f $g.name, $g.vram_used_gb, $g.vram_total_gb) }
    Ok ("RAM: {0} of {1} GB in use - CPU: {2}" -f $stats.ram_used_gb, $stats.ram_total_gb, $stats.cpu)
    if ($stats.network) {
        $n = $stats.network
        $sig = if ($n.wifi_signal_pct -ne $null) { ", signal $($n.wifi_signal_pct)%" } else { "" }
        Ok ("Network: {0} at {1} Mbps{2}" -f $n.type, $n.link_mbps, $sig)
    }
    Write-Host "  Done. Open AETHER: Settings > Hardware." -ForegroundColor Green
} else {
    Warn "The monitor didn't answer yet. Log out and back in, or run: Start-ScheduledTask -TaskName '$TaskName'"
}
Write-Host ""
