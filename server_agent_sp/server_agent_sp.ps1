# ===========================================================================
# Server Agent SP (agente_sp) - versao PowerShell
#
# Mesma funcao da versao .exe: monitora a caixa de e-mail e envia por e-mail
# os logs dos servidores solicitados. Roda sob o powershell.exe (assinado pela
# Microsoft), necessario em servidores onde o EDR bloqueia executaveis proprios.
#
# Responde somente a e-mails com assunto contendo [Solicitacao Log SP].
# ===========================================================================

$ErrorActionPreference = 'Continue'

$ASSUNTO_GATILHO = 'Log SP'   # casa com "[Solicitação Log SP]" sem depender de acentuacao

if ($PSScriptRoot) { $BASE_DIR = $PSScriptRoot } else { $BASE_DIR = (Get-Location).Path }

# Versao vem de version.ps1 (mesmo padrao do version.py do BEC e do extrator).
# O fallback cobre instalacoes antigas, anteriores ao arquivo de versionamento.
$AGENT_VERSION = '0.0.0'
$_arqVersao = Join-Path $BASE_DIR 'version.ps1'
if (Test-Path -LiteralPath $_arqVersao) {
    . $_arqVersao
    if ($AGENTE_VERSAO) { $AGENT_VERSION = $AGENTE_VERSAO }
}
$CONFIG_FILE = Join-Path $BASE_DIR 'agent.properties'
$CSV_LOG     = Join-Path $BASE_DIR 'historico_envio_logs.csv'
$LOG_DIR     = Join-Path $BASE_DIR 'log'
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR | Out-Null }

$LOG_FILE_BASE = 'server_agent_sp'
$script:LogData       = $null
$script:LogNivelMin   = 1     # INFO por padrao; ajustado no start por log.level (agent.properties)
$script:LogIdentidade = ''    # identidade sob a qual o agente roda (resolvida no start)
$script:CtxUsuario    = ''    # usuario que pediu a operacao (campo Usuario do e-mail)
$script:CtxPid        = ''    # PID da operacao em processamento

# Niveis: quanto maior, mais severo. Uma linha so e gravada quando seu nivel e
# maior ou igual ao configurado em log.level.
function ConvertTo-NivelNum {
    param([string]$Nivel)
    switch (($Nivel + '').ToUpper()) {
        'DEBUG'   { 0 }
        'INFO'    { 1 }
        'WARN'    { 2 }
        'WARNING' { 2 }
        'ERROR'   { 3 }
        default   { 1 }
    }
}

# ---------------------------------------------------------------------------
# Log diario: o dia corrente vai no arquivo fixo; na virada do dia o arquivo e
# renomeado para server_agent_sp_<data-anterior>.log
#
# Cada linha carrega [usuario] e, durante o processamento de uma solicitacao,
# tambem o PID: [usuario | PID]. Fora de uma solicitacao o usuario e a
# identidade sob a qual o agente roda (ex.: SYSTEM).
# ---------------------------------------------------------------------------
function Escrever-Log {
    param([string]$Mensagem, [string]$Nivel = 'INFO')
    if ((ConvertTo-NivelNum $Nivel) -lt $script:LogNivelMin) { return }

    $agora = Get-Date
    $hoje  = $agora.ToString('yyyy-MM-dd')
    $arquivoFixo = Join-Path $LOG_DIR "$LOG_FILE_BASE.log"

    if ($script:LogData -ne $hoje) {
        $dataAnterior = $script:LogData
        if (-not $dataAnterior -and (Test-Path $arquivoFixo)) {
            $dataAnterior = (Get-Item $arquivoFixo).LastWriteTime.ToString('yyyy-MM-dd')
        }
        if ($dataAnterior -and $dataAnterior -ne $hoje -and (Test-Path $arquivoFixo)) {
            $destino = Join-Path $LOG_DIR "${LOG_FILE_BASE}_$dataAnterior.log"
            try {
                if (Test-Path $destino) {
                    Get-Content $arquivoFixo | Add-Content -Path $destino -Encoding UTF8
                    Remove-Item $arquivoFixo -Force
                } else {
                    Move-Item $arquivoFixo $destino -Force
                }
            } catch {
                Write-Host "[LOG] Falha ao arquivar log anterior ($dataAnterior): $($_.Exception.Message)"
            }
        }
        $script:LogData = $hoje
    }

    $usuario = if ($script:CtxUsuario) { $script:CtxUsuario } else { $script:LogIdentidade }
    if ($script:CtxPid) { $ctx = "[$usuario | $($script:CtxPid)] " }
    elseif ($usuario)   { $ctx = "[$usuario] " }
    else                { $ctx = '' }

    $linha = "$($agora.ToString('yyyy-MM-dd HH:mm:ss')) $ctx[$Nivel] $Mensagem"
    Write-Host $linha
    # Sem permissao de escrita, segue apenas com saida no console em vez de abortar
    try { Add-Content -Path $arquivoFixo -Value $linha -Encoding UTF8 } catch { }
}

# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------
function Ler-Properties {
    param([string]$Arquivo)
    $props = @{}
    if (-not (Test-Path $Arquivo)) { return $props }
    foreach ($linha in (Get-Content $Arquivo -Encoding UTF8)) {
        $t = $linha.Trim()
        if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
            $i = $t.IndexOf('=')
            $props[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
        }
    }
    return $props
}

function Gravar-CsvLog {
    param([array]$Dados)
    try {
        if (-not (Test-Path $CSV_LOG)) {
            'PID,Destino,Logs,Data,ArquivoZip,DataHora,Status,Erro' |
                Out-File -FilePath $CSV_LOG -Encoding utf8
        }
        $campos = $Dados | ForEach-Object { '"' + ($_ -replace '"', '""') + '"' }
        ($campos -join ',') | Add-Content -Path $CSV_LOG -Encoding UTF8
    } catch {
        Escrever-Log "Falha ao gravar CSV: $($_.Exception.Message)" 'WARN'
    }
}

