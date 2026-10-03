# Project Smart Layer by Azam Puri - Windows installer.
# Run as Administrator (or double-click "Install Smart Layer.bat"). Safe to run again.
#
#  1. finds Python 3.11+ (offers to install it with winget if missing)
#  2. installs the Python packages
#  3. runs the setup wizard (mill name, ERP connection, dashboard password, phone alerts)
#  4. first sync from the ERP
#  5. schedules the daily run and starts the dashboard with Windows
#  6. opens the dashboard port in the Windows firewall for phones on the mill network

param([switch]$SkipWizard, [switch]$SkipTasks, [string]$DailyAt = "07:30")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($text, $color = "White") { Write-Host $text -ForegroundColor $color }
function Fail($text) { Say "`n$text" Red; Read-Host "Press Enter to close"; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not $SkipTasks) { Fail "Please run this as Administrator (right-click > Run as administrator)." }

Say "Project Smart Layer by Azam Puri - installer" Cyan
Say "============================================" Cyan

# ---- 1. Python ------------------------------------------------------------------------------
function Test-Python($exe) {
    try { $ok = & $exe -c "import sys; print(sys.version_info >= (3, 11))" 2>$null; return ($ok -eq "True") } catch { return $false }
}
function Find-Python {
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { try { $candidates += (& py -3 -c "import sys; print(sys.executable)" 2>$null) } catch {} }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $candidates += $cmd.Source }
    $candidates += Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                                       "C:\Program Files\Python3*\python.exe",
                                       "C:\Users\*\AppData\Local\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($c in $candidates) { if ($c -and (Test-Python $c)) { return $c } }
    return $null
}

Say "`n[1/6] Looking for Python 3.11 or newer ..."
$python = Find-Python
if (-not $python) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Python not found - installing Python 3.12 with winget ..." Yellow
        winget install --id Python.Python.3.12 -e --scope machine --accept-package-agreements --accept-source-agreements
        $python = Find-Python
    }
    if (-not $python) { Fail "Python 3.11+ is needed. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this again." }
}
Set-Content -Path "python_path.txt" -Value $python -Encoding ascii
Say "  Using $python" Green

# ---- 2. packages ----------------------------------------------------------------------------
Say "`n[2/6] Installing Python packages ..."
& $python -m pip install --quiet --upgrade pip 2>$null | Out-Null
& $python -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Package installation failed - check the internet connection and run again." }
Say "  Done" Green

# ---- 3. setup wizard ------------------------------------------------------------------------
if (-not $SkipWizard -or -not (Test-Path "config.toml")) {
    Say "`n[3/6] Setup questions (Enter keeps the value in [brackets])" Cyan
    & $python -m smart_layer setup
    if ($LASTEXITCODE -ne 0) {
        Say "`nThe ERP connection test reported a problem (see above). Fix the mapping or answers and run this again." Yellow
        Read-Host "Press Enter to continue anyway, or close this window to stop"
    }
} else { Say "`n[3/6] Keeping the existing config.toml (run without -SkipWizard to change it)" }

# ---- licence ---------------------------------------------------------------------------------
& $python -m smart_layer licence show *> $null
$licensed = ($LASTEXITCODE -eq 0)
if (-not $licensed) {
    $mid = (& $python -m smart_layer machine-id).Trim()
    Say "`nLicence: this server's ID is  $mid" Cyan
    Say "  Send this ID to Azam Puri to receive the licence (a code or a license.key file)."
    $code = Read-Host "  Paste the licence code or the path of the .key file now (Enter = add it later from the dashboard)"
    if ($code) {
        & $python -m smart_layer licence install "$code"
        $licensed = ($LASTEXITCODE -eq 0)
    }
}

# ---- 4. first sync ---------------------------------------------------------------------------
if ($licensed) {
    Say "`n[4/6] First sync from the ERP (can take a few minutes) ..."
    & $python -m smart_layer sync
    if ($LASTEXITCODE -ne 0) { Fail "Sync failed - see the message above." }
} else {
    Say "`n[4/6] No licence yet - the first sync runs once the licence is added (dashboard > Licence)." Yellow
}

if ($SkipTasks) { Say "`nSkipping scheduled tasks (-SkipTasks). Done." Green; exit 0 }

# ---- 5. scheduled tasks ----------------------------------------------------------------------
Say "`n[5/6] Scheduling the daily run ($DailyAt) and the dashboard (starts with Windows) ..."
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$daily = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run_daily.ps1`""
Register-ScheduledTask -TaskName "Smart Layer Daily" -Action $daily -Principal $principal -Force `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $DailyAt) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)) | Out-Null

$dash = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run_dashboard.ps1`""
Register-ScheduledTask -TaskName "Smart Layer Dashboard" -Action $dash -Principal $principal -Force `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)) | Out-Null
Stop-ScheduledTask -TaskName "Smart Layer Dashboard" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "Smart Layer Dashboard"
Say "  Tasks 'Smart Layer Daily' and 'Smart Layer Dashboard' installed" Green

# ---- 6. firewall -----------------------------------------------------------------------------
$port = (& $python -c "from smart_layer import config; print(config.load('config.toml')['serve'].get('port', 8080))").Trim()
$hostSetting = (& $python -c "from smart_layer import config; print(config.load('config.toml')['serve'].get('host', '127.0.0.1'))").Trim()
if ($hostSetting -eq "0.0.0.0") {
    Say "`n[6/6] Opening port $port in the Windows firewall (mill network only) ..."
    Get-NetFirewallRule -DisplayName "Smart Layer Dashboard" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "Smart Layer Dashboard" -Direction Inbound -Protocol TCP -LocalPort $port `
        -Action Allow -Profile Domain, Private | Out-Null
} else { Say "`n[6/6] Dashboard is for this computer only - no firewall change" }

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1).IPAddress
Start-Sleep -Seconds 4
Say "`n============================================" Cyan
Say "Installed." Green
Say "  Dashboard on this server : http://localhost:$port"
if ($hostSetting -eq "0.0.0.0") { Say "  From phones on the mill Wi-Fi : http://${ip}:$port   (any user name + the dashboard password)" }
Say "  Daily run                : every day at $DailyAt (sync, audit, alerts, reports in the 'output' folder)"
Say "  Health check             : `"$python`" -m smart_layer status"
if (-not $licensed) { Say "  Licence                  : not added yet - server ID $mid (dashboard > Licence)" Yellow }
Start-Process "http://localhost:$port"
Read-Host "`nPress Enter to close"
