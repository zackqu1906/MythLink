param(
    [switch]$SkipDependencyInstall,
    [string]$Version,
    [switch]$AutoVersion,
    [string]$Firmware,
    [switch]$BootloaderWithoutCfg,
    [switch]$PrepareRuntime,
    [switch]$InstallerOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".runtime\venv\Scripts\python.exe"

$BundledExe = Join-Path $ProjectRoot "dist\Mythlink\Mythlink.exe"

function Get-InnoSetupCompiler {
    # Match the pinned release used by the Wine build.
    $InnoVersion = "6.7.4"
    $Tools = Join-Path $ProjectRoot ".build\tools"
    $PortableDir = Join-Path $Tools "InnoSetup-$InnoVersion"
    $PortableCompiler = Join-Path $PortableDir "ISCC.exe"
    $Candidates = @(
        $PortableCompiler,
        (Join-Path $Tools "InnoSetup6\ISCC.exe"),
        (Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $Compiler = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    if ($Compiler) { return $Compiler }

    Write-Host "Preparing Inno Setup $InnoVersion in the project..."
    New-Item -ItemType Directory -Path $Tools -Force | Out-Null
    $Stage = New-Item -ItemType Directory -Path (Join-Path $Tools ([IO.Path]::GetRandomFileName()))
    try {
        $Download = Join-Path $Stage.FullName "innosetup.exe"
        $Unpacked = Join-Path $Stage.FullName "InnoSetup"
        $Url = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-$InnoVersion.exe"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Download -TimeoutSec 120 | Out-Null
        } catch {
            throw "Could not download Inno Setup $InnoVersion. Install Inno Setup 6 or put ISCC.exe on PATH and retry. $($_.Exception.Message)"
        }
        $ExpectedHash = "a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c"
        if ((Get-FileHash -LiteralPath $Download -Algorithm SHA256).Hash -ne $ExpectedHash) {
            throw "The Inno Setup download failed SHA-256 verification."
        }
        $InstallArgs = @("/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/PORTABLE=1", "/DIR=`"$Unpacked`"")
        $Install = Start-Process -FilePath $Download -ArgumentList $InstallArgs `
            -WorkingDirectory $Stage.FullName -PassThru -Wait
        if ($Install.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $Unpacked "ISCC.exe") -PathType Leaf)) {
            throw "Inno Setup portable installation failed (exit $($Install.ExitCode))."
        }
        try {
            [IO.Directory]::Move($Unpacked, $PortableDir)
        } catch [IO.IOException] {
            if (-not (Test-Path -LiteralPath $PortableCompiler -PathType Leaf)) { throw }
        }
    } finally {
        Remove-Item -LiteralPath $Stage.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $PortableCompiler
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

function Set-InstallerSignature([string]$Installer) {
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
}

if ($InstallerOnly) {
    if ($Version -or $AutoVersion -or $Firmware) {
        throw "-InstallerOnly uses the existing bundle's version and firmware. Omit -Version, -AutoVersion, and -Firmware."
    }
    $VersionFile = Join-Path $ProjectRoot "dist\Mythlink\_internal\proximic_ring\package-version.txt"
    if (-not (Test-Path -LiteralPath $BundledExe -PathType Leaf) -or -not (Test-Path -LiteralPath $VersionFile -PathType Leaf)) {
        throw "The built Mythlink application or its version file is missing from dist\Mythlink. Run a full Windows build first."
    }
    $AppVersion = ([string](Get-Content -LiteralPath $VersionFile -Raw)).Trim()
    if ($AppVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
        throw "The built Mythlink application has an invalid version. Run a full Windows build first."
    }
} elseif ($Firmware) {
    $Firmware = (Resolve-Path -LiteralPath $Firmware).Path
}
$Iscc = Get-InnoSetupCompiler

$PreviousFirmware = $env:MYTHLINK_RING_FIRMWARE
$PreviousBuildVersion = $env:MYTHLINK_BUILD_VERSION
Push-Location -LiteralPath $ProjectRoot
try {
    if ($InstallerOnly) {
        Write-Host "Packaging existing Mythlink $AppVersion"
    } else {
        if ($PrepareRuntime -and -not $SkipDependencyInstall) {
            & (Join-Path $PSScriptRoot "setup.ps1") -Compute cpu -SkipLocalLLM
        }
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
            throw "Missing .runtime Python. Run scripts/setup.ps1 -SkipLocalLLM first."
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
            & $Python -m pip install -c (Join-Path $ProjectRoot "requirements-windows.lock") -r (Join-Path $ProjectRoot "requirements-packaging.txt")
            if ($LASTEXITCODE -ne 0) { throw "Packaging dependency installation failed." }
        }
        Write-Host "Building the Windows TSF input method..."
        & (Join-Path $PSScriptRoot "windows-ime-prototype.ps1") -Action Build -Compiler $env:MYTHLINK_TSF_COMPILER | Out-Host

        if ($BootloaderWithoutCfg) {
            $CfgBootloaderRequirement = Get-CfgBootloaderRequirement
            if ($CfgBootloaderRequirement) {
                if ($SkipDependencyInstall) {
                    throw "The Mythlink bootloader still uses CFG. Run build-windows.ps1 without -SkipDependencyInstall to rebuild it."
                }
                # R1's Unicorn runtime is incompatible with CFG in the PyInstaller bootloader.
                Write-Host "Rebuilding the Mythlink Windows bootloader without CFG..."
                # pip extracts a deeply nested source tree before starting the compiler.
                $BuildRoot = New-Item -ItemType Directory -Path (Join-Path $ProjectRoot ".build") -Force
                $BootloaderTemp = New-Item -ItemType Directory -Path (Join-Path $BuildRoot.FullName ([IO.Path]::GetRandomFileName()))
                $BootloaderEnvironment = @{
                    PYINSTALLER_COMPILE_BOOTLOADER = "1"
                    PYINSTALLER_BOOTLOADER_WAF_ARGS = "--no-cfg"
                    TMPDIR = $BootloaderTemp.FullName
                    TEMP = $BootloaderTemp.FullName
                    TMP = $BootloaderTemp.FullName
                }
                $PreviousEnvironment = @{}
                try {
                    foreach ($Name in $BootloaderEnvironment.Keys) {
                        $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name)
                        [Environment]::SetEnvironmentVariable($Name, $BootloaderEnvironment[$Name])
                    }
                    & $Python -m pip install --force-reinstall --no-cache-dir --no-deps --no-binary=PyInstaller $CfgBootloaderRequirement
                    if ($LASTEXITCODE -ne 0) {
                        throw "PyInstaller bootloader rebuild failed (pip exit $LASTEXITCODE). See the pip output above."
                    }
                } finally {
                    foreach ($Name in $PreviousEnvironment.Keys) {
                        [Environment]::SetEnvironmentVariable($Name, $PreviousEnvironment[$Name])
                    }
                    Remove-Item -LiteralPath $BootloaderTemp.FullName -Recurse -Force -ErrorAction SilentlyContinue
                }
                if (Get-CfgBootloaderRequirement) {
                    throw "The rebuilt Mythlink Windows bootloader still has CFG enabled."
                }
            }
        }
        & $Python (Join-Path $ProjectRoot "tools\install_r1_sdk.py") @SdkArgs
        if ($LASTEXITCODE -ne 0) { throw "R1 SDK installation failed." }

        if ($Firmware) { $env:MYTHLINK_RING_FIRMWARE = $Firmware }
        $env:MYTHLINK_BUILD_VERSION = $AppVersion
        & $Python -m PyInstaller --noconfirm --clean "packaging\mythlink.spec"
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    }

    $PackageCheck = Start-Process -FilePath $BundledExe `
        -ArgumentList "--self-check-package" -PassThru -Wait -WindowStyle Hidden
    if ($PackageCheck.ExitCode -ne 0) {
        throw "Bundled application self-check failed (exit $($PackageCheck.ExitCode)); check the startup log under LOCALAPPDATA\ProxiMic Voice\logs."
    }
    Write-Host "Bundled Ringo, R1, QML, ADPCM, and ASR self-check passed."

    & $Iscc "/DMyAppVersion=$AppVersion" "packaging\windows\MythlinkInput.iss"
    if ($LASTEXITCODE -ne 0) { throw "Input method installer build failed." }
    Set-InstallerSignature (Join-Path $ProjectRoot ".build\windows-ime\MythlinkInputSetup.exe")

    & $Iscc "/DMyAppVersion=$AppVersion" "packaging\windows\Mythlink.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }

    $Installer = Join-Path $ProjectRoot "dist\installer\Mythlink-$AppVersion-windows-x64-setup.exe"
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) { throw "Installer not found: $Installer" }
    Set-InstallerSignature $Installer
} finally {
    $env:MYTHLINK_BUILD_VERSION = $PreviousBuildVersion
    $env:MYTHLINK_RING_FIRMWARE = $PreviousFirmware
    Pop-Location
}

Write-Host "Installer: $Installer"
