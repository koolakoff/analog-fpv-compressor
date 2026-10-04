param(
    [string]$Program = "$PSScriptRoot\..\.venv\Scripts\fpv-compress-gui.exe",
    [string]$Destination = "$PSScriptRoot\..\fpv-compress.lnk"
)
$ErrorActionPreference = 'Stop'
$programPath = (Resolve-Path -LiteralPath $Program).Path
$iconPath = (Resolve-Path -LiteralPath "$PSScriptRoot\..\src\analog_fpv_compressor\gui\assets\icon-big.ico").Path
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut([System.IO.Path]::GetFullPath($Destination))
$shortcut.TargetPath = $programPath
$shortcut.WorkingDirectory = Split-Path -Parent $programPath
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'Analog FPV Compressor'
$shortcut.Save()
