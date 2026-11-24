; ClipMic Inno Setup 6 Installer Script
; Version MUST be passed from scripts/build_release.ps1 via /DMyAppVersion=X.Y.Z
; NEVER change AppId below — changing it causes duplicate installations on upgrade.
; User clips, configs, and logs live in %LOCALAPPDATA%\ClipMic and are NEVER deleted.

#ifndef MyAppVersion
  #error "MyAppVersion must be passed via /DMyAppVersion=X.Y.Z (derived from paths.py)"
#endif

#define MyAppName "ClipMic"
#define MyAppPublisher "ClipMic"
#define MyAppURL "https://github.com/3bdyna/clipmic"
#define MyAppExeName "ClipMic.exe"

[Setup]
; Fixed static GUID — DO NOT CHANGE across releases
AppId={{8D4F6C2B-9A3E-4E71-B5C8-3F1D2E6A940B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex=Global\ClipMic_SingleInstance_Mutex
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\clipmic.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
OutputDir=..\dist\release
OutputBaseFilename=ClipMic-Setup-{#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clean stale PyInstaller _internal binaries on in-place upgrades without touching user data
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ClipMic\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Normal interactive install: offer to launch ClipMic when Setup finishes
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; Silent self-update handoff: automatically relaunch ClipMic when /RELAUNCH was passed
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent; Check: HasRelaunchSwitch

[Code]
function HasRelaunchSwitch: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
var
  WaitMs: Integer;
begin
  Result := True;
  { During silent self-updates, wait up to 12s for the exiting ClipMic instance
    to release Global\ClipMic_SingleInstance_Mutex before Inno checks AppMutex. }
  if WizardSilent() then
  begin
    WaitMs := 0;
    while CheckForMutexes('Global\ClipMic_SingleInstance_Mutex') and (WaitMs < 12000) do
    begin
      Sleep(250);
      WaitMs := WaitMs + 250;
    end;
  end;
end;
