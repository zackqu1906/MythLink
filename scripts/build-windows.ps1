[CmdletBinding()]
param(
    [string]$Version,
    [switch]$AutoVersion,
    [string]$Firmware,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "Run this script on 64-bit Windows to build the x64 installer."
}
if ($Version -and $AutoVersion) {
    throw "Choose either -Version or -AutoVersion."
}

$InstallerArgs = @{ SkipDependencyInstall = $SkipDependencyInstall }
if ($Version) { $InstallerArgs.Version = $Version }
if ($AutoVersion) { $InstallerArgs.AutoVersion = $true }
if ($Firmware) {
    $FirmwareFile = Get-Item -LiteralPath $Firmware
    if ($FirmwareFile -isnot [IO.FileInfo]) { throw "-Firmware must point to a firmware ZIP file." }
    $InstallerArgs.Firmware = $FirmwareFile.FullName
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    if (-not $SkipDependencyInstall) {
        & (Join-Path $PSScriptRoot "setup.ps1") -Compute cpu -SkipLocalLLM
    }
    & (Join-Path $PSScriptRoot "build-windows-installer.ps1") @InstallerArgs
} finally {
    Pop-Location
}
