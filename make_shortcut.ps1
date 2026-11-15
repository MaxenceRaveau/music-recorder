# Creates a shortcut that launches recorder_gui.py with the project's .venv pythonw.exe (no console).
param(
    [string]$Destination = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot

$pythonw = Join-Path $project '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Run 'uv sync' first." }

$lnk = Join-Path $Destination 'Audio Recorder.lnk'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $pythonw
$sc.Arguments = '"' + (Join-Path $project 'recorder_gui.py') + '"'
$sc.WorkingDirectory = $project
$sc.Description = 'Audio Recorder'
$sc.Save()
Write-Host "Created $lnk"
