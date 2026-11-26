#define InputDll "..\..\dist\Mythlink\_internal\windows-ime\MythlinkTsf.dll"
#define InstalledDll "MythlinkTsf-" + GetSHA256OfFile(SourcePath + "\" + InputDll) + ".dll"

[Setup]
AppId=MythlinkTsf
AppName=Mythlink Input Method
AppVersion={#MyAppVersion}
DefaultDirName={commonpf64}\Mythlink Input Method
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\.build\windows-ime
OutputBaseFilename=MythlinkInputSetup
CloseApplications=no

[Files]
; TSF editors can hold an older DLL open throughout the upgrade.
Source: "{#InputDll}"; DestDir: "{commonpf64}\Mythlink Input Method"; DestName: "{#InstalledDll}"; Flags: onlyifdoesntexist uninsrestartdelete; AfterInstall: RegisterInputMethod

[Code]
function ServiceDll: String;
begin
  Result := ExpandConstant('{commonpf64}\Mythlink Input Method\{#InstalledDll}');
end;

procedure RegisterInputMethod;
begin
  RegisterServer(True, ServiceDll, False);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RegisteredDll: String;
begin
  if (CurUninstallStep = usUninstall) and
     RegQueryStringValue(HKLM64, 'Software\Classes\CLSID\{40b2749b-c169-4a28-921c-c3367b96482b}\InprocServer32', '', RegisteredDll) and
     (CompareText(RegisteredDll, ServiceDll) = 0) then
    if not UnregisterServer(True, ServiceDll, False) then
      RaiseException('Could not unregister the Mythlink input method.');
end;
