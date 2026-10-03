; Inno Setup script for Project Smart Layer by Azam Puri.
; Builds SmartLayer-Setup-<version>.exe from the compiled program in build\SmartLayer.dist.
; Compile with:  ISCC.exe /DMyVersion=0.5.0 installer\SmartLayer.iss   (see .github/workflows/build-windows.yml)

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
AppName=Smart Layer
AppVersion={#MyVersion}
AppPublisher=Azam Puri
DefaultDirName={autopf}\Smart Layer
DefaultGroupName=Smart Layer
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SmartLayer-Setup-{#MyVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName=Smart Layer

[Files]
; The whole compiled program (Nuitka --standalone output: build\app_main.dist, exe renamed to SmartLayer.exe).
Source: "..\build\app_main.dist\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Scripts that the scheduled tasks run (they call SmartLayer.exe, not python).
Source: "..\installer\run_daily.cmd";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\run_dashboard.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\install_tasks.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data";   Permissions: users-modify
Name: "{app}\output"; Permissions: users-modify

[Icons]
Name: "{group}\Smart Layer dashboard"; Filename: "http://localhost:8080"
Name: "{group}\Smart Layer setup (change answers)"; Filename: "{app}\SmartLayer.exe"; Parameters: "setup"; WorkingDir: "{app}"
Name: "{group}\Smart Layer status";    Filename: "{app}\SmartLayer.exe"; Parameters: "status"; WorkingDir: "{app}"
Name: "{group}\Uninstall Smart Layer"; Filename: "{uninstallexe}"

[Run]
; After copying files: ask the setup questions, do the first sync, schedule the daily run and the dashboard.
Filename: "{app}\SmartLayer.exe"; Parameters: "setup"; WorkingDir: "{app}"; Flags: runascurrentuser; \
  StatusMsg: "Setup questions (mill, database, dashboard, licence)..."
Filename: "{app}\SmartLayer.exe"; Parameters: "sync"; WorkingDir: "{app}"; Flags: runascurrentuser runhidden; \
  StatusMsg: "First sync from the ERP (can take a few minutes)..."
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_tasks.ps1"" -AppDir ""{app}"""; \
  Flags: runhidden; StatusMsg: "Scheduling the daily run and the dashboard..."
Filename: "http://localhost:8080"; Description: "Open the dashboard now"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
; Remove the scheduled tasks and the firewall rule (keeps the folder, data and licence).
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""Smart Layer Daily"" /F";     Flags: runhidden; RunOnceId: "DelDaily"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""Smart Layer Dashboard"" /F"; Flags: runhidden; RunOnceId: "DelDash"
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-NetFirewallRule -DisplayName 'Smart Layer Dashboard' -ErrorAction SilentlyContinue | Remove-NetFirewallRule"""; Flags: runhidden; RunOnceId: "DelFw"
