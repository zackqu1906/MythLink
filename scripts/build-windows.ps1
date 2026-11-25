[CmdletBinding()]
param(
    [string]$Version,
    [switch]$AutoVersion,
    [string]$Firmware,
    [string]$R1SdkWheel,
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

$PreviousWheel = $env:MYTHLINK_R1_SDK_WHEEL
$Wheel = if ($R1SdkWheel) { $R1SdkWheel } else { $PreviousWheel }
if ($Wheel) {
    $WheelFile = Get-Item -LiteralPath $Wheel
    if ($WheelFile -isnot [IO.FileInfo] -or $WheelFile.Extension -ne ".whl") {
        throw "The R1 SDK must be a local wheel file."
    }
    $Wheel = $WheelFile.FullName
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    $env:MYTHLINK_R1_SDK_WHEEL = $Wheel
    if (-not $SkipDependencyInstall) {
        & (Join-Path $PSScriptRoot "setup.ps1") -Compute cpu -SkipLocalLLM
    }
    & (Join-Path $PSScriptRoot "build-windows-installer.ps1") @InstallerArgs
} finally {
    $env:MYTHLINK_R1_SDK_WHEEL = $PreviousWheel
    Pop-Location
}
