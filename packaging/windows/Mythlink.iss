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

[Files]
Source: "..\..\dist\Mythlink\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
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
