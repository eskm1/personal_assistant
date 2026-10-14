# Registers the studio worker as a Windows scheduled task that starts when you
# sign in and restarts itself if it crashes. Run from the studio folder:
#   powershell -ExecutionPolicy Bypass -File .\install_task.ps1
# Remove it again with:
#   Unregister-ScheduledTask -TaskName "Studio Worker" -Confirm:$false
#
# A logon task (not a Windows service) on purpose: Google Drive for desktop only
# mounts the inbox drive inside your signed-in session.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $here "venv\Scripts\pythonw.exe"
$worker = Join-Path $here "worker.py"

if (-not (Test-Path $pythonw)) {
    throw "No venv found at $pythonw. Create it first (see README.md, step 3)."
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$worker`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "Studio Worker" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Tags phone footage from the Studio inbox (personal_assistant/studio)." -Force | Out-Null

Start-ScheduledTask -TaskName "Studio Worker"
Write-Host "Studio Worker registered and started. Log: $env:USERPROFILE\Studio\logs\worker.log"
