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
; O [Code] atualiza as chaves de catálogo (lojas, PDVs, logs, consultas Oracle, APIs) com o
; conteúdo do build e preserva as chaves específicas da máquina (PinPad, bec_loja/bec_pdv,
; modo_instalacao, tunnel, nível de log, abas). Ver EhChaveCatalogo no [Code].
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
// Procura a linha ATIVA (não comentada) de uma chave. Retorna True e a linha
// completa em Linha quando encontrada.
// ---------------------------------------------------------------------------
function LinhaAtivaDaChave(const Chave: string; const Linhas: TStringList; var Linha: string): Boolean;
var
  i: Integer;
  Trimada: string;
begin
  Result := False;
  if Chave = '' then Exit;
  for i := 0 to Linhas.Count - 1 do
  begin
    Trimada := Trim(Linhas[i]);
    if (Trimada = '') or (Copy(Trimada, 1, 1) = '#') then Continue;
    if CompareText(ExtrairChave(Linhas[i]), Chave) = 0 then
    begin
      Linha  := Linhas[i];
      Result := True;
      Exit;
    end;
  end;
end;

function ChaveTemLinhaAtiva(const Chave: string; const Linhas: TStringList): Boolean;
var
  Ignorada: string;
begin
  Result := LinhaAtivaDaChave(Chave, Linhas, Ignorada);
end;

function ComecaCom(const Texto, Prefixo: string): Boolean;
begin
  Result := (Length(Texto) >= Length(Prefixo)) and
            (CompareText(Copy(Texto, 1, Length(Prefixo)), Prefixo) = 0);
end;

function TerminaCom(const Texto, Sufixo: string): Boolean;
begin
  Result := (Length(Texto) >= Length(Sufixo)) and
            (CompareText(Copy(Texto, Length(Texto) - Length(Sufixo) + 1, Length(Sufixo)), Sufixo) = 0);
end;

// ---------------------------------------------------------------------------
// Chaves de CATÁLOGO: conteúdo distribuído com a aplicação (listas de lojas,
// PDVs, logs, consultas Oracle e APIs). São sempre atualizadas pelo instalador,
// para que ajustes feitos no config.properties do projeto cheguem às máquinas.
// As demais chaves (porta do PinPad, bec_loja/bec_pdv, modo_instalacao, tunnel,
// nível de log, abas visíveis) são específicas da máquina e ficam preservadas.
// ---------------------------------------------------------------------------
function EhChaveCatalogo(const Chave: string): Boolean;
begin
  Result :=
    (CompareText(Chave, 'stores')             = 0) or
    (CompareText(Chave, 'logs')               = 0) or
    (CompareText(Chave, 'logs_sp')            = 0) or
    (CompareText(Chave, 'ignorar_lojas')      = 0) or
    (CompareText(Chave, 'emails_destino')     = 0) or
    (CompareText(Chave, 'PARAMETROS_PDV')     = 0) or
    (CompareText(Chave, 'oracle_query_names') = 0) or
    (CompareText(Chave, 'api_order')          = 0) or
    TerminaCom(Chave, '_pdvs')       or
    ComecaCom(Chave, 'oracle_query.') or
    ComecaCom(Chave, 'api.');
end;

// ---------------------------------------------------------------------------
// Faz o merge do arquivo .new no arquivo existente. O resultado segue a
// estrutura (ordem e comentários) do arquivo do build:
//   - chaves de CATÁLOGO      → valor do build (sempre atualizado)
//   - demais chaves existentes→ valor preservado da máquina
//   - chaves novas            → adicionadas com o valor do build
//   - chaves locais ausentes no build → mantidas em bloco no final
//     (chaves de catálogo ausentes no build são removidas, para que exclusões
//      de consultas/APIs/logs feitas no projeto também cheguem à máquina)
// Grava um backup .bkp do arquivo anterior e apaga o .new ao terminar.
// ---------------------------------------------------------------------------
procedure MergeProperties(const ArquivoExistente, ArquivoNovo: string);
var
  Existente, Novo, Resultado: TStringList;
  i: Integer;
  Chave, Linha: string;
  PrimeiraLocal: Boolean;
begin
  Existente := TStringList.Create;
  Novo      := TStringList.Create;
  Resultado := TStringList.Create;
  try
    Existente.LoadFromFile(ArquivoExistente);
    Novo.LoadFromFile(ArquivoNovo);

    // 1) Base: arquivo do build, preservando os valores locais das chaves
    //    que não são de catálogo
    for i := 0 to Novo.Count - 1 do
    begin
      Chave := ExtrairChave(Novo[i]);
      if (Chave = '') or (Copy(Trim(Novo[i]), 1, 1) = '#') or EhChaveCatalogo(Chave) then
        Resultado.Add(Novo[i])
      else if LinhaAtivaDaChave(Chave, Existente, Linha) then
        Resultado.Add(Linha)
      else
        Resultado.Add(Novo[i]);
    end;

    // 2) Chaves configuradas na máquina que não existem no build
    PrimeiraLocal := True;
    for i := 0 to Existente.Count - 1 do
    begin
      if (Trim(Existente[i]) = '') or (Copy(Trim(Existente[i]), 1, 1) = '#') then Continue;
      Chave := ExtrairChave(Existente[i]);
      if (Chave = '') or EhChaveCatalogo(Chave) then Continue;
      if ChaveTemLinhaAtiva(Chave, Novo) then Continue;

      if PrimeiraLocal then
      begin
        Resultado.Add('');
        Resultado.Add('# === Chaves locais preservadas na atualizacao ===');
        PrimeiraLocal := False;
      end;
      Resultado.Add(Existente[i]);
    end;

    CopyFile(ArquivoExistente, ArquivoExistente + '.bkp', False);
    Resultado.SaveToFile(ArquivoExistente);
  finally
    Existente.Free;
    Novo.Free;
    Resultado.Free;
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
