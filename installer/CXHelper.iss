; Inno Setup script for CXHelper (GUI for Samueli924/chaoxing).
; Build it through gui\build_release.py --installer (gui\build_exe.bat), which
; freezes the app into dist\CXHelper\, tests and selftests it and then runs
; ISCC on this file - or compile it by hand after a build:
;   ISCC /DAppVersion=3.1.3 installer\CXHelper.iss
;
; What gets installed is dist\CXHelper\ exactly: CXHelper.exe (all project
; code, the web UI, the config template and the font table are inside it),
; the Python runtime and libraries in _internal\, and the licence files. The
; user's own data - config.ini, cookies.txt, cache.json, chaoxing*.log - is
; created next to the exe at run time and is never listed here, so upgrades
; keep it; the uninstaller asks before removing it.
;
; ISCC /DTestBuild compiles a throwaway variant under a different AppId and
; name, for trying the installer without touching a real installation.
;
; This file is UTF-8 with a BOM (it contains Chinese text).

#ifndef AppVersion
  #define AppVersion "3.1.3"
#endif
#ifndef SrcDir
  #define SrcDir AddBackslash(SourcePath) + ".."
#endif

#ifdef TestBuild
  #define AppName "CXHelperTest"
  #define AppGuid "{{7E1D5C0A-2B8F-4C3E-9A64-0D5F1B7C2E91}"
#else
  #define AppName "CXHelper"
  #define AppGuid "{{43B6A1A6-7047-439B-926D-4D1DB19175B8}"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Sensu
AppPublisherURL=https://suzume.cloud
AppSupportURL=https://suzume.cloud/projects/cxhelper
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 安装程序
VersionInfoProductName={#AppName}
; per-user location: no admin rights needed, and the app writes its data next
; to the exe, so the folder has to be writable by the user
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
; an existing installation (also the earlier one-click builds) is upgraded where it is
UsePreviousAppDir=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; a running copy is detected and closed (after asking) so files can be replaced
CloseApplications=yes
RestartApplications=no
; chaoxing is GPL-3.0: shown for information, the GPL needs no acceptance to run the program
InfoBeforeFile={#SrcDir}\LICENSE
OutputDir={#SrcDir}\installer\output
OutputBaseFilename={#AppName}-Setup
SetupIconFile={#SrcDir}\gui\assets\icon.ico
UninstallDisplayIcon={app}\CXHelper.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
chinesesimplified.DirNotWritable=无法写入这个文件夹：%n%1%n%n程序会把设置、登录状态和日志保存在安装目录里，所以安装目录必须可写。请换一个文件夹（例如默认位置）。
chinesesimplified.NeedWebView2=没有检测到 Microsoft Edge WebView2 运行时。%n%n{#AppName} 的界面依赖它显示。Windows 11 和较新的 Windows 10 一般自带；缺少时需要从微软官网免费安装。%n%n是否现在打开微软官方下载页面？（安装会继续进行）
chinesesimplified.NeedDotNet=没有检测到 .NET Framework 4.7.2 或更高版本。%n%n{#AppName} 需要它才能启动。Windows 10 (1803 起) 和 Windows 11 已自带；缺少时请从微软官网安装 .NET Framework 4.8。%n%n是否现在打开微软官方下载页面？（安装会继续进行）
chinesesimplified.RemoveUserData=是否同时删除 {#AppName} 的用户数据？%n%n包括：设置和账号 (config.ini，勾选过“记住密码”时含密码)、登录状态 (cookies.txt)、缓存 (cache.json) 和日志 (chaoxing.log)。%n%n选择“否”会保留这些文件，重新安装后可直接使用。

[InstallDelete]
; this release's runtime folder is replaced as a whole on upgrade (earlier
; builds also kept the web UI and other project files in here)
Type: filesandordirs; Name: "{app}\_internal"
; loose project file of an earlier build - it is inside the exe now
Type: files; Name: "{app}\config_template.ini"

[Files]
Source: "{#SrcDir}\dist\CXHelper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.ini,cookies.txt,cache.json,*.log,selftest.json"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\CXHelper.exe"; WorkingDir: "{app}"; IconFilename: "{app}\CXHelper.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\CXHelper.exe"; WorkingDir: "{app}"; IconFilename: "{app}\CXHelper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CXHelper.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\selftest.json"

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Url = 'https://developer.microsoft.com/microsoft-edge/webview2/';
  DotNetKey = 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full';
  DotNetUrl = 'https://dotnet.microsoft.com/download/dotnet-framework/net48';
  DotNet472Release = 461808;

function VersionSet(const Root: Integer; const Key: String): Boolean;
var
  V: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

function HasWebView2: Boolean;
begin
  Result := VersionSet(HKLM32, WebView2Key) or VersionSet(HKLM64, WebView2Key) or VersionSet(HKCU, WebView2Key);
end;

function HasDotNet472: Boolean;
var
  Release: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, DotNetKey, 'Release', Release) and (Release >= DotNet472Release);
end;

procedure OfferDownload(const Msg, Url: String);
var
  Code: Integer;
begin
  Log(Msg);
  if WizardSilent then
    Exit;
  if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDYES then
    ShellExec('open', Url, '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

function InitializeSetup: Boolean;
begin
  { runtime prerequisites (pywebview's WinForms + WebView2 backend): only
    checked and pointed to, never installed behind the user's back }
  if not HasDotNet472 then
    OfferDownload(CustomMessage('NeedDotNet'), DotNetUrl);
  if not HasWebView2 then
    OfferDownload(CustomMessage('NeedWebView2'), WebView2Url);
  Result := True;
end;

function DirWritable(const Dir: String): Boolean;
var
  Existed: Boolean;
  Probe: String;
begin
  Existed := DirExists(Dir);
  Result := ForceDirectories(Dir);
  if Result then
  begin
    Probe := AddBackslash(Dir) + '.cxhelper-write-test';
    Result := SaveStringToFile(Probe, 'x', False);
    if Result then
      DeleteFile(Probe);
    if not Existed then
      RemoveDir(Dir);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { interactive: stay on the folder page so another folder can be picked
    (a silent install would loop here forever - it is stopped in
    PrepareToInstall instead) }
  if (CurPageID = wpSelectDir) and not WizardSilent then
    if not DirWritable(WizardDirValue) then
    begin
      MsgBox(FmtMessage(CustomMessage('DirNotWritable'), [WizardDirValue]), mbError, MB_OK);
      Result := False;
    end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { a non-empty result ends Setup with that error (exit code 7 when silent) }
  Result := '';
  if not DirWritable(WizardDirValue) then
    Result := FmtMessage(CustomMessage('DirNotWritable'), [WizardDirValue]);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  App := ExpandConstant('{app}');
  if not (FileExists(App + '\config.ini') or FileExists(App + '\cookies.txt') or
          FileExists(App + '\cache.json') or FileExists(App + '\chaoxing.log')) then
    Exit;
  { silent uninstalls keep the data; only an explicit "yes" removes it }
  if UninstallSilent then
    Exit;
  if MsgBox(CustomMessage('RemoveUserData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DeleteFile(App + '\config.ini');
    DeleteFile(App + '\cookies.txt');
    DeleteFile(App + '\cache.json');
    { loguru rotates chaoxing.log into chaoxing.<date>.log }
    DelTree(App + '\chaoxing*.log', False, True, False);
    RemoveDir(App);
  end;
end;
