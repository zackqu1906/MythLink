param(
    [Parameter(Mandatory)]
    [ValidateSet('Build', 'Install', 'Uninstall', 'Enable', 'Disable', 'Run', 'Status')]
    [string]$Action,
    [string]$Compiler
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $Root 'native/ProxiMicInputWindows'
$Build = Join-Path $Root '.build/windows-ime'
$Dll = Join-Path $Build 'MythlinkTsf.dll'
$BundledDll = Join-Path $PSScriptRoot 'MythlinkTsf.dll'
$InstallDirectory = Join-Path $env:ProgramFiles 'Mythlink TSF Prototype'
$RegistrationKey = 'HKLM:\Software\Classes\CLSID\{40b2749b-c169-4a28-921c-c3367b96482b}\InprocServer32'

# Apply the simplified Chinese TSF profile to the invoking user's input list.
if ($Action -in @('Enable', 'Disable')) {
    Add-Type -Namespace Mythlink -Name UserInput -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("input.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, ExactSpelling = true)]
[System.Runtime.InteropServices.DefaultDllImportSearchPaths(System.Runtime.InteropServices.DllImportSearchPath.System32)]
public static extern bool InstallLayoutOrTip(string profile, uint flags);
"@
    $Profile = '0x0804:{40B2749B-C169-4A28-921C-C3367B96482B}{D31DD85F-750D-498B-A2C3-6282DD4E7552}'
    $Flags = if ($Action -eq 'Disable') { 1 } else { 0 }
    if (![Mythlink.UserInput]::InstallLayoutOrTip($Profile, $Flags)) {
        throw "Windows could not $Action the Mythlink input profile."
    }
    return
}

if ($Action -eq 'Status') {
    $Key = Get-Item -LiteralPath $RegistrationKey -ErrorAction SilentlyContinue
    $RegisteredDll = if ($Key) { $Key.GetValue('') }
    if ($RegisteredDll) { Write-Host "Registered DLL: $RegisteredDll" }
    Get-Process | ForEach-Object {
        $Process = $_
        try {
            $Process.Modules | Where-Object { $_.FileName -like "$InstallDirectory\MythlinkTsf*.dll" } |
                ForEach-Object {
                    $Version = if ($_.FileName -eq $RegisteredDll) { 'Current' } else { 'Old (restart app)' }
                    [pscustomobject]@{ Process = $Process.ProcessName; Id = $Process.Id; Version = $Version; Dll = $_.FileName }
                }
        } catch {}
    }
    return
}

if ($Action -eq 'Build') {
    New-Item -Path $Build -ItemType Directory -Force | Out-Null
    if ($Compiler) {
        $Windres = & $Compiler -print-prog-name=windres
        & $Windres -I $Source -i "$Source/MythlinkTsf.rc" -O coff -o "$Build/MythlinkTsf.res.o"
        if ($LASTEXITCODE) { throw "Could not compile the input method icon." }
        & $Compiler -std=c++17 -O2 -static -shared "$Source/TextService.cpp" "$Source/MythlinkTsf.def" "$Build/MythlinkTsf.res.o" -o $Dll -lole32 -loleaut32 -ladvapi32 -luuid
    } else {
        $CMake = Join-Path $Root '.runtime/venv/Scripts/cmake.exe'
        if (!(Test-Path -LiteralPath $CMake -PathType Leaf)) {
            $CMake = (Get-Command cmake -CommandType Application -ErrorAction SilentlyContinue).Source
            if (!$CMake) { throw "Install CMake to build the Windows TSF input method." }
        }
        & $CMake -S $Source -B "$Build/cmake" -A x64
        if ($LASTEXITCODE) { throw "Could not configure the Windows TSF build." }
        & $CMake --build "$Build/cmake" --config Release
        if (!$LASTEXITCODE) { Copy-Item "$Build/cmake/Release/MythlinkTsf.dll" $Dll -Force }
    }
    if ($LASTEXITCODE) { throw "Could not build MythlinkTsf.dll." }
    Get-Item -LiteralPath $Dll
    return
}

if ($Action -in @('Install', 'Uninstall')) {
    $Principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (!$Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Open PowerShell as Administrator to register or unregister the Windows input method."
    }
    $Key = Get-Item -LiteralPath $RegistrationKey -ErrorAction SilentlyContinue
    if ($Action -eq 'Install') {
        if (Test-Path -LiteralPath $BundledDll -PathType Leaf) { $Dll = $BundledDll }
        if (!(Test-Path -LiteralPath $Dll -PathType Leaf)) { throw "MythlinkTsf.dll is missing. Run this script with -Action Build first." }
        New-Item -Path $InstallDirectory -ItemType Directory -Force | Out-Null
        $Hash = (Get-FileHash -LiteralPath $Dll -Algorithm SHA256).Hash
        $InstalledDll = Join-Path $InstallDirectory "MythlinkTsf-$Hash.dll"
        if (!(Test-Path -LiteralPath $InstalledDll)) {
            $StagedDll = Join-Path $InstallDirectory ([guid]::NewGuid().ToString() + '.tmp')
            try {
                Copy-Item -LiteralPath $Dll -Destination $StagedDll
                if ((Get-FileHash -LiteralPath $StagedDll -Algorithm SHA256).Hash -ne $Hash) {
                    throw "The build changed while being copied. Run Install again."
                }
                [IO.File]::Move($StagedDll, $InstalledDll)
            } finally {
                if (Test-Path -LiteralPath $StagedDll) { Remove-Item -LiteralPath $StagedDll }
            }
        } elseif ((Get-FileHash -LiteralPath $InstalledDll -Algorithm SHA256).Hash -ne $Hash) {
            throw "An installed DLL has unexpected contents: $InstalledDll"
        }
        if ($Key) {
            # Keep the existing TSF profile and change only its COM server path.
            Set-Item -LiteralPath $RegistrationKey -Value $InstalledDll
            Write-Host "Updated Mythlink. Restart any app still using the previous DLL."
            return
        }
        $Arguments = @('/s', "`"$InstalledDll`"")
    } else {
        if ($Key) {
            $InstalledDll = $Key.GetValue('')
            if (!(Test-Path -LiteralPath $InstalledDll -PathType Leaf)) {
                throw "The registered DLL is missing: $InstalledDll"
            }
            $Arguments = @('/u', '/s', "`"$InstalledDll`"")
        }
    }
    if ($Arguments) {
        $Registration = Start-Process -FilePath "$env:WINDIR/System32/regsvr32.exe" -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
        if ($Registration.ExitCode) { throw "TSF registration action failed with exit code $($Registration.ExitCode)." }
    }
    if ($Action -eq 'Uninstall' -and (Test-Path -LiteralPath $InstallDirectory)) {
        Get-ChildItem -LiteralPath $InstallDirectory -File |
            Where-Object { $_.Name -match '^MythlinkTsf(-[0-9A-F]{64})?\.dll$' } |
            ForEach-Object {
                try { Remove-Item -LiteralPath $_.FullName -ErrorAction Stop }
                catch { Write-Warning "Could not remove $($_.TargetObject). Close apps using Mythlink and run Uninstall again." }
            }
    }
    return
}

$env:MYTHLINK_WINDOWS_TSF = '1'
& (Join-Path $Root 'scripts/start-ui.cmd')
exit $LASTEXITCODE
