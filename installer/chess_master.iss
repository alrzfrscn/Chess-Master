; Reference script for building the same installer with Inno Setup.
; Our shipped setup is scripts/installer_stub.py compiled by PyInstaller.
#define MyAppName "Chess Master"
#define MyAppVersion "1.0.0"
#define MyAppExeName "ChessMaster.exe"

[Setup]
AppId={{8C2F4E9A-6B1D-4C7E-9A3F-5D2B8E1C0A47}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\Chess Master
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ChessMaster-Setup-v{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\ChessMaster\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Chess Master"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Chess Master"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Flags: nowait postinstall skipifsilent
