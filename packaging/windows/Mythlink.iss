#define MyAppName "Mythlink"
#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build script
#endif
#define MyAppPublisher "Mythlink"
#define MyAppExeName "Mythlink.exe"

[Setup]
AppId={{7F4E8DCA-785E-4D6D-A582-49736863F7B8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={%LOCALAPPDATA}\Programs\Mythlink
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=yes
ShowLanguageDialog=no
UsePreviousLanguage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=Mythlink-{#MyAppVersion}-windows-x64-setup
#ifdef WineBuild
Compression=zip
#else
Compression=lzma2/ultra64
SolidCompression=yes
#endif
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
english.InputTask=Add the Mythlink input method (requires administrator approval)
chinesesimplified.InputTask=添加 Mythlink 输入法（需要管理员授权）
english.InputInstallError=Could not register the Mythlink input method. Retry or go back and clear the input method task.
chinesesimplified.InputInstallError=未能注册 Mythlink 输入法。请重试，或返回取消勾选输入法后继续安装。
english.InputEnableError=Windows could not add Mythlink to your input methods. Retry or go back and clear the input method task.
chinesesimplified.InputEnableError=Windows 未能将 Mythlink 添加到当前用户的输入法列表。请重试，或返回取消勾选输入法后继续安装。
english.InputShellRefresh=Windows Explorer is still using the previous MythLink input method. Restart Windows Explorer in Task Manager, or sign out and back in, to refresh the taskbar icon.
chinesesimplified.InputShellRefresh=Windows 资源管理器仍在使用旧版 MythLink 输入法。在任务管理器中重新启动“Windows 资源管理器”，或注销并重新登录，即可让任务栏重新加载输入法图标。

[InstallDelete]
; Older installers left discoverable model sources and partial optional
; Python packages behind. Only clean the bundled ASR code, before copying it.
Type: filesandordirs; Name: "{app}\_internal\funasr"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\transformers\models"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\numba"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\llvmlite"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\llvmlite.libs"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\librosa"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\scipy"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\scipy.libs"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\sklearn"; Check: HasPreviousMythlinkRuntime
Type: filesandordirs; Name: "{app}\_internal\soxr"; Check: HasPreviousMythlinkRuntime

[Files]
Source: "..\..\.build\windows-ime\MythlinkInputSetup.exe"; Flags: dontcopy
Source: "..\..\scripts\windows-ime-prototype.ps1"; DestDir: "{app}\_internal\windows-ime"; Flags: ignoreversion
Source: "..\..\dist\Mythlink\*"; DestDir: "{app}"; Excludes: "_internal\windows-ime\windows-ime-prototype.ps1"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Mythlink\Installer"; ValueType: dword; ValueName: "InputProfile"; ValueData: "1"; Tasks: inputmethod; Flags: uninsdeletevalue uninsdeletekeyifempty

[Tasks]
Name: "inputmethod"; Description: "{cm:InputTask}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  ShellRefreshNeeded: Boolean;

function HasPreviousMythlinkRuntime: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#MyAppExeName}')) and
    FileExists(ExpandConstant('{app}\_internal\proximic_ring\package-version.txt'));
end;

function SetInputProfile(const ScriptPath, Action: String): Boolean;
var
  ExitCode: Integer;
  Options: String;
begin
  Options := '-Action ' + Action;
  if Action = 'Enable' then Options := Options + ' -ReportShellRefresh';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ScriptPath + '" ' + Options,
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  ShellRefreshNeeded := Result and (Action = 'Enable') and (ExitCode = 10);
  Result := Result and ((ExitCode = 0) or ShellRefreshNeeded);
  Log(Format('Input profile %s returned %d', [Action, ExitCode]));
end;

procedure CurPageChanged(CurPageID: Integer);
var
  HeightChange: Integer;
begin
  if (CurPageID = wpFinished) and ShellRefreshNeeded then begin
    WizardForm.FinishedLabel.Caption := CustomMessage('InputShellRefresh');
    HeightChange := WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
    WizardForm.RunList.Top := WizardForm.RunList.Top + HeightChange;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if not WizardIsTaskSelected('inputmethod') then Exit;
  ExtractTemporaryFile('MythlinkInputSetup.exe');
  if not Exec(ExpandConstant('{tmp}\MythlinkInputSetup.exe'),
      '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-', '', SW_HIDE, ewWaitUntilTerminated, ExitCode)
      or (ExitCode <> 0) then begin
    Log(Format('Input method setup returned %d', [ExitCode]));
    Result := CustomMessage('InputInstallError');
    Exit;
  end;
  ExtractTemporaryFile('windows-ime-prototype.ps1');
  if not SetInputProfile(ExpandConstant('{tmp}\windows-ime-prototype.ps1'), 'Enable') then
    Result := CustomMessage('InputEnableError');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and
     RegValueExists(HKCU, 'Software\Mythlink\Installer', 'InputProfile') then
    SetInputProfile(ExpandConstant('{app}\_internal\windows-ime\windows-ime-prototype.ps1'), 'Disable');
end;

procedure InitializeWizard;
var
  Padding: Integer;
begin
  { The checkbox column in Inno Setup 6 can be measured at a lower DPI than its glyph. }
  Padding := ScaleX(16) - 16;
  if Padding > 0 then
  begin
    WizardForm.TasksList.Offset := WizardForm.TasksList.Offset + Padding;
    WizardForm.RunList.Offset := WizardForm.RunList.Offset + Padding;
  end;
end;
