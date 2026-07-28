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
Name: "{app}";            Permissions: users-full
Name: "{app}\properties"; Permissions: users-full

[Files]
; Arquivos do PyInstaller — exclui a pasta properties (gerenciada separadamente abaixo)
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "properties\*"

; config.properties e agent.properties: instalados como .new para merge inteligente no pós-instalação
; O [Code] adiciona apenas chaves novas ao arquivo existente, preservando valores do usuário.
; Em instalação nova (arquivo ainda não existe), o .new é renomeado para o nome definitivo.
Source: "properties\config.properties"; DestDir: "{app}\properties"; DestName: "config.properties.new"; Flags: ignoreversion
Source: "properties\agent.properties";  DestDir: "{app}\properties"; DestName: "agent.properties.new";  Flags: ignoreversion

; secure.properties: nunca sobrescrito — somente instalado na primeira vez
Source: "properties\secure.properties";         DestDir: "{app}\properties"; Flags: onlyifdoesntexist
Source: "properties\secure.properties.example"; DestDir: "{app}\properties"; Flags: ignoreversion

Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";       Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: startmenuicon
Name: "{group}\Desinstalar";      Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Iniciar {#AppName} agora"; Flags: postinstall nowait skipifsilent; WorkingDir: "{app}"

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExeName}"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
// ---------------------------------------------------------------------------
// Extrai a chave de uma linha de properties (ex: "  key = value" → "key").
// Retorna string vazia se a linha for vazia, comentário ou não tiver '='.
// ---------------------------------------------------------------------------
function ExtrairChave(const Linha: string): string;
var
  Trimada: string;
  EqPos: Integer;
begin
  Result := '';
  Trimada := Trim(Linha);
  if (Trimada = '') then Exit;
  // Linha comentada com '#' — extraímos a chave do que está após '#'
  // para detectar chaves presentes mesmo que comentadas
  if Copy(Trimada, 1, 1) = '#' then
    Trimada := Trim(Copy(Trimada, 2, Length(Trimada)));
  EqPos := Pos('=', Trimada);
  if EqPos > 0 then
    Result := Trim(Copy(Trimada, 1, EqPos - 1));
end;

// ---------------------------------------------------------------------------
// Verifica se uma chave já existe (ativa ou comentada) no arquivo existente.
// ---------------------------------------------------------------------------
function ChaveExisteNoArquivo(const Chave: string; const Linhas: TStringList): Boolean;
var
  i: Integer;
  ChaveExistente: string;
begin
  Result := False;
  if Chave = '' then Exit;
  for i := 0 to Linhas.Count - 1 do
  begin
    ChaveExistente := ExtrairChave(Linhas[i]);
    if CompareText(ChaveExistente, Chave) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

// ---------------------------------------------------------------------------
// Faz o merge do arquivo .new no arquivo existente:
//   - chaves já presentes (ativas ou comentadas) → preservadas como estão
//   - chaves novas (ausentes no existente) → adicionadas ao final
// Apaga o arquivo .new ao terminar.
// ---------------------------------------------------------------------------
procedure MergeProperties(const ArquivoExistente, ArquivoNovo: string);
var
  Existente, Novo: TStringList;
  i: Integer;
  Chave: string;
  Alterado: Boolean;
  Bloco: string;
begin
  Existente := TStringList.Create;
  Novo      := TStringList.Create;
  try
    Existente.LoadFromFile(ArquivoExistente);
    Novo.LoadFromFile(ArquivoNovo);
    Alterado := False;
    Bloco    := '';   // acumula comentários de seção e chaves novas para adicionar juntos

    for i := 0 to Novo.Count - 1 do
    begin
      // Linha de comentário de seção (ex: "# === Lojas ===") — acumula para incluir junto
      if (Trim(Novo[i]) = '') or
         ((Copy(Trim(Novo[i]), 1, 1) = '#') and (Pos('=', Novo[i]) = 0)) then
      begin
        Bloco := Bloco + Novo[i] + #13#10;
        Continue;
      end;

      Chave := ExtrairChave(Novo[i]);
      if Chave = '' then
      begin
        Bloco := '';
        Continue;
      end;

      if not ChaveExisteNoArquivo(Chave, Existente) then
      begin
        // Chave nova: adiciona bloco de comentário acumulado + a linha
        if (Bloco <> '') and not Alterado then
          Existente.Add('');  // linha em branco antes do primeiro bloco novo
        if Bloco <> '' then
        begin
          // remove trailing CRLF do bloco antes de adicionar
          Existente.Add(TrimRight(Bloco));
          Bloco := '';
        end;
        Existente.Add(Novo[i]);
        Alterado := True;
      end else
        Bloco := '';  // chave já existe — descarta comentário acumulado
    end;

    if Alterado then
      Existente.SaveToFile(ArquivoExistente);
  finally
    Existente.Free;
    Novo.Free;
  end;
  DeleteFile(ArquivoNovo);
end;

// ---------------------------------------------------------------------------
// Pós-instalação: processa cada .new —
//   instalação nova  → renomeia para o nome definitivo
//   atualização      → merge inteligente + apaga .new
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  PropsDir: string;
  Arquivos: array[0..1] of string;
  i: Integer;
  Existente, Novo: string;
begin
  if CurStep <> ssPostInstall then Exit;

  PropsDir := ExpandConstant('{app}\properties\');
  Arquivos[0] := 'config.properties';
  Arquivos[1] := 'agent.properties';

  for i := 0 to 1 do
  begin
    Existente := PropsDir + Arquivos[i];
    Novo      := Existente + '.new';

    if not FileExists(Novo) then Continue;

    if FileExists(Existente) then
      MergeProperties(Existente, Novo)   // atualização: merge
    else
    begin
      RenameFile(Novo, Existente);       // instalação nova: usa diretamente
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;
