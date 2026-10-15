# Puts a "Studio" icon on your desktop. Double-click it to start the worker (if it
# isn't already running) and open the dashboard. Run once from the studio folder:
#   powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $here "venv\Scripts\pythonw.exe"

if (-not (Test-Path $pythonw)) {
    throw "No venv found at $pythonw. Create it first (see README.md, step 3)."
}

# GetFolderPath follows OneDrive's desktop redirection, unlike $env:USERPROFILE\Desktop.
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop "Studio.lnk"))
$link.TargetPath = $pythonw
$link.Arguments = "`"$(Join-Path $here 'launch.py')`""
$link.WorkingDirectory = $here
$link.IconLocation = Join-Path $here "web\studio.ico"
$link.Description = "Start the studio worker and open the review dashboard"
$link.Save()

Write-Host "Studio icon added to $desktop"
