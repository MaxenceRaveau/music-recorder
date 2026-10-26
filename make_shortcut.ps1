# Creates a shortcut that launches recorder_gui.py with pythonw.exe (no console).
param(
    [string]$Destination = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot

# Prefer the pythonw next to the python on PATH, so the same interpreter is used.
$pythonw = $null
$python = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($python) {
    $candidate = Join-Path (Split-Path $python.Source) 'pythonw.exe'
    if (Test-Path $candidate) { $pythonw = $candidate }
}
if (-not $pythonw) {
    $fallback = Get-Command pythonw -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($fallback) { $pythonw = $fallback.Source }
}
if (-not $pythonw) { throw 'pythonw.exe not found on PATH.' }

$lnk = Join-Path $Destination 'Audio Recorder.lnk'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $pythonw
$sc.Arguments = '"' + (Join-Path $project 'recorder_gui.py') + '"'
$sc.WorkingDirectory = $project
$sc.Description = 'Audio Recorder'
$sc.Save()
Write-Host "Created $lnk"
