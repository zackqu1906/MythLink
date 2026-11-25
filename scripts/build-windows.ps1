[CmdletBinding()]
param(
    [string]$Version,
    [switch]$AutoVersion,
    [string]$Firmware,
    [switch]$SkipDependencyInstall,
    [switch]$InstallerOnly
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "Run this script on 64-bit Windows to build the x64 installer."
}
if ($Version -and $AutoVersion) {
    throw "Choose either -Version or -AutoVersion."
}

$InstallerArgs = @{
    SkipDependencyInstall = $SkipDependencyInstall
    BootloaderWithoutCfg = $true
    PrepareRuntime = $true
    InstallerOnly = $InstallerOnly
}
if ($Version) { $InstallerArgs.Version = $Version }
if ($AutoVersion) { $InstallerArgs.AutoVersion = $true }
if ($Firmware) {
    $FirmwareFile = Get-Item -LiteralPath $Firmware
    if ($FirmwareFile -isnot [IO.FileInfo]) { throw "-Firmware must point to a firmware ZIP file." }
    $InstallerArgs.Firmware = $FirmwareFile.FullName
}

& (Join-Path $PSScriptRoot "build-windows-installer.ps1") @InstallerArgs
