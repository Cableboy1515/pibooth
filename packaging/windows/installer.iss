; Inno Setup script producing the pibooth Windows installer.
;
; Expects the PyInstaller one-dir bundle in <repo>/dist/pibooth (built with
; packaging/windows/pibooth.spec). Compile from the repository root with:
;   ISCC.exe /DAppVersion=<version> packaging\windows\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6CC5B503-75C9-43F4-AF87-C17096B5ACEE}
AppName=pibooth
AppVersion={#AppVersion}
AppPublisher=pibooth project
AppPublisherURL=https://github.com/pibooth/pibooth
DefaultDirName={autopf}\pibooth
DefaultGroupName=pibooth
DisableProgramGroupPage=yes
; Allow per-user installation without administrator rights
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=pibooth-setup-{#AppVersion}
SetupIconFile=pibooth.ico
UninstallDisplayIcon={app}\pibooth.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\pibooth\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\pibooth"; Filename: "{app}\pibooth.exe"
Name: "{autodesktop}\pibooth"; Filename: "{app}\pibooth.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\pibooth.exe"; Description: "{cm:LaunchProgram,pibooth}"; Flags: nowait postinstall skipifsilent
