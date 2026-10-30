#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif
#ifndef AppSource
  #define AppSource "..\..\dist\windows\" + AppVersion + "\ClaudeTUI"
#endif
[Setup]
AppId={{5FE82A69-02E3-45BC-9B2E-C98F6357CE7B}
AppName=ClaudeTUI
AppVersion={#AppVersion}
AppPublisher=ClaudeTUI
AppPublisherURL=https://github.com/victorccronemberger-blip/ClaudeTUI
AppSupportURL=https://github.com/victorccronemberger-blip/ClaudeTUI/issues
DefaultDirName={localappdata}\Programs\ClaudeTUI
DefaultGroupName=ClaudeTUI
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\dist
OutputBaseFilename=ClaudeTUI-Setup-{#AppVersion}-x64
SetupIconFile=..\..\src\free_claude_code\assets\app-icon.ico
UninstallDisplayIcon={app}\ClaudeTUI.exe
LicenseFile=..\..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"

[Icons]
Name: "{autodesktop}\ClaudeTUI"; Filename: "{app}\ClaudeTUI.exe"; WorkingDir: "{app}"
Name: "{group}\ClaudeTUI"; Filename: "{app}\ClaudeTUI.exe"; WorkingDir: "{app}"
Name: "{group}\Desinstalar ClaudeTUI"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\ClaudeTUI.exe"; Description: "Abrir ClaudeTUI"; Flags: nowait postinstall skipifsilent

; User configuration (~/.fcc), OAuth credentials and managed runtimes are deliberately
; outside {app}. Inno removes only its own installed files and shortcuts.
