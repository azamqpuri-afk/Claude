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
; Extra safety net on upgrades: let the Restart Manager close anything still holding our files, without prompting,
; and do not let it relaunch the killed program (our [Code] re-starts the tasks itself). The [Code] stop/kill below
; is the primary mechanism; this just covers a handle the kill missed.
CloseApplications=force
RestartApplications=no

[Files]
; The whole compiled program (Nuitka --standalone output: build\app_main.dist, exe renamed to SmartLayer.exe).
Source: "..\build\app_main.dist\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Scripts that the scheduled tasks run (they call SmartLayer.exe, not python).
Source: "..\installer\run_daily.cmd";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\run_live.cmd";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\run_dashboard.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\run_setup.cmd";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\run_morning.cmd";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\run_hidden.vbs";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\install_tasks.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data";   Permissions: users-modify
Name: "{app}\output"; Permissions: users-modify

[Icons]
Name: "{group}\Smart Layer dashboard"; Filename: "http://localhost:8080"
; Setup needs admin (scheduled tasks + firewall), so the shortcut goes through run_setup.cmd, which re-launches elevated.
Name: "{group}\Smart Layer setup (change answers)"; Filename: "{app}\run_setup.cmd"; WorkingDir: "{app}"
Name: "{group}\Smart Layer status";    Filename: "{app}\SmartLayer.exe"; Parameters: "status"; WorkingDir: "{app}"
Name: "{group}\Uninstall Smart Layer"; Filename: "{uninstallexe}"

