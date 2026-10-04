# Install the full FFmpeg build through the official Windows package manager.
$ErrorActionPreference = 'Stop'
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host 'WinGet is not available. Install Microsoft App Installer or download the full build from https://www.gyan.dev/ffmpeg/builds/.'
    Write-Host 'Place ffmpeg.exe and ffprobe.exe in the tools directory next to fpv-compress.exe.'
    exit 1
}
& winget install --exact --id Gyan.FFmpeg --source winget --scope user --accept-package-agreements --accept-source-agreements
if ($LASTEXITCODE -ne 0) {
    Write-Host 'WinGet did not complete installation. Check its message above; FFmpeg may already be installed.'
    exit $LASTEXITCODE
}
Write-Host 'FFmpeg installation completed. fpv-compress also discovers the WinGet installation without a PATH refresh.'
