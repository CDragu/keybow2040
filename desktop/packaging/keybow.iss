[Setup]
AppId={{C38858A7-0E6F-4FDA-8BE4-D4E9EBA1AB82}
AppName=Keybow Codex
AppVersion={#Version}
AppPublisher=Keybow Codex project
AppPublisherURL=https://github.com/CDragu/keybow2040
DefaultDirName={localappdata}\Programs\Keybow Codex
DefaultGroupName=Keybow Codex
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=KeybowCodex-{#Version}-x64-Setup
UninstallDisplayIcon={app}\KeybowCodex.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: startup; Description: "Open Keybow Codex when I sign in to Windows"; Flags: unchecked

[InstallDelete]
Type: files; Name: "{userstartup}\Keybow Codex.lnk"

[Files]
Source: "{#AppFiles}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "THIRD-PARTY.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Keybow Codex"; Filename: "{app}\KeybowCodex.exe"; WorkingDir: "{app}"
Name: "{userstartup}\Keybow Codex"; Filename: "{app}\KeybowCodex.exe"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\KeybowCodex.exe"; Description: "Open Keybow Codex"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