[Run]
; Only non-interactive work runs while the progress bar is up, so the bar always finishes quickly:
; register the scheduled tasks (no questions, no input).
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_tasks.ps1"" -AppDir ""{app}"""; \
  Flags: runhidden; StatusMsg: "Scheduling the daily run, the live sync and the dashboard..."
; The setup wizard asks questions, so it runs AFTER the Finish page as a visible, foreground step
; (never blocking the installer). It writes config, tests the ERP and does the first sync itself.
Filename: "{app}\SmartLayer.exe"; Parameters: "setup"; WorkingDir: "{app}"; \
  Description: "Set up Smart Layer now - mill, ERP database, dashboard password, licence (opens a window)"; \
  Flags: postinstall runascurrentuser
Filename: "http://localhost:8080"; Description: "Open the dashboard (do this after setup)"; \
  Flags: postinstall shellexec unchecked skipifsilent

[UninstallRun]
; First stop the always-on dashboard and kill SmartLayer.exe so its files are not locked while we remove them.
Filename: "schtasks.exe"; Parameters: "/End /TN ""Smart Layer Dashboard"""; Flags: runhidden; RunOnceId: "StopDash"
Filename: "taskkill.exe"; Parameters: "/IM SmartLayer.exe /F /T";           Flags: runhidden; RunOnceId: "KillExe"
; Then remove the scheduled tasks and the firewall rule (keeps the folder, data and licence).
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""Smart Layer Daily"" /F";     Flags: runhidden; RunOnceId: "DelDaily"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""Smart Layer Live"" /F";      Flags: runhidden; RunOnceId: "DelLive"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""Smart Layer Dashboard"" /F"; Flags: runhidden; RunOnceId: "DelDash"
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-NetFirewallRule -DisplayName 'Smart Layer Dashboard' -ErrorAction SilentlyContinue | Remove-NetFirewallRule"""; Flags: runhidden; RunOnceId: "DelFw"

[Code]
// On an upgrade the old dashboard may still be running and holding SmartLayer.exe open, which blocks overwriting
// the files ("DeleteFile failed; code 5. Access is denied"). The task scheduler's own "End" is not enough: the
// dashboard's keep-alive loop runs in a cmd.exe whose wscript parent (run_hidden.vbs) has already exited, so Task
// Scheduler cannot see or kill it, and 5 s later it relaunches SmartLayer.exe. The Live task (every 5 min) can also
// start the exe mid-install. So before copying files we: drop a stop-flag the loop watches, End and DISABLE every
// task, kill any orphaned loop shell by its command line, kill the exe, and wait until it is really gone.

const
  FlagFile = 'upgrading.flag';

procedure RunHidden(const Exe, Params: String);
var
  ResultCode: Integer;
begin
  Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure StopSmartLayer();
var
  ResultCode, I: Integer;
  AppDir: String;
begin
  AppDir := ExpandConstant('{app}');
  // 1. Ask the dashboard keep-alive loop to stop itself (run_dashboard.cmd checks this flag at the top of its loop).
  if DirExists(AppDir) then
    SaveStringToFile(AppDir + '\' + FlagFile, 'upgrading', False);
  // 2. End and DISABLE every Smart Layer task so nothing relaunches the exe during the copy.
  RunHidden('schtasks.exe', '/End /TN "Smart Layer Dashboard"');
  RunHidden('schtasks.exe', '/End /TN "Smart Layer Live"');
  RunHidden('schtasks.exe', '/End /TN "Smart Layer Daily"');
  RunHidden('schtasks.exe', '/End /TN "Smart Layer Morning"');
  RunHidden('schtasks.exe', '/Change /TN "Smart Layer Dashboard" /DISABLE');
  RunHidden('schtasks.exe', '/Change /TN "Smart Layer Live" /DISABLE');
  RunHidden('schtasks.exe', '/Change /TN "Smart Layer Daily" /DISABLE');
  RunHidden('schtasks.exe', '/Change /TN "Smart Layer Morning" /DISABLE');
  // 3. Kill any surviving loop shell (cmd.exe / wscript.exe / cscript.exe) whose command line points at one of our
  //    run scripts - this catches the orphaned loop Task Scheduler could not.
  RunHidden('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ' +
    '($_.Name -eq ''cmd.exe'' -or $_.Name -eq ''wscript.exe'' -or $_.Name -eq ''cscript.exe'') -and ' +
    '($_.CommandLine -match ''run_dashboard\.cmd|run_live\.cmd|run_daily\.cmd|run_morning\.cmd|run_hidden\.vbs'') } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"');
  // 4. Kill the program itself (and any child it spawned).
  RunHidden('taskkill.exe', '/IM SmartLayer.exe /F /T');
  // 5. Wait until SmartLayer.exe is really gone (up to ~15 s) so the file copy never hits a locked exe.
  for I := 1 to 15 do
  begin
    Exec('cmd.exe', '/C tasklist /FI "IMAGENAME eq SmartLayer.exe" /NH | find /I "SmartLayer.exe" > nul',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then   // find returns 1 when the name is not listed = the process is gone
      Break;
    Sleep(1000);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopSmartLayer();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  AppDir, Flag, Cfg: String;
begin
  if CurStep = ssPostInstall then
  begin
    AppDir := ExpandConstant('{app}');
    // The new files are in place and install_tasks.ps1 has re-registered the tasks. Re-enable them explicitly
    // (belt-and-suspenders - a fresh Register already enables them) and remove the stop-flag so the loop runs again.
    RunHidden('schtasks.exe', '/Change /TN "Smart Layer Dashboard" /ENABLE');
    RunHidden('schtasks.exe', '/Change /TN "Smart Layer Live" /ENABLE');
    RunHidden('schtasks.exe', '/Change /TN "Smart Layer Daily" /ENABLE');
    RunHidden('schtasks.exe', '/Change /TN "Smart Layer Morning" /ENABLE');
    Flag := AppDir + '\' + FlagFile;
    if FileExists(Flag) then
      DeleteFile(Flag);
    // On an upgrade of an already-configured install, bring the dashboard and the live sync back up now (a fresh
    // install starts them from the setup wizard instead, once config.toml has been written).
    Cfg := AppDir + '\config.toml';
    if FileExists(Cfg) then
    begin
      Exec('schtasks.exe', '/Run /TN "Smart Layer Dashboard"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Exec('schtasks.exe', '/Run /TN "Smart Layer Live"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;