function Extrair-Campo {
    <#  Le o valor na MESMA linha do campo. Nao usar \s* aqui: ele atravessa a
        quebra de linha e, com o campo vazio, capturaria a linha seguinte.  #>
    param([string]$Corpo, [string]$Campo)
    $m = [regex]::Match($Corpo, "(?m)^[ \t]*$([regex]::Escape($Campo))[ \t]*:[ \t]*(.*)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return ''
}

# ---------------------------------------------------------------------------
# Decodificacao MIME (assunto e corpo)
# ---------------------------------------------------------------------------
function Decode-QuotedPrintable {
    param([string]$Texto, $Encoding = [System.Text.Encoding]::UTF8)
    $Texto = $Texto -replace "=\r?\n", ''
    $bytes = New-Object System.Collections.Generic.List[byte]
    $i = 0
    while ($i -lt $Texto.Length) {
        if ($Texto[$i] -eq '=' -and ($i + 2) -lt $Texto.Length) {
            $hex = $Texto.Substring($i + 1, 2)
            $val = 0
            if ([int]::TryParse($hex, [System.Globalization.NumberStyles]::HexNumber,
                                [System.Globalization.CultureInfo]::InvariantCulture, [ref]$val)) {
                $bytes.Add([byte]$val); $i += 3; continue
            }
        }
        $bytes.AddRange([System.Text.Encoding]::UTF8.GetBytes([string]$Texto[$i]))
        $i++
    }
    return $Encoding.GetString($bytes.ToArray())
}

function Decode-EncodedWords {
    param([string]$Texto)
    if (-not $Texto) { return '' }
    $regex = [regex]'=\?([^?]+)\?([BbQq])\?([^?]*)\?='
    $resultado = ''
    $pos = 0
    foreach ($m in $regex.Matches($Texto)) {
        $resultado += $Texto.Substring($pos, $m.Index - $pos)
        try { $enc = [System.Text.Encoding]::GetEncoding($m.Groups[1].Value) }
        catch { $enc = [System.Text.Encoding]::UTF8 }
        $dados = $m.Groups[3].Value
        try {
            if ($m.Groups[2].Value.ToUpper() -eq 'B') {
                $resultado += $enc.GetString([Convert]::FromBase64String($dados))
            } else {
                $resultado += (Decode-QuotedPrintable ($dados -replace '_', ' ') $enc)
            }
        } catch { $resultado += $dados }
        $pos = $m.Index + $m.Length
    }
    $resultado += $Texto.Substring($pos)
    return $resultado
}

function Get-CorpoTexto {
    <#  Extrai o texto/plain de uma mensagem RFC822 bruta, tratando multipart,
        base64 e quoted-printable.  #>
    param([string]$Raw)

    $sep = 4
    $idx = $Raw.IndexOf("`r`n`r`n")
    if ($idx -lt 0) { $idx = $Raw.IndexOf("`n`n"); $sep = 2 }
    if ($idx -lt 0) { return $Raw }

    $cabecalho = $Raw.Substring(0, $idx)
    $corpo     = $Raw.Substring($idx + $sep)

    $ctype = ''
    if ($cabecalho -match '(?im)^Content-Type:\s*(.+)$') { $ctype = $matches[1] }
    $cte = ''
    if ($cabecalho -match '(?im)^Content-Transfer-Encoding:\s*(\S+)') { $cte = $matches[1].ToLower() }

    if ($ctype -match '(?i)multipart' -and $ctype -match '(?i)boundary="?([^";\s]+)"?') {
        $boundary = $matches[1]
        foreach ($parte in ($corpo -split [regex]::Escape("--$boundary"))) {
            if ($parte -match '(?i)text/plain') {
                return (Get-CorpoTexto ($parte -replace '^[\r\n]+', ''))
            }
        }
        return $corpo
    }

    switch ($cte) {
        'base64' {
            try {
                return [System.Text.Encoding]::UTF8.GetString(
                    [Convert]::FromBase64String(($corpo -replace '\s', '')))
            } catch { return $corpo }
        }
        'quoted-printable' { return (Decode-QuotedPrintable $corpo) }
        default            { return $corpo }
    }
}

# ---------------------------------------------------------------------------
# Formato de nomenclatura dos logs — ver Resolver-FormatoLog mais abaixo, que
# e usado tanto pelos logs do dia quanto pelos historicos.
# ---------------------------------------------------------------------------
function Resolver-TokensData {
    param([string]$Tokens, [datetime]$Data)
    $resultado = ''
    $i = 0
    while ($i -lt $Tokens.Length) {
        $t4 = if (($i + 4) -le $Tokens.Length) { $Tokens.Substring($i, 4).ToLower() } else { '' }
        $t2 = if (($i + 2) -le $Tokens.Length) { $Tokens.Substring($i, 2).ToLower() } else { '' }
        if     ($t4 -eq 'yyyy') { $resultado += $Data.ToString('yyyy'); $i += 4 }
        elseif ($t2 -eq 'yy')   { $resultado += $Data.ToString('yy');   $i += 2 }
        elseif ($t2 -eq 'mm')   { $resultado += $Data.ToString('MM');   $i += 2 }
        elseif ($t2 -eq 'dd')   { $resultado += $Data.ToString('dd');   $i += 2 }
        elseif ($t2 -eq 'hh')   { $resultado += $Data.ToString('HH');   $i += 2 }
        elseif ($t2 -eq 'ss')   { $resultado += $Data.ToString('ss');   $i += 2 }
        else                    { $resultado += $Tokens[$i];            $i += 1 }
    }
    return $resultado
}

function Get-ShareRaiz {
    <# Extrai \\servidor\share de um caminho UNC completo. Vazio se nao for UNC. #>
    param([string]$Caminho)
    $m = [regex]::Match($Caminho, '^(\\\\[^\\]+\\[^\\]+)')
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}

function Autenticar-Share {
    <#  Estabelece credencial para a share antes de ler os arquivos.
        Necessario porque o servico roda como LocalSystem, que se apresenta na
        rede como a conta da maquina e nao tem direito nas shares d$.
        Sem windows_user configurado, usa as credenciais da propria sessao.  #>
    param([string]$Share, [string]$Usuario, [string]$Senha)

    if (-not $Share) { return }
    if (-not $Usuario -or -not $Senha) {
        Escrever-Log "windows_user/windows_senha nao configurados - acessando $Share com as credenciais da sessao" 'WARN'
        return
    }

    try {
        if (Get-SmbMapping -RemotePath $Share -ErrorAction SilentlyContinue) {
            Escrever-Log "Conexao ja estabelecida em $Share"
            return
        }
    } catch { }

    try {
        New-SmbMapping -RemotePath $Share -UserName $Usuario -Password $Senha `
                       -Persistent $false -ErrorAction Stop | Out-Null
        Escrever-Log "Autenticado em $Share (usuario=$Usuario)"
        return
    } catch {
        $erroSmb = $_.Exception.Message
    }

    # Fallback para servidores sem os cmdlets SMB
    try {
        $net = Join-Path $env:SystemRoot 'System32\net.exe'
        & $net use $Share /user:$Usuario $Senha 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Escrever-Log "Autenticado em $Share via net use (usuario=$Usuario)"
            return
        }
        Escrever-Log "Falha ao autenticar em ${Share}: $erroSmb (net use retornou $LASTEXITCODE)" 'ERROR'
    } catch {
        Escrever-Log "Falha ao autenticar em ${Share}: $($_.Exception.Message)" 'ERROR'
    }
}

function Arquivo-Existe {
    <#  Test-Path lanca excecao quando a share nega acesso; aqui isso vira um
        "nao encontrado" com o motivo registrado, para que a falha em um
        servidor nao derrube a coleta dos demais logs.  #>
    param([string]$Caminho, [ref]$Motivo)
    try {
        if (Test-Path -LiteralPath $Caminho -ErrorAction Stop) { return $true }
        if ($Motivo) { $Motivo.Value = 'arquivo nao encontrado' }
        return $false
    } catch {
        if ($Motivo) { $Motivo.Value = $_.Exception.Message }
        return $false
    }
}

function Parsear-Data {
    param([string]$Texto)
    $formatos = @('dd/MM/yyyy', 'dd-MM-yyyy', 'yyyy-MM-dd', 'dd/MM/yy')
    foreach ($f in $formatos) {
        $dt = [datetime]::MinValue
        if ([datetime]::TryParseExact($Texto.Trim(), $f,
                [System.Globalization.CultureInfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$dt)) {
            return $dt
        }
    }
    return $null
}

function Definir-DataReferencia {
    <#  Decide, a partir do campo "Data:" do e-mail, se a extracao e do dia
        atual ou de um dia anterior (historico). Data ausente, invalida ou
        futura cai no dia atual.  #>
    param([string]$Texto)

    $hoje    = (Get-Date).Date
    $dataRef = $null
    if ($Texto) {
        $dataRef = Parsear-Data $Texto
        if (-not $dataRef) {
            Escrever-Log "Campo Data '$Texto' invalido - usando a data atual" 'WARN'
        } elseif ($dataRef.Date -gt $hoje) {
            Escrever-Log "Data solicitada ($($dataRef.ToString('dd/MM/yyyy'))) e futura - usando os logs do dia atual" 'WARN'
            $dataRef = $null
        }
    }

    $historico = ($dataRef -ne $null) -and ($dataRef.Date -lt $hoje)
    if (-not $dataRef) { $dataRef = Get-Date }
    return [pscustomobject]@{ Data = $dataRef; Historico = $historico }
}

# ---------------------------------------------------------------------------
# Logs historicos (dias anteriores)
#
# Configuracao no agent.properties:
#   historico.<log>.caminho = pasta base onde o arquivo/pasta do dia fica
#   historico.<log>.formato = nomenclatura do arquivo ou da pasta
#   historico.<log>.tipo    = arquivo | pasta
#
# Tokens do formato: [..] e {..} -> data quando o conteudo so tem marcadores de
# data, senao texto fixo; (LOJA)/(PDV) -> variaveis da solicitacao; (xxx) ->
# curinga, casa com qualquer texto. Texto fora dos delimitadores e literal.
# ---------------------------------------------------------------------------
$RE_TOKEN_FORMATO = [regex]'\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\}'
$RE_TOKEN_SO_DATA = [regex]'^[ymdhs\-_/.: ]+$'

function Token-EhData {
    <#  Classifica o token pelo CONTEUDO e nao pelo delimitador, para tolerar
        [..] e {..} trocados na configuracao: '[yyyymmdd]' e '{yyyymmdd}' sao
        ambos data, '{lgComandosSQL_}' e '[lgComandosSQL_]' sao ambos fixos.  #>
    param([string]$Conteudo)
    $c = $Conteudo.ToLower()
    return ($c -ne '' -and $RE_TOKEN_SO_DATA.IsMatch($c) -and $c -match '[ymd]')
}

function Resolver-FormatoLog {
    <#  Resolve o formato para o nome (ou padrao com curinga) do dia informado.
        Vale para os logs do dia e para os historicos.
        Ex.: '{lgComandosSQL_}[yyyymmdd].txt'      -> 'lgComandosSQL_20260803.txt'
             '{linx-webservices_}[yyyy-mm-dd](xxx).zip' -> 'linx-webservices_2026-08-03*.zip'  #>
    param([string]$Formato, [datetime]$Data = (Get-Date), [string]$Loja = '', [string]$Pdv = '')

    $resultado = ''
    $pos = 0
    foreach ($m in $RE_TOKEN_FORMATO.Matches($Formato)) {
        $resultado += $Formato.Substring($pos, $m.Index - $pos)
        $conteudo = ''
        foreach ($g in 1, 2, 3) {
            if ($m.Groups[$g].Success) { $conteudo = $m.Groups[$g].Value; break }
        }
        $chave = $conteudo.Trim().ToUpper()
        if     ($chave -eq 'LOJA') { $resultado += $Loja }
        elseif ($chave -eq 'PDV')  { $resultado += $Pdv }
        elseif ($chave -eq 'XXX')  { $resultado += '*' }
        elseif (Token-EhData $conteudo) { $resultado += (Resolver-TokensData $conteudo $Data) }
        else   { $resultado += $conteudo }
        $pos = $m.Index + $m.Length
    }
    $resultado += $Formato.Substring($pos)
    return $resultado
}

function Localizar-Itens {
    <#  Devolve os arquivos (ou pastas) que casam com o padrao dentro da base.
        Com curinga pode retornar mais de um item — e o caso dos logs que o
        servidor rotaciona varias vezes no mesmo dia (.0, .1, .2).  #>
    param([string]$Base, [string]$Padrao, [string]$Tipo, [ref]$Motivo)

    $ehPasta = ($Tipo -eq 'pasta')
    try {
        if ($Padrao.Contains('*')) {
            # -Filter faz a filtragem no servidor (rapido em pasta grande) e o
            # -like depois corrige as folgas do matching estilo 8.3 do -Filter.
            $itens = @(Get-ChildItem -LiteralPath $Base -Filter $Padrao -Force -ErrorAction Stop |
                       Where-Object { $_.PSIsContainer -eq $ehPasta -and $_.Name -like $Padrao })
        } else {
            $itens = @()
            $completo = Join-Path $Base $Padrao
            if (Test-Path -LiteralPath $completo -ErrorAction Stop) {
                $item = Get-Item -LiteralPath $completo -Force -ErrorAction Stop
                $itens = @($item | Where-Object { $_.PSIsContainer -eq $ehPasta })
            }
        }
    } catch {
        $Motivo.Value = $_.Exception.Message
        return @()
    }

    if ($itens.Count -eq 0) {
        $Motivo.Value = if ($ehPasta) { 'pasta nao encontrada' } else { 'arquivo nao encontrado' }
    }
    return $itens
}

function Descrever-Itens {
    <# Texto do que foi coletado, para a coluna "Arquivo" do e-mail. #>
    param($Itens, [string]$Base)
    if ($Itens.Count -eq 1) { return $Itens[0].FullName }
    $nomes = ($Itens | ForEach-Object { $_.Name }) -join ', '
    return "$Base\ ($($Itens.Count) arquivos: $nomes)"
}

function Copiar-Pastas {
    <#  Copia as pastas para a area de montagem do zip, sob uma subpasta com o
        nome do log — do contrario o anexo traria so pastas chamadas com a data,
        sem indicar de qual log vieram. A mesma pasta de origem nao e copiada
        duas vezes: quando outro log ja a incluiu, apenas registra.  #>
    param($Itens, [string]$Log, [string]$Staging, [hashtable]$Incluidas)

    $descricoes = @()
    foreach ($pasta in $Itens) {
        $chave = $pasta.FullName.TrimEnd('\').ToLower()
        if ($Incluidas.ContainsKey($chave)) {
            Escrever-Log "Pasta ja incluida por outro log - nao sera compactada de novo: $($pasta.FullName)"
            $descricoes += "$($pasta.FullName) (ja no anexo em $($Incluidas[$chave]))"
            continue
        }

        $raizLog = Join-Path $Staging $Log
        New-Item -ItemType Directory -Path $raizLog -Force | Out-Null

        # Desempate defensivo: duas pastas de origem com o mesmo nome dentro do
        # mesmo log se sobrescreveriam sem isto.
        $nomeDest = $pasta.Name
        $n = 2
        while (Test-Path -LiteralPath (Join-Path $raizLog $nomeDest)) {
            $nomeDest = "$($pasta.Name)_$n"
            $n++
        }

        $destino = Join-Path $raizLog $nomeDest
        Copy-Item -LiteralPath $pasta.FullName -Destination $destino -Recurse -Force -ErrorAction Stop
        $qtd = @(Get-ChildItem -LiteralPath $destino -Recurse -File -Force -ErrorAction SilentlyContinue).Count
        Escrever-Log "Pasta incluida no anexo: $($pasta.FullName) -> $Log/$nomeDest/ ($qtd arquivo(s))"
        $Incluidas[$chave] = "$Log/$nomeDest/"
        $descricoes += $pasta.FullName
    }
    return ($descricoes -join ' | ')
}

function Resolver-LogHistorico {
    <#  Le a configuracao historico.<log>.* e localiza os itens do dia pedido.
        Retorna $null quando o log nao tem configuracao de historico.  #>
    param([string]$Log, $Props, [datetime]$Data)

    $base    = $Props["historico.$Log.caminho"]
    $formato = $Props["historico.$Log.formato"]
    if (-not $base -or -not $formato) { return $null }

    $tipo = $Props["historico.$Log.tipo"]
    if (-not $tipo) { $tipo = 'arquivo' }

    return [pscustomobject]@{
        # O caminho tambem passa pelo resolvedor: ha logs cuja data esta na
        # PASTA e nao no nome do arquivo (ex.: ...\logsTesouraria\[yyyymmdd]).
        Base   = (Resolver-FormatoLog $base $Data).TrimEnd('\', '/')
        Tipo   = $tipo.Trim().ToLower()
        Padrao = Resolver-FormatoLog $formato $Data
    }
}

# ---------------------------------------------------------------------------
# Cliente IMAP (TLS via SslStream, leitura em bytes para respeitar literais)
# ---------------------------------------------------------------------------
function Read-LinhaImap {
    param($Stream)
    $bytes = New-Object System.Collections.Generic.List[byte]
    while ($true) {
        $b = $Stream.ReadByte()
        if ($b -lt 0 -or $b -eq 10) { break }
        if ($b -ne 13) { $bytes.Add([byte]$b) }
    }
    return [System.Text.Encoding]::UTF8.GetString($bytes.ToArray())
}

function Read-BytesImap {
    param($Stream, [int]$Total)
    $buf = New-Object byte[] $Total
    $lidos = 0
    while ($lidos -lt $Total) {
        $r = $Stream.Read($buf, $lidos, $Total - $lidos)
        if ($r -le 0) { break }
        $lidos += $r
    }
    return [System.Text.Encoding]::UTF8.GetString($buf, 0, $lidos)
}

function Send-ComandoImap {
    param($Stream, [string]$Tag, [string]$Comando)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("$Tag $Comando`r`n")
    $Stream.Write($bytes, 0, $bytes.Length)
    $Stream.Flush()
}

function Read-RespostaImap {
    <# Le ate a linha de conclusao da tag. Devolve as linhas intermediarias. #>
    param($Stream, [string]$Tag)
    $linhas = @()
    while ($true) {
        $linha = Read-LinhaImap $Stream
        if ($linha -match "^$Tag\s+(OK|NO|BAD)") {
            return [pscustomobject]@{ Linhas = $linhas; Final = $linha; Ok = ($matches[1] -eq 'OK') }
        }
        if ($linha -eq '' -and $Stream.CanRead -eq $false) { break }
        $linhas += $linha
    }
    return [pscustomobject]@{ Linhas = $linhas; Final = ''; Ok = $false }
}

# ---------------------------------------------------------------------------
# Envio de e-mail
# ---------------------------------------------------------------------------
function Enviar-Email {
    param(
        [string]$Usuario, [string]$Senha, [string]$Destino,
        [string]$Assunto, [string]$CorpoHtml, [string]$Anexo = ''
    )
    $mail = New-Object System.Net.Mail.MailMessage
    $smtp = $null
    $att  = $null
    try {
        $mail.From = New-Object System.Net.Mail.MailAddress($Usuario)
        foreach ($d in ($Destino -split '[;,]')) {
            $d = $d.Trim()
            if ($d) { $mail.To.Add($d) }
        }
        $mail.Subject         = $Assunto
        $mail.SubjectEncoding = [System.Text.Encoding]::UTF8
        $mail.Body            = $CorpoHtml
        $mail.BodyEncoding    = [System.Text.Encoding]::UTF8
        $mail.IsBodyHtml      = $true

        if ($Anexo -and (Test-Path $Anexo)) {
            $att = New-Object System.Net.Mail.Attachment($Anexo)
            $mail.Attachments.Add($att)
        }

        $smtp = New-Object System.Net.Mail.SmtpClient('smtp.gmail.com', 587)
        $smtp.EnableSsl   = $true
        $smtp.Credentials = New-Object System.Net.NetworkCredential($Usuario, $Senha)
        $smtp.Send($mail)
    } finally {
        if ($att)  { $att.Dispose() }
        if ($mail) { $mail.Dispose() }
        if ($smtp) { $smtp.Dispose() }
    }
}

# ---------------------------------------------------------------------------
# Montagem do HTML do e-mail de resposta
# ---------------------------------------------------------------------------
function Montar-HtmlResultado {
    param($Pid_, $DataRefFmt, $AgoraFmt, $NomeZip, $Arquivos, $Versao, [bool]$Historico = $false)

    $nOk   = @($Arquivos | Where-Object { $_.status -eq 'ok' }).Count
    $nErro = $Arquivos.Count - $nOk

    # O card da data fica em ambar quando os logs sao de um dia anterior
    if ($Historico) {
        $bgData = '#fef3c7'; $corData = '#92400e'; $rotuloData = 'Data (hist&oacute;rico)'
    } else {
        $bgData = '#f8fafc'; $corData = '#1e3a5f'; $rotuloData = 'Data dos Logs'
    }

    $linhas = ''
    foreach ($a in $Arquivos) {
        if ($a.status -eq 'ok') {
            $bg = ''
            $badge = "<span style='background:#dcfce7;color:#1a7f4b;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10004; Inclu&iacute;do</span>"
        } elseif ($a.status -eq 'nao_encontrado') {
            $bg = 'background:#fff5f5;'
            $badge = "<span style='background:#fee2e2;color:#b91c1c;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10006; N&atilde;o encontrado</span>"
        } else {
            $bg = 'background:#fff5f5;'
            $badge = "<span style='background:#fee2e2;color:#b91c1c;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10006; Sem configura&ccedil;&atilde;o</span>"
        }
        $linhas += @"

            <tr style='$bg'>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:12px;color:#1e293b'>$($a.nome)</td>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#6b7280;word-break:break-all'>$($a.caminho)</td>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap'>$badge</td>
            </tr>
"@
    }

    return @"
<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='640' cellpadding='0' cellspacing='0' style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Solicita&ccedil;&atilde;o de Logs SP</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f;font-family:monospace'>$Pid_</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:$bgData;border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>$rotuloData</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:$corData'>$DataRefFmt</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Gerado em</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f'>$AgoraFmt</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:16px 28px'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='background:#dcfce7;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#1a7f4b'>$nOk</p>
          <p style='margin:2px 0 0;font-size:11px;color:#1a7f4b;font-weight:bold'>INCLU&Iacute;DO(S)</p>
        </td>
        <td width='12'></td>
        <td style='background:#fee2e2;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b91c1c'>$nErro</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b91c1c;font-weight:bold'>N&Atilde;O ENCONTRADO(S)</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:0 28px 8px'>
    <table width='100%' cellpadding='0' cellspacing='0' style='border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;font-size:13px'>
      <thead>
        <tr style='background:#f8fafc'>
          <th style='padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Log</th>
          <th style='padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Arquivo</th>
          <th style='padding:10px 14px;text-align:center;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Status</th>
        </tr>
      </thead>
      <tbody>$linhas
      </tbody>
    </table>
  </td></tr>

  <tr><td style='padding:8px 28px 24px'>
    <div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:12px 16px'>
      <p style='margin:0;font-size:12px;color:#0369a1'><strong>Anexo:</strong> $NomeZip</p>
    </div>
  </td></tr>

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em $AgoraFmt &nbsp;|&nbsp; Server Agent SP v$Versao</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>
"@
}

function Montar-HtmlErro {
    param($Pid_, $DataRefFmt, $AgoraFmt, $Erro, $Versao)
    return @"
<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='560' cellpadding='0' cellspacing='0' style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>
  <tr><td style='background:#7f1d1d;padding:24px 28px'>
    <p style='margin:0;color:#fecaca;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Falha na Solicita&ccedil;&atilde;o de Logs SP</h1>
  </td></tr>
  <tr><td style='padding:20px 28px'>
    <p style='margin:0 0 8px;font-size:13px;color:#374151'><strong>PID:</strong> <span style='font-family:monospace'>$Pid_</span></p>
    <p style='margin:0 0 8px;font-size:13px;color:#374151'><strong>Data dos logs:</strong> $DataRefFmt</p>
    <p style='margin:0 0 12px;font-size:13px;color:#b91c1c'><strong>Erro:</strong> $Erro</p>
  </td></tr>
  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em $AgoraFmt &nbsp;|&nbsp; Server Agent SP v$Versao</p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>
"@
}

# ---------------------------------------------------------------------------
# Processamento de uma solicitacao
# ---------------------------------------------------------------------------
function Processar-Solicitacao {
    param([string]$Corpo, $Props, [string]$Usuario, [string]$Senha)

    $pidSolic = Extrair-Campo $Corpo 'PID'
    $destino  = Extrair-Campo $Corpo 'Destino'
    $logs     = Extrair-Campo $Corpo 'Logs'
    $dataTxt  = Extrair-Campo $Corpo 'Data'

    $ref = Definir-DataReferencia $dataTxt
    $dataRef   = $ref.Data
    $historico = $ref.Historico

    Escrever-Log ("[SolicitacaoLogSP] PID=$pidSolic | Logs=$logs | " +
                  "Data=$($dataRef.ToString('dd/MM/yyyy')) ($(if ($historico) { 'historico' } else { 'dia atual' }))")

    $agora       = Get-Date
    $agoraFmt    = $agora.ToString('dd/MM/yyyy HH:mm:ss')
    $dataRefFmt  = $dataRef.ToString('dd/MM/yyyy')
    if ($historico) {
        $nomeZip = "LOG-SP-HIST$($dataRef.ToString('yyyyMMdd'))-$($agora.ToString('ddMMyyyyHHmmss')).zip"
    } else {
        $nomeZip = "LOG-SP-$($agora.ToString('ddMMyyyyHHmmss')).zip"
    }
    $zipPath     = Join-Path $BASE_DIR $nomeZip
    $statusEnvio = 'Sucesso'
    $erroMsg     = ''
    $arquivos    = @()
    $staging     = Join-Path ([System.IO.Path]::GetTempPath()) "agentesp_$($agora.ToString('ddMMyyyyHHmmss'))"

    try {
        $listaLogs = @($logs -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($listaLogs.Count -eq 0) { throw 'Campo Logs vazio no corpo do e-mail.' }

        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        $algumArquivo = $false

        $winUser  = $Props['windows_user']
        $winSenha = $Props['windows_senha']
        $sharesFeitas = @{}
        # Pastas ja copiadas, por caminho de origem: quando varios logs apontam
        # para a mesma pasta ela entra no anexo uma unica vez.
        $pastasIncluidas = @{}

        foreach ($item in $listaLogs) {
            if ($historico) {
                $cfg = Resolver-LogHistorico $item $Props $dataRef
                if (-not $cfg) {
                    Escrever-Log "Log '$item' sem configuracao de historico (historico.$item.caminho/.formato)." 'WARN'
                    $arquivos += [pscustomobject]@{ nome = $item; caminho = '-'; status = 'sem_config' }
                    continue
                }
                $caminhoBase = $cfg.Base
                $padrao      = $cfg.Padrao
                $tipo        = $cfg.Tipo
            } else {
                $caminhoBase = $Props["log.$item.caminho"]
                $formato     = $Props["log.$item.formato"]
                if (-not $caminhoBase -or -not $formato) {
                    Escrever-Log "Log '$item' sem configuracao (log.$item.caminho/.formato)." 'WARN'
                    $arquivos += [pscustomobject]@{ nome = $item; caminho = '-'; status = 'sem_config' }
                    continue
                }
                $tipo = $Props["log.$item.tipo"]
                if (-not $tipo) { $tipo = 'arquivo' }
                $tipo        = $tipo.Trim().ToLower()
                $caminhoBase = (Resolver-FormatoLog $caminhoBase $dataRef).TrimEnd('\', '/')
                $padrao      = Resolver-FormatoLog $formato $dataRef
            }

            # Autentica uma vez por share antes do primeiro acesso
            $share = Get-ShareRaiz $caminhoBase
            if ($share -and -not $sharesFeitas.ContainsKey($share.ToLower())) {
                Autenticar-Share $share $winUser $winSenha
                $sharesFeitas[$share.ToLower()] = $true
            }

            $prefixo = if ($historico) { '[Historico] ' } else { '' }
            Escrever-Log "$prefixo$(if ($tipo -eq 'pasta') { 'Pasta' } else { 'Arquivo' }) $item -> $caminhoBase\$padrao"

            $motivo = ''
            $itens = Localizar-Itens $caminhoBase $padrao $tipo ([ref]$motivo)
            if ($itens.Count -eq 0) {
                Escrever-Log "$(if ($tipo -eq 'pasta') { 'Pasta' } else { 'Arquivo' }) indisponivel ($motivo): $caminhoBase\$padrao" 'WARN'
                $arquivos += [pscustomobject]@{ nome = $item; caminho = "$caminhoBase\$padrao"; status = 'nao_encontrado' }
                continue
            }

            try {
                if ($tipo -eq 'pasta') {
                    $descricao = Copiar-Pastas $itens $item $staging $pastasIncluidas
                } else {
                    # Subpasta por log evita colisao entre logs cujo arquivo tem
                    # o mesmo nome (csi_ws-safe e csi_safe-retaguarda, por ex.)
                    $destPasta = Join-Path $staging $item
                    New-Item -ItemType Directory -Path $destPasta -Force | Out-Null
                    foreach ($f in $itens) {
                        Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $destPasta $f.Name) -Force -ErrorAction Stop
                    }
                    $descricao = Descrever-Itens $itens $caminhoBase
                }
                $arquivos += [pscustomobject]@{ nome = $item; caminho = $descricao; status = 'ok' }
                $algumArquivo = $true
            } catch {
                Escrever-Log "Falha ao copiar $item ($caminhoBase\$padrao): $($_.Exception.Message)" 'WARN'
                $arquivos += [pscustomobject]@{ nome = $item; caminho = "$caminhoBase\$padrao"; status = 'nao_encontrado' }
            }
        }

        if (-not $algumArquivo) { throw 'Nenhum arquivo valido para compactar.' }

        Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zipPath -Force

        $html = Montar-HtmlResultado $pidSolic $dataRefFmt $agoraFmt $nomeZip $arquivos $AGENT_VERSION $historico
        Enviar-Email $Usuario $Senha $destino "[Logs SP][$pidSolic]" $html $zipPath
        Escrever-Log "Email enviado para $destino | Arquivo: $nomeZip"

    } catch {
        $statusEnvio = 'Erro'
        $erroMsg = $_.Exception.Message
        Escrever-Log "Erro ao processar log SP PID=${pidSolic}: $erroMsg" 'ERROR'
        if ($destino) {
            try {
                $htmlErro = Montar-HtmlErro $pidSolic $dataRefFmt $agoraFmt $erroMsg $AGENT_VERSION
                Enviar-Email $Usuario $Senha $destino "[Logs SP][$pidSolic] - ERRO" $htmlErro
                Escrever-Log "E-mail de erro enviado para $destino"
            } catch {
                Escrever-Log "Erro ao enviar e-mail de falha: $($_.Exception.Message)" 'ERROR'
            }
        }
    } finally {
        if (Test-Path $staging) { Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue }
    }

    Gravar-CsvLog @($pidSolic, $destino, $logs, $dataRefFmt, $nomeZip,
                    $agora.ToString('s'), $statusEnvio, $erroMsg)
}

# ---------------------------------------------------------------------------
# Ciclo: le a caixa e processa as solicitacoes
# ---------------------------------------------------------------------------
# ===========================================================================
# Atualizacao automatica (espelha o Agent Extrator Log)
#
# O BEC envia o pacote anexado a um e-mail [Atualizacao Agente SP]. O agente
# confere remetente, tamanho e SHA256, extrai, desfaz a neutralizacao feita
# pelo BEC (o Gmail recusa .ps1/.exe/.bat mesmo dentro de zip) e delega a troca
# dos arquivos a um script externo, disparado pelo Agendador de Tarefas — se
# fosse filho do agente, seria encerrado junto com o servico que ele mesmo para.
#
# Particularidade desta versao: os arquivos vindos do zip chegam marcados como
# "da internet" e a politica RemoteSigned recusaria o .ps1. Por isso o
# Unblock-File e obrigatorio, antes e depois da copia.
# ===========================================================================
$ASSUNTO_ATUALIZACAO   = 'Atualizacao Agente SP'
$SERVICO_NOME_SP       = 'ServerAgentSP'
$SCRIPT_AGENTE_SP      = 'server_agent_sp.ps1'
$ATUALIZACAO_DIR       = Join-Path $BASE_DIR 'atualizacao'
$PIDS_APLICADOS        = Join-Path $BASE_DIR 'atualizacoes_aplicadas.txt'
$TAREFA_ATUALIZACAO_SP = 'BEC_Atualiza_ServerAgentSP'
$ARQUIVO_RESULTADO     = 'resultado.txt'
$ARQUIVO_CONTEXTO      = 'contexto.txt'
$FLAG_RESULTADO        = 'resultado_enviado.flag'
# Guardam a configuracao da maquina: nunca sao sobrescritos na atualizacao
$PRESERVAR_ATUALIZACAO = @('agent.properties')

function Get-Sha256Arquivo {
    param([string]$Caminho)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fs  = [System.IO.File]::OpenRead($Caminho)
    try   { return (($sha.ComputeHash($fs) | ForEach-Object { $_.ToString('x2') }) -join '') }
    finally { $fs.Dispose(); $sha.Dispose() }
}

function Get-AnexoZip {
    <# Grava o primeiro anexo .zip da mensagem bruta. Retorna o caminho ou ''. #>
    param([string]$Raw, [string]$Destino)

    $idx = $Raw.IndexOf("`r`n`r`n")
    if ($idx -lt 0) { return '' }
    $cabecalho = $Raw.Substring(0, $idx)
    if ($cabecalho -notmatch '(?i)boundary="?([^";\s]+)"?') { return '' }
    $boundary = $matches[1]

    foreach ($parte in ($Raw -split [regex]::Escape("--$boundary"))) {
        if ($parte -notmatch '(?i)filename="?([^"\r\n;]+\.zip)"?') { continue }
        $nome = [System.IO.Path]::GetFileName($matches[1].Trim())

        $pi = $parte.IndexOf("`r`n`r`n")
        if ($pi -lt 0) { continue }
        $conteudo = $parte.Substring($pi + 4) -replace '\s', ''
        if (-not $conteudo) { continue }

        try {
            $bytes = [Convert]::FromBase64String($conteudo)
        } catch {
            Escrever-Log "[Atualizacao] Anexo em base64 invalido: $($_.Exception.Message)" 'ERROR'
            continue
        }
        $caminho = Join-Path $Destino $nome
        [System.IO.File]::WriteAllBytes($caminho, $bytes)
        return $caminho
    }
    return ''
}

function Restaurar-SufixoNeutro {
    param([string]$Pasta, [string]$Sufixo)
    if (-not $Sufixo) { return 0 }
    $n = 0
    foreach ($item in (Get-ChildItem -LiteralPath $Pasta -Recurse -File)) {
        if ($item.Name.EndsWith($Sufixo)) {
            $final = $item.FullName.Substring(0, $item.FullName.Length - $Sufixo.Length)
            Move-Item -LiteralPath $item.FullName -Destination $final -Force
            $n++
        }
    }
    return $n
}

function Gravar-ChaveValor {
    param([string]$Caminho, [hashtable]$Dados)
    $linhas = foreach ($k in $Dados.Keys) { "$k=$($Dados[$k])" }
    Set-Content -LiteralPath $Caminho -Value $linhas -Encoding UTF8
}

function Ler-ChaveValor {
    param([string]$Caminho)
    $dados = @{}
    if (-not (Test-Path -LiteralPath $Caminho)) { return $dados }
    foreach ($linha in (Get-Content -LiteralPath $Caminho -ErrorAction SilentlyContinue)) {
        if ($linha -match '^\s*([^=]+)=(.*)$') { $dados[$matches[1].Trim()] = $matches[2].Trim() }
    }
    return $dados
}

function Test-PidJaAplicado {
    # Nao usar $Pid como nome de parametro: e variavel automatica somente-leitura
    param([string]$PidAtualizacao)
    if (-not $PidAtualizacao -or -not (Test-Path -LiteralPath $PIDS_APLICADOS)) { return $false }
    foreach ($linha in (Get-Content -LiteralPath $PIDS_APLICADOS)) {
        if ($linha.Trim().EndsWith($PidAtualizacao)) { return $true }
    }
    return $false
}

function Escrever-ScriptAtualizacaoSP {
    <#  Gera o atualizador em PowerShell.

        A primeira versao usava .bat, que morria logo apos parar o servico sem
        registrar nada. Duas causas provaveis, ambas resolvidas aqui: "timeout"
        falha quando nao ha console (o caso da tarefa agendada em sessao 0), e o
        cmd.exe parando servico e copiando executaveis e um padrao que o EDR
        deste servidor derruba. O powershell.exe e assinado pela Microsoft e ja
        e confiavel neste ambiente - mesma razao pela qual o agente roda nele.

        Nao usar $Pid como nome de parametro: e variavel automatica somente-leitura. #>
    param([string]$Pasta, [string]$Origem, [string]$PidAtualizacao)

    $backup    = Join-Path $Pasta 'backup'
    $logPs     = Join-Path $Pasta 'atualizacao.log'
    $script    = Join-Path $Pasta 'aplicar_atualizacao.ps1'
    $resultado = Join-Path $Pasta $ARQUIVO_RESULTADO
    $preservar = ($PRESERVAR_ATUALIZACAO | ForEach-Object { "'$_'" }) -join ','

    $modelo = @'
# Atualizador do Server Agent SP - gerado automaticamente pelo agente.
# Roda pelo Agendador de Tarefas, fora da arvore de processos do servico.
$ErrorActionPreference = 'Continue'

$SERVICE   = '__SERVICE__'
$INSTALL   = '__INSTALL__'
$ORIGEM    = '__ORIGEM__'
$BACKUP    = '__BACKUP__'
$LOGF      = '__LOG__'
$RESULTADO = '__RESULTADO__'
$TAREFA    = '__TAREFA__'
$PIDATU    = '__PIDATU__'
$PRESERVAR = @(__PRESERVAR__)

function Log {
    param([string]$m)
    $linha = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    try { Add-Content -LiteralPath $LOGF -Value $linha -Encoding UTF8 } catch { }
}

function Aguardar-Estado {
    param([string]$Alvo, [int]$Tentativas = 20)
    for ($i = 1; $i -le $Tentativas; $i++) {
        Start-Sleep -Seconds 2
        $svc = Get-Service -Name $SERVICE -ErrorAction SilentlyContinue
        if (-not $svc) { return $false }
        if ($svc.Status -eq $Alvo) { return $true }
    }
    return $false
}

function Copiar-Novos {
    foreach ($item in (Get-ChildItem -LiteralPath $ORIGEM)) {
        if ($PRESERVAR -contains $item.Name) {
            Log "  preservado (nao sobrescrito): $($item.Name)"
            continue
        }
        Copy-Item -LiteralPath $item.FullName -Destination $INSTALL -Recurse -Force
    }
}

$status  = 'FALHA'
$detalhe = 'Atualizacao interrompida antes de concluir.'

try {
    Log "=== Atualizacao PID=$PIDATU iniciada ==="
    Log "Identidade: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"

    # ---- Para o servico ----
    Log "Parando servico $SERVICE..."
    try { Stop-Service -Name $SERVICE -Force -ErrorAction Stop }
    catch { Log "  aviso ao parar: $($_.Exception.Message)" }

    if (-not (Aguardar-Estado 'Stopped')) {
        Log '  servico nao parou em 40s; encerrando o processo do agente.'
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*server_agent_sp.ps1*' } |
            ForEach-Object {
                Log "  encerrando PID $($_.ProcessId)"
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
        Start-Sleep -Seconds 3
    }
    Log '[OK] Servico parado.'

    # ---- Backup do que sera substituido ----
    if (-not (Test-Path -LiteralPath $BACKUP)) { New-Item -ItemType Directory -Path $BACKUP -Force | Out-Null }
    Get-ChildItem -LiteralPath $INSTALL -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in '.ps1', '.bat', '.exe' } |
        Copy-Item -Destination $BACKUP -Force
    Log "[OK] Backup gravado em $BACKUP"

    # ---- Copia a nova versao ----
    Copiar-Novos
    Log '[OK] Arquivos da nova versao copiados.'

    # Sem isso a politica RemoteSigned recusa os .ps1 vindos do zip
    Get-ChildItem -LiteralPath $INSTALL -Recurse -File -ErrorAction SilentlyContinue |
        Unblock-File -ErrorAction SilentlyContinue
    Log '[OK] Scripts desbloqueados (Unblock-File).'

    # ---- Sobe o servico ----
    Log 'Iniciando servico...'
    Start-Service -Name $SERVICE -ErrorAction SilentlyContinue
    if (Aguardar-Estado 'Running') {
        Log '[OK] Servico em execucao. Atualizacao concluida.'
        $status  = 'SUCESSO'
        $detalhe = 'Nova versao instalada e servico em execucao.'
    } else {
        throw 'Servico nao entrou em execucao com a nova versao.'
    }
} catch {
    $detalhe = $_.Exception.Message
    Log "[ERRO] $detalhe"
    Log 'Restaurando versao anterior a partir do backup...'
    try {
        Stop-Service -Name $SERVICE -Force -ErrorAction SilentlyContinue
        [void](Aguardar-Estado 'Stopped' 10)
        Get-ChildItem -LiteralPath $BACKUP -File -ErrorAction SilentlyContinue |
            Copy-Item -Destination $INSTALL -Force
        Get-ChildItem -LiteralPath $INSTALL -Recurse -File -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
        Start-Service -Name $SERVICE -ErrorAction SilentlyContinue
        if (Aguardar-Estado 'Running' 10) {
            Log '[OK] Versao anterior restaurada e servico em execucao.'
            $status  = 'REVERTIDO'
            $detalhe = "$detalhe Versao anterior restaurada e servico em execucao."
        } else {
            Log '[FALHA] Servico nao subiu nem apos a restauracao. Requer acao manual.'
            $status  = 'FALHA_CRITICA'
            $detalhe = "$detalhe Servico nao subiu nem apos a restauracao; requer acao manual."
        }
    } catch {
        Log "[FALHA] Erro tambem na restauracao: $($_.Exception.Message)"
        $status  = 'FALHA_CRITICA'
        $detalhe = "$detalhe Falha tambem na restauracao: $($_.Exception.Message)"
    }
} finally {
    Log "=== Atualizacao PID=$PIDATU finalizada (STATUS=$status) ==="
    # O agente le este arquivo ao subir e responde por e-mail com o resultado
    $linhas = @("STATUS=$status", "DETALHE=$detalhe",
                "DATAHORA=$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')")
    try { Set-Content -LiteralPath $RESULTADO -Value $linhas -Encoding UTF8 } catch { }
    schtasks /delete /tn $TAREFA /f 2>&1 | Out-Null
}
'@

    $conteudo = $modelo.
        Replace('__SERVICE__',   $SERVICO_NOME_SP).
        Replace('__INSTALL__',   $BASE_DIR).
        Replace('__ORIGEM__',    $Origem).
        Replace('__BACKUP__',    $backup).
        Replace('__LOG__',       $logPs).
        Replace('__RESULTADO__', $resultado).
        Replace('__TAREFA__',    $TAREFA_ATUALIZACAO_SP).
        Replace('__PIDATU__',    $PidAtualizacao).
        Replace('__PRESERVAR__', $preservar)

    Set-Content -LiteralPath $script -Value $conteudo -Encoding UTF8
    return $script
}

function Disparar-ScriptAtualizacao {
    param([string]$Script)

    # Executa sob o powershell.exe (assinado pela Microsoft), nao pelo cmd.exe:
    # o EDR deste servidor derruba processos que param servico e substituem
    # executaveis, e foi o que aconteceu com a primeira versao em .bat.
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $psExe)) { $psExe = 'powershell.exe' }
    $comando = "$psExe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Script`""

    $criar = schtasks /create /tn $TAREFA_ATUALIZACAO_SP /tr $comando `
                      /sc once /st 00:00 /ru SYSTEM /rl HIGHEST /f 2>&1
    if ($LASTEXITCODE -eq 0) {
        $rodar = schtasks /run /tn $TAREFA_ATUALIZACAO_SP 2>&1
        if ($LASTEXITCODE -eq 0) {
            Escrever-Log '[Atualizacao] Script agendado e disparado via Agendador de Tarefas.'
            return $true
        }
        Escrever-Log "[Atualizacao] Tarefa criada mas nao disparou ($rodar); executando direto." 'WARN'
    } else {
        Escrever-Log "[Atualizacao] schtasks falhou ($criar); executando direto." 'WARN'
    }

    Start-Process -FilePath $psExe `
                  -ArgumentList '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $Script `
                  -WindowStyle Hidden
    return $true
}

function Processar-Atualizacao {
    param([string]$Raw, [string]$Corpo, [hashtable]$Props, [string]$Usuario)

    $pid_    = Extrair-Campo $Corpo 'PID'
    $pacote  = Extrair-Campo $Corpo 'Pacote'
    $sha     = (Extrair-Campo $Corpo 'SHA256').ToLower()
    $sufixo  = Extrair-Campo $Corpo 'SufixoNeutro'
    $destino = Extrair-Campo $Corpo 'Destino'
    $usuarioBec = Extrair-Campo $Corpo 'Usuario'
    $tamanhoInformado = Extrair-Campo $Corpo 'TamanhoBytes'

    Escrever-Log "[Atualizacao] PID=$pid_ | Pacote=$pacote | Resultado para: $destino"

    # Tudo dentro do try: uma falha aqui nao pode derrubar o ciclo e impedir o
    # processamento das solicitacoes de log da mesma rodada.
    try {
        # Remetente: por padrao so a propria conta monitorada (o BEC envia de e para ela)
        $remetente = ''
        if ($Raw -match '(?im)^From:\s*(.+)$') { $remetente = $matches[1].Trim() }
        if ($remetente -match '<([^>]+)>') { $remetente = $matches[1] }
        $autorizados = @($Usuario.ToLower())
        if ($Props['atualizacao.remetentes']) {
            $autorizados += ($Props['atualizacao.remetentes'] -split ',' | ForEach-Object { $_.Trim().ToLower() })
        }
        if ($autorizados -notcontains $remetente.Trim().ToLower()) {
            Escrever-Log "[Atualizacao] Remetente nao autorizado: $remetente. Pacote ignorado." 'ERROR'
            return $false
        }

        if (Test-PidJaAplicado $pid_) {
            Escrever-Log "[Atualizacao] PID $pid_ ja aplicado anteriormente. Ignorando." 'WARN'
            return $false
        }

        if (-not $pid_) { $pid_ = (Get-Date -Format 'yyyyMMddHHmmss') }
        $pasta = Join-Path $ATUALIZACAO_DIR $pid_
        if (Test-Path -LiteralPath $pasta) { Remove-Item -LiteralPath $pasta -Recurse -Force }
        New-Item -ItemType Directory -Path $pasta -Force | Out-Null

        $zip = Get-AnexoZip $Raw $pasta
        if (-not $zip) {
            Escrever-Log '[Atualizacao] E-mail sem anexo .zip. Nada a fazer.' 'ERROR'
            return $false
        }

        $tamanho = (Get-Item -LiteralPath $zip).Length
        if ($tamanhoInformado -and "$tamanho" -ne $tamanhoInformado.Trim()) {
            Escrever-Log "[Atualizacao] Tamanho divergente: recebido $tamanho, informado $tamanhoInformado. Pacote descartado." 'ERROR'
            return $false
        }

        if ($sha) {
            $calculado = Get-Sha256Arquivo $zip
            if ($calculado -ne $sha) {
                Escrever-Log "[Atualizacao] SHA256 divergente. Esperado $sha, calculado $calculado. Pacote descartado." 'ERROR'
                return $false
            }
            Escrever-Log '[Atualizacao] SHA256 conferido.'
        } else {
            Escrever-Log '[Atualizacao] E-mail sem SHA256; seguindo sem verificacao de integridade.' 'WARN'
        }

        $extraido = Join-Path $pasta 'extraido'
        New-Item -ItemType Directory -Path $extraido -Force | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $extraido)

        $restaurados = Restaurar-SufixoNeutro $extraido $sufixo
        if ($restaurados) {
            Escrever-Log "[Atualizacao] $restaurados arquivo(s) renomeado(s) de volta (sufixo $sufixo)."
        }

        # Sem isso a politica RemoteSigned recusa o .ps1 vindo do zip
        Get-ChildItem -LiteralPath $extraido -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
        Escrever-Log '[Atualizacao] Arquivos extraidos desbloqueados (Unblock-File).'

        if (-not (Test-Path -LiteralPath (Join-Path $extraido $SCRIPT_AGENTE_SP))) {
            Escrever-Log "[Atualizacao] $SCRIPT_AGENTE_SP nao encontrado no pacote. Atualizacao abortada." 'ERROR'
            return $false
        }

        # Gravado antes de disparar: depois da troca este processo nao existe
        # mais, e e por este arquivo que a nova versao sabe para quem responder.
        Gravar-ChaveValor (Join-Path $pasta $ARQUIVO_CONTEXTO) @{
            'PID'            = $pid_
            'Destino'        = $destino
            'Pacote'         = $pacote
            'Usuario'        = $usuarioBec
            'VersaoAnterior' = $AGENT_VERSION
        }

        $script = Escrever-ScriptAtualizacaoSP $pasta $extraido $pid_
        Add-Content -LiteralPath $PIDS_APLICADOS -Value ("{0};{1};{2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $pacote, $pid_)

        Escrever-Log '[Atualizacao] Pacote validado. O servico sera parado, atualizado e reiniciado.'
        return (Disparar-ScriptAtualizacao $script)
    } catch {
        Escrever-Log "[Atualizacao] Falha ao preparar a atualizacao: $($_.Exception.Message)" 'ERROR'
        return $false
    }
}

function Enviar-ResultadosAtualizacao {
    <#  Responde o desfecho das atualizacoes concluidas. Roda na subida do
        agente: como quem envia e o processo recem-iniciado, a versao informada
        e comprovadamente a que esta em execucao.  #>
    param([hashtable]$Props)

    if (-not (Test-Path -LiteralPath $ATUALIZACAO_DIR)) { return }

    foreach ($pasta in (Get-ChildItem -LiteralPath $ATUALIZACAO_DIR -Directory | Sort-Object Name)) {
        $arqResultado = Join-Path $pasta.FullName $ARQUIVO_RESULTADO
        $flag         = Join-Path $pasta.FullName $FLAG_RESULTADO
        if (-not (Test-Path -LiteralPath $arqResultado) -or (Test-Path -LiteralPath $flag)) { continue }

        try {
            $resultado = Ler-ChaveValor $arqResultado
            $contexto  = Ler-ChaveValor (Join-Path $pasta.FullName $ARQUIVO_CONTEXTO)

            $destino = $contexto['Destino']
            if (-not $destino) {
                Escrever-Log "[Atualizacao] Resultado de $($pasta.Name) sem destino; e-mail nao enviado." 'WARN'
                New-Item -ItemType File -Path $flag -Force | Out-Null
                continue
            }

            $status = $resultado['STATUS']
            if (-not $status) { $status = 'DESCONHECIDO' }
            $pidCtx = $contexto['PID']
            if (-not $pidCtx) { $pidCtx = $pasta.Name }

            $corpo = @"
PID: $pidCtx
Agente: Server Agent SP
Maquina: $env:COMPUTERNAME
Status: $status
VersaoInstalada: $AGENT_VERSION
VersaoAnterior: $($contexto['VersaoAnterior'])
Pacote: $($contexto['Pacote'])
Detalhe: $($resultado['DETALHE'])
ConcluidoEm: $($resultado['DATAHORA'])
SolicitadoPor: $($contexto['Usuario'])
"@
            $corpoHtml = "<pre style=""font-family:Consolas,monospace;font-size:13px"">$corpo</pre>"
            $assunto   = "[Resultado Atualizacao Agente SP] - [$status] - [$pidCtx]"
            $logAtu    = Join-Path $pasta.FullName 'atualizacao.log'
            $anexo     = ''
            if (Test-Path -LiteralPath $logAtu) { $anexo = $logAtu }

            Enviar-Email $Props['email'] $Props['senha'] $destino $assunto $corpoHtml $anexo

            New-Item -ItemType File -Path $flag -Force | Out-Null
            Escrever-Log "[Atualizacao] Resultado $status (PID=$pidCtx) enviado para $destino. Versao em execucao: $AGENT_VERSION"
        } catch {
            Escrever-Log "[Atualizacao] Falha ao enviar resultado de $($pasta.Name): $($_.Exception.Message)" 'ERROR'
        }
    }
}

function Executar-Ciclo {
    $props   = Ler-Properties $CONFIG_FILE
    $usuario = $props['email']
    $senha   = $props['senha']
    if (-not $usuario -or -not $senha) {
        Escrever-Log 'email/senha nao configurados em agent.properties' 'ERROR'
        return
    }

    # A cada ciclo, e nao so na subida: o atualizador confirma o servico no ar e
    # so entao grava o resultado, alguns segundos DEPOIS de o agente ja ter
    # iniciado. Verificando so no start, esse resultado nunca seria enviado.
    # Fica antes do IMAP para nao depender da conexao com a caixa.
    try { Enviar-ResultadosAtualizacao $props }
    catch { Escrever-Log "[Atualizacao] Erro ao enviar resultado pendente: $($_.Exception.Message)" 'ERROR' }

    $cliente = $null
    $ssl     = $null
    try {
        $cliente = New-Object System.Net.Sockets.TcpClient('imap.gmail.com', 993)
        $ssl = New-Object System.Net.Security.SslStream($cliente.GetStream(), $false)
        $ssl.AuthenticateAsClient('imap.gmail.com')
        [void](Read-LinhaImap $ssl)   # saudacao

        Send-ComandoImap $ssl 'a1' "LOGIN `"$usuario`" `"$senha`""
        $r = Read-RespostaImap $ssl 'a1'
        if (-not $r.Ok) { throw "Falha no LOGIN IMAP: $($r.Final)" }

        Send-ComandoImap $ssl 'a2' 'SELECT INBOX'
        $r = Read-RespostaImap $ssl 'a2'
        if (-not $r.Ok) { throw "Falha no SELECT INBOX: $($r.Final)" }

        Send-ComandoImap $ssl 'a3' 'SEARCH UNSEEN'
        $r = Read-RespostaImap $ssl 'a3'
        $ids = @()
        foreach ($linha in $r.Linhas) {
            if ($linha -match '^\*\s+SEARCH(.*)$') {
                $ids = @($matches[1].Trim() -split '\s+' | Where-Object { $_ })
            }
        }
        # A varredura roda a cada minuto; sem novidade, so registra em DEBUG para
        # nao inundar o log com "0 e-mails".
        if ($ids.Count -gt 0) {
            Escrever-Log "Emails nao lidos encontrados: $($ids.Count)"
        } else {
            Escrever-Log 'Emails nao lidos encontrados: 0' 'DEBUG'
        }

        $n = 0
        foreach ($id in $ids) {
            $n++
            $tag = "f$n"
            # BODY.PEEK[] nao marca como lido — quem nao for do agente fica intacto
            Send-ComandoImap $ssl $tag "FETCH $id (BODY.PEEK[])"

            $raw = ''
            while ($true) {
                $linha = Read-LinhaImap $ssl
                if ($linha -match "^$tag\s+(OK|NO|BAD)") { break }
                if ($linha -match '\{(\d+)\}\s*$') {
                    $raw = Read-BytesImap $ssl ([int]$matches[1])
                }
            }
            if (-not $raw) { continue }

            $assuntoRaw = ''
            if ($raw -match '(?im)^Subject:\s*(.+(?:\r?\n[ \t].+)*)') {
                $assuntoRaw = ($matches[1] -replace '\r?\n[ \t]', ' ').Trim()
            }
            $assunto = Decode-EncodedWords $assuntoRaw

            $ehAtualizacao = $assunto -match [regex]::Escape($ASSUNTO_ATUALIZACAO)
            $ehSolicitacao = $assunto -match [regex]::Escape($ASSUNTO_GATILHO)

            if ($ehAtualizacao -or $ehSolicitacao) {
                $corpo = Get-CorpoTexto $raw
                # Contexto para o log: usuario que pediu e PID, extraidos do corpo.
                # Todas as linhas emitidas no processamento herdam [usuario | PID].
                $script:CtxUsuario = Extrair-Campo $corpo 'Usuario'
                $script:CtxPid     = Extrair-Campo $corpo 'PID'
                try {
                    Escrever-Log "Tratando e-mail: $assunto"
                    if ($ehAtualizacao) {
                        # Marcado como lido antes de processar: o servico sera
                        # reiniciado no meio e nao pode reprocessar a mensagem.
                        Send-ComandoImap $ssl "s$n" "STORE $id +FLAGS (\Seen)"
                        [void](Read-RespostaImap $ssl "s$n")
                        if (Processar-Atualizacao $raw $corpo $props $usuario) {
                            # O servico sera parado pelo script; encerra o ciclo
                            # para nao processar outros e-mails na troca de arquivos.
                            break
                        }
                    } else {
                        Processar-Solicitacao $corpo $props $usuario $senha
                        Send-ComandoImap $ssl "s$n" "STORE $id +FLAGS (\Seen)"
                        [void](Read-RespostaImap $ssl "s$n")
                    }
                } finally {
                    $script:CtxUsuario = ''
                    $script:CtxPid     = ''
                }
            } else {
                Escrever-Log "Ignorando e-mail: $assunto" 'DEBUG'
            }
        }

        Send-ComandoImap $ssl 'z1' 'LOGOUT'
    } finally {
        if ($ssl)     { $ssl.Dispose() }
        if ($cliente) { $cliente.Close() }
    }
}

# ---------------------------------------------------------------------------
# Diagnostico: -TesteConexao
# ---------------------------------------------------------------------------
function Get-IdentidadeAtual {
    try { return [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
    catch { return 'desconhecida' }
}

function Testar-Conexao {
    $props = Ler-Properties $CONFIG_FILE
    Write-Host "Server Agent SP v$AGENT_VERSION - teste de conexao"
    Write-Host "Identidade em uso: $(Get-IdentidadeAtual)"
    Write-Host "  ATENCAO: rodando assim voce testa com SEU usuario. Como servico,"
    Write-Host "  o agente roda como NT AUTHORITY\SYSTEM e acessa a rede como a conta"
    Write-Host "  de computador ($env:COMPUTERNAME`$) - e essa conta que precisa ter"
    Write-Host "  direito de leitura nas pastas de log dos servidores."
    Write-Host ("-" * 60)
    foreach ($alvo in @(@('imap.gmail.com', 993), @('smtp.gmail.com', 587))) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient($alvo[0], $alvo[1])
            $c.Close()
            Write-Host "[OK]    TCP  $($alvo[0]):$($alvo[1])" -ForegroundColor Green
        } catch {
            Write-Host "[FALHA] TCP  $($alvo[0]):$($alvo[1]) -> $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    try {
        $cliente = New-Object System.Net.Sockets.TcpClient('imap.gmail.com', 993)
        $ssl = New-Object System.Net.Security.SslStream($cliente.GetStream(), $false)
        $ssl.AuthenticateAsClient('imap.gmail.com')
        [void](Read-LinhaImap $ssl)
        Send-ComandoImap $ssl 'a1' "LOGIN `"$($props['email'])`" `"$($props['senha'])`""
        $r = Read-RespostaImap $ssl 'a1'
        if ($r.Ok) { Write-Host "[OK]    IMAP login $($props['email'])" -ForegroundColor Green }
        else { Write-Host "[FALHA] IMAP login: $($r.Final)" -ForegroundColor Red }
        Send-ComandoImap $ssl 'z1' 'LOGOUT'
        $ssl.Dispose(); $cliente.Close()
    } catch {
        Write-Host "[FALHA] IMAP -> $($_.Exception.Message)" -ForegroundColor Red
    }
    Write-Host "`nAcesso as shares (usuario=$(if ($props['windows_user']) { $props['windows_user'] } else { 'sessao atual' })):"
    $sharesFeitas = @{}
    foreach ($chave in ($props.Keys | Where-Object { $_ -like 'log.*.caminho' } | Sort-Object)) {
        $share = Get-ShareRaiz $props[$chave]
        if ($share -and -not $sharesFeitas.ContainsKey($share.ToLower())) {
            Autenticar-Share $share $props['windows_user'] $props['windows_senha']
            $sharesFeitas[$share.ToLower()] = $true
        }
    }

    Write-Host "`nLogs configurados (dia atual):"
    foreach ($chave in ($props.Keys | Where-Object { $_ -like 'log.*.caminho' } | Sort-Object)) {
        $nome = $chave -replace '^log\.', '' -replace '\.caminho$', ''
        $tipo = $props["log.$nome.tipo"]
        if (-not $tipo) { $tipo = 'arquivo' }
        $tipo = $tipo.Trim().ToLower()
        $base   = (Resolver-FormatoLog $props[$chave]).TrimEnd('\', '/')
        $padrao = Resolver-FormatoLog $props["log.$nome.formato"]
        $motivo = ''
        $itens = Localizar-Itens $base $padrao $tipo ([ref]$motivo)
        if ($itens.Count -gt 0) {
            Write-Host "  [OK]      $nome ($tipo) -> $(Descrever-Itens $itens $base)" -ForegroundColor Green
        } else {
            Write-Host "  [AUSENTE] $nome ($tipo) -> $base\$padrao" -ForegroundColor Yellow
            Write-Host "            motivo: $motivo" -ForegroundColor DarkGray
        }
    }

    # Historico: valida com a data de ontem, que e o caso de uso mais comum
    $ontem = (Get-Date).AddDays(-1)
    Write-Host "`nLogs historicos, testados com a data de ontem ($($ontem.ToString('dd/MM/yyyy'))):"
    $chavesHist = @($props.Keys | Where-Object { $_ -like 'historico.*.caminho' } | Sort-Object)
    if ($chavesHist.Count -eq 0) {
        Write-Host '  (nenhum historico.<log>.caminho configurado)' -ForegroundColor DarkGray
    }
    foreach ($chave in $chavesHist) {
        $nome = $chave -replace '^historico\.', '' -replace '\.caminho$', ''
        $share = Get-ShareRaiz $props[$chave]
        if ($share -and -not $sharesFeitas.ContainsKey($share.ToLower())) {
            Autenticar-Share $share $props['windows_user'] $props['windows_senha']
            $sharesFeitas[$share.ToLower()] = $true
        }
        $cfg = Resolver-LogHistorico $nome $props $ontem
        $motivo = ''
        $itens = Localizar-Itens $cfg.Base $cfg.Padrao $cfg.Tipo ([ref]$motivo)
        if ($itens.Count -gt 0) {
            Write-Host "  [OK]      $nome ($($cfg.Tipo)) -> $(Descrever-Itens $itens $cfg.Base)" -ForegroundColor Green
        } else {
            Write-Host "  [AUSENTE] $nome ($($cfg.Tipo)) -> $($cfg.Base)\$($cfg.Padrao)" -ForegroundColor Yellow
            Write-Host "            motivo: $motivo" -ForegroundColor DarkGray
        }
    }

    # Logs que so tem configuracao do dia atual nao atendem pedidos retroativos
    $semHist = @($props.Keys | Where-Object { $_ -like 'log.*.caminho' } |
                 ForEach-Object { $_ -replace '^log\.', '' -replace '\.caminho$', '' } |
                 Where-Object { -not $props["historico.$_.caminho"] })
    if ($semHist.Count -gt 0) {
        Write-Host "`nSem configuracao de historico (so atendem a data de hoje): $($semHist -join ', ')" -ForegroundColor DarkYellow
    }
}

# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
# Carrega apenas as funcoes (usado pelos testes): . .\server_agent_sp.ps1 -Biblioteca
if ($args -contains '-Biblioteca') { return }

if ($args -contains '-TesteConexao' -or $args -contains '--teste-conexao') {
    Testar-Conexao
    return
}
if ($args -contains '-TesteFormato' -or $args -contains '--teste-formato') {
    Write-Host 'Dia atual (log.<nome>.formato):'
    foreach ($f in @('{integrador}.log', '{linx-webservices}.log', '{CSIDebugFile}.txt',
                     '{lgComandosSQL_}[YYYYmmdd].txt', '[ddmmyyyy].log', '[yyyymmdd]')) {
        Write-Host "  $f  ->  $(Resolver-FormatoLog $f)"
    }
    $ontem = (Get-Date).AddDays(-1)
    Write-Host "`nHistorico (historico.<nome>.formato) para $($ontem.ToString('dd/MM/yyyy')):"
    foreach ($f in @('{linx-webservices_}[yyyy-mm-dd](xxx).zip', '[yyyymmdd]',
                     '{communication_}[yyyy-mm-dd](xxx).zip',
                     '{lgComandosSQL_}[yyyymmdd].txt', '{logsTesouraria_}[yyyymmdd].zip',
                     '[ddmmyyyy].log')) {
        Write-Host "  $f  ->  $(Resolver-FormatoLog $f $ontem)"
    }
    return
}

$propsIni  = Ler-Properties $CONFIG_FILE
$intervalo = [int]($propsIni['intervalo_minutos'])
if ($intervalo -le 0) { $intervalo = 5 }

# Nivel de log e identidade resolvidos antes da primeira linha
$nivelCfg = $propsIni['log.level']; if (-not $nivelCfg) { $nivelCfg = 'INFO' }
$script:LogNivelMin = ConvertTo-NivelNum $nivelCfg
$idFull = Get-IdentidadeAtual
$script:LogIdentidade = if ($idFull -match '\\([^\\]+)$') { $matches[1] } else { $idFull }

Escrever-Log "Server Agent SP v$AGENT_VERSION (PowerShell) iniciado. Intervalo de verificacao: $intervalo minuto(s). Log level: $($nivelCfg.ToUpper())."
Escrever-Log "Identidade: $(Get-IdentidadeAtual) | acesso a rede como: $env:USERDOMAIN\$env:COMPUTERNAME`$ (quando SYSTEM)"

# Se o agente subiu logo apos uma atualizacao, responde o resultado
try {
    Enviar-ResultadosAtualizacao $propsIni
} catch {
    Escrever-Log "[Atualizacao] Erro ao enviar resultado pendente: $($_.Exception.Message)" 'ERROR'
}

while ($true) {
    try {
        Executar-Ciclo
    } catch {
        Escrever-Log "Erro geral no ciclo: $($_.Exception.Message)" 'ERROR'
    }
    Start-Sleep -Seconds ($intervalo * 60)
}
