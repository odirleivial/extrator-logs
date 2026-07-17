#define AppName      "Backoffice Equipe QA"
; AppVersion pode ser sobrescrita via linha de comando: /DAppVersion=X.Y.Z
#ifndef AppVersion
  #define AppVersion "2.7.0"
#endif
#define AppPublisher "Equipe QA"
#define AppExeName   "ExtratordeLogs.exe"
#define SourceDir    "dist\ExtratordeLogs"

[Setup]
AppId={{B3A1C2D4-5E6F-7A8B-9C0D-E1F2A3B4C5D6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=ExtratordeLogs_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; Ícone do instalador (opcional — coloque icon.ico na pasta do projeto)
SetupIconFile=icon.ico
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon";    Description: "Criar ícone na Área de Trabalho"; GroupDescription: "Atalhos:"
Name: "startmenuicon";  Description: "Criar atalho no Menu Iniciar";    GroupDescription: "Atalhos:"

[Dirs]
; Permissão de escrita para todos os usuários (necessário para salvar config.properties)
Name: "{app}"; Permissions: users-full

[Files]
; Arquivos empacotados pelo PyInstaller
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; config.properties instalado ao lado do .exe (onlyifdoesntexist para não sobrescrever ao atualizar)
Source: "properties\config.properties";  DestDir: "{app}\properties"; Flags: onlyifdoesntexist
Source: "properties\secure.properties"; DestDir: "{app}\properties"; Flags: onlyifdoesntexist
Source: "properties\agent.properties";  DestDir: "{app}\properties"; Flags: ignoreversion
Source: "properties\secure.properties.example"; DestDir: "{app}\properties"; Flags: ignoreversion
Source: "icon.ico";                      DestDir: "{app}";            Flags: ignoreversion

[Icons]
; Menu Iniciar
Name: "{group}\{#AppName}";       Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: startmenuicon
Name: "{group}\Desinstalar";      Filename: "{uninstallexe}"

; Área de Trabalho
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
; Pergunta se deseja abrir a aplicação ao final da instalação
Filename: "{app}\{#AppExeName}"; Description: "Iniciar {#AppName} agora"; Flags: postinstall nowait skipifsilent; WorkingDir: "{app}"

[UninstallRun]
; Encerra o processo antes de desinstalar
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExeName}"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
// Verifica se o app já está rodando antes de instalar
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
