# run_erp_audit.ps1 - Task Scheduler wrapper for 70_erp_audit.py.
#   daily  (07:45): morning summary + yesterday's ERP audit        -> run_erp_audit.ps1
#   monthly (10th, 09:00): full audit of the previous month         -> run_erp_audit.ps1 -Monthly
#   hourly 10:00-21:00: yesterday's ERP production once keyed       -> run_erp_audit.ps1 -Followup
param([switch]$Monthly, [switch]$Followup)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = 'C:\Users\DBSERVER\AppData\Local\Programs\Python\Python312\python.exe'
$Log       = Join-Path $ScriptDir 'erp_audit_status_log.txt'
$mode = if ($Followup) { 'erp-followup' } elseif ($Monthly) { 'monthly' } else { 'daily' }
Add-Content -Path $Log -Value ("STARTED $mode " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
try {
    if ($Followup) { & $PythonExe (Join-Path $ScriptDir '70_erp_audit.py') --erp-followup *>> $Log }
    elseif ($Monthly) { & $PythonExe (Join-Path $ScriptDir '70_erp_audit.py') --monthly *>> $Log }
    else          { & $PythonExe (Join-Path $ScriptDir '70_erp_audit.py') *>> $Log }
    $code = $LASTEXITCODE
} catch { $code = 1; Add-Content -Path $Log -Value ("LAUNCH FAILURE " + $_.Exception.Message) }
Add-Content -Path $Log -Value ("COMPLETED $mode " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " exit=$code")
exit $code
