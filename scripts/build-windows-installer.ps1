param(
    [switch]$SkipDependencyInstall,
    [string]$Version,
    [switch]$AutoVersion,
    [string]$Firmware,
    [switch]$BootloaderWithoutCfg
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing .runtime Python. Run scripts/setup.ps1 -SkipLocalLLM first."
}
function Get-CfgBootloaderRequirement {
    # The windowed Mythlink executable uses runw.exe from the PyInstaller package.
    $Probe = @'
from pathlib import Path

import pefile
import PyInstaller

bootloader = Path(PyInstaller.__file__).parent / 'bootloader' / PyInstaller.PLATFORM / 'runw.exe'
with pefile.PE(str(bootloader), fast_load=True) as image:
    if image.OPTIONAL_HEADER.DllCharacteristics & 0x4000:
        print('PyInstaller==' + PyInstaller.__version__)
'@
    $Requirement = & $Python -c $Probe
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the PyInstaller Windows bootloader." }
    return $Requirement
}

$VersionArgs = @()
if ($Version) { $VersionArgs += @("--version", $Version) }
if ($AutoVersion) { $VersionArgs += "--auto-version" }
$AppVersion = & $Python (Join-Path $ProjectRoot "tools\build_version.py") @VersionArgs
if ($LASTEXITCODE -ne 0) { throw "Version selection failed." }
Write-Host "Building Mythlink $AppVersion"

$SdkArgs = @()
if ($SkipDependencyInstall) {
    $SdkArgs += "--no-deps"
} else {
    & $Python -m pip install -r (Join-Path $ProjectRoot "requirements-packaging.txt")
    if ($LASTEXITCODE -ne 0) { throw "Packaging dependency installation failed." }
}
if ($BootloaderWithoutCfg) {
    $CfgBootloaderRequirement = Get-CfgBootloaderRequirement
    if ($CfgBootloaderRequirement) {
        if ($SkipDependencyInstall) {
            throw "The Mythlink bootloader still uses CFG. Run build-windows.ps1 without -SkipDependencyInstall to rebuild it."
        }
        # R1's Unicorn runtime is incompatible with CFG in the PyInstaller bootloader.
        Write-Host "Rebuilding the Mythlink Windows bootloader without CFG..."
        $PreviousCompileBootloader = $env:PYINSTALLER_COMPILE_BOOTLOADER
        $PreviousWafArgs = $env:PYINSTALLER_BOOTLOADER_WAF_ARGS
        try {
            $env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
            $env:PYINSTALLER_BOOTLOADER_WAF_ARGS = "--no-cfg"
            & $Python -m pip install --force-reinstall --no-cache-dir --no-deps --no-binary=PyInstaller $CfgBootloaderRequirement
            if ($LASTEXITCODE -ne 0) {
                throw "PyInstaller bootloader rebuild failed. Check that Visual Studio C++ Build Tools and the Windows SDK are installed."
            }
        } finally {
            $env:PYINSTALLER_COMPILE_BOOTLOADER = $PreviousCompileBootloader
            $env:PYINSTALLER_BOOTLOADER_WAF_ARGS = $PreviousWafArgs
        }
        if (Get-CfgBootloaderRequirement) {
            throw "The rebuilt Mythlink Windows bootloader still has CFG enabled."
        }
    }
}
& $Python (Join-Path $ProjectRoot "tools\install_r1_sdk.py") @SdkArgs
if ($LASTEXITCODE -ne 0) { throw "R1 SDK installation failed." }

$PreviousFirmware = $env:MYTHLINK_RING_FIRMWARE
$PreviousBuildVersion = $env:MYTHLINK_BUILD_VERSION
if ($Firmware) { $Firmware = (Resolve-Path -LiteralPath $Firmware).Path }
Push-Location -LiteralPath $ProjectRoot
try {
    if ($Firmware) { $env:MYTHLINK_RING_FIRMWARE = $Firmware }
    $env:MYTHLINK_BUILD_VERSION = $AppVersion
    & $Python -m PyInstaller --noconfirm --clean "packaging\mythlink.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    $BundledExe = Join-Path $ProjectRoot "dist\Mythlink\Mythlink.exe"
    $PackageCheck = Start-Process -FilePath $BundledExe `
        -ArgumentList "--self-check-package" -PassThru -Wait -WindowStyle Hidden
    if ($PackageCheck.ExitCode -ne 0) {
        throw "Bundled application self-check failed (exit $($PackageCheck.ExitCode)); check the startup log under LOCALAPPDATA\ProxiMic Voice\logs."
    }
    Write-Host "Bundled QML, ADPCM, and ASR self-check passed."

    $IsccCandidates = @(
        "$ProjectRoot\.build\tools\InnoSetup6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $IsccCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $Iscc) {
        throw "Inno Setup 6 not found. Install it, then rerun this script."
    }
    & $Iscc "/DMyAppVersion=$AppVersion" "packaging\windows\Mythlink.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }

    $Installer = Join-Path $ProjectRoot "dist\installer\Mythlink-$AppVersion-windows-x64-setup.exe"
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) { throw "Installer not found: $Installer" }
    if ($env:WINDOWS_SIGNING_CERT_SHA1) {
        $SignToolCandidates = @(
            (Get-Command signtool.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
            "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe",
            "$env:ProgramFiles\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
        )
        $SignTool = $SignToolCandidates | Where-Object {
            $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
        } | Select-Object -First 1
        if (-not $SignTool) {
            throw "WINDOWS_SIGNING_CERT_SHA1 is set, but signtool.exe was not found."
        }
        $TimestampUrl = if ($env:WINDOWS_TIMESTAMP_URL) {
            $env:WINDOWS_TIMESTAMP_URL
        } else {
            "http://timestamp.digicert.com"
        }
        & $SignTool sign /sha1 $env:WINDOWS_SIGNING_CERT_SHA1 /fd SHA256 `
            /tr $TimestampUrl /td SHA256 $Installer
        if ($LASTEXITCODE -ne 0) { throw "Windows code signing failed." }
        & $SignTool verify /pa $Installer
        if ($LASTEXITCODE -ne 0) { throw "Windows signature verification failed." }
    }
} finally {
    $env:MYTHLINK_BUILD_VERSION = $PreviousBuildVersion
    $env:MYTHLINK_RING_FIRMWARE = $PreviousFirmware
    Pop-Location
}

Write-Host "Installer: $Installer"
