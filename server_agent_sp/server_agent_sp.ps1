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

$AGENT_VERSION   = '2.0.0'
$ASSUNTO_GATILHO = 'Log SP'   # casa com "[Solicitação Log SP]" sem depender de acentuacao

if ($PSScriptRoot) { $BASE_DIR = $PSScriptRoot } else { $BASE_DIR = (Get-Location).Path }
$CONFIG_FILE = Join-Path $BASE_DIR 'agent.properties'
$CSV_LOG     = Join-Path $BASE_DIR 'historico_envio_logs.csv'
$LOG_DIR     = Join-Path $BASE_DIR 'log'
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR | Out-Null }

$LOG_FILE_BASE = 'server_agent_sp'
$script:LogData = $null

# ---------------------------------------------------------------------------
# Log diario: o dia corrente vai no arquivo fixo; na virada do dia o arquivo e
# renomeado para server_agent_sp_<data-anterior>.log
# ---------------------------------------------------------------------------
function Escrever-Log {
    param([string]$Mensagem, [string]$Nivel = 'INFO')

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

    $linha = "$($agora.ToString('yyyy-MM-dd HH:mm:ss')) [$Nivel] $Mensagem"
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
# Formato de nomenclatura dos logs
#   {texto}  -> parte fixa       [tokens] -> data (yyyy, yy, mm, dd)
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
        else                    { $resultado += $Tokens[$i];            $i += 1 }
    }
    return $resultado
}

function Resolver-Formato {
    param([string]$Formato, [datetime]$Data = (Get-Date))
    $regex = [regex]'\{([^}]*)\}|\[([^\]]*)\]'
    $resultado = ''
    $pos = 0
    foreach ($m in $regex.Matches($Formato)) {
        $resultado += $Formato.Substring($pos, $m.Index - $pos)
        if ($m.Groups[1].Success) { $resultado += $m.Groups[1].Value }
        else { $resultado += (Resolver-TokensData $m.Groups[2].Value $Data) }
        $pos = $m.Index + $m.Length
    }
    $resultado += $Formato.Substring($pos)
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
    param($Pid_, $DataRefFmt, $AgoraFmt, $NomeZip, $Arquivos, $Versao)

    $nOk   = @($Arquivos | Where-Object { $_.status -eq 'ok' }).Count
    $nErro = $Arquivos.Count - $nOk

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
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Data dos Logs</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f'>$DataRefFmt</p>
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

    Escrever-Log "[SolicitacaoLogSP] PID=$pidSolic | Logs=$logs | Data=$(if ($dataTxt) { $dataTxt } else { 'hoje' })"

    $dataRef = $null
    if ($dataTxt) {
        $dataRef = Parsear-Data $dataTxt
        if (-not $dataRef) { Escrever-Log "Campo Data '$dataTxt' invalido - usando data atual" 'WARN' }
    }
    if (-not $dataRef) { $dataRef = Get-Date }

    $agora       = Get-Date
    $agoraFmt    = $agora.ToString('dd/MM/yyyy HH:mm:ss')
    $dataRefFmt  = $dataRef.ToString('dd/MM/yyyy')
    $nomeZip     = "LOG-SP-$($agora.ToString('ddMMyyyyHHmmss')).zip"
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

        foreach ($item in $listaLogs) {
            $caminhoBase = $Props["log.$item.caminho"]
            $formato     = $Props["log.$item.formato"]
            if (-not $caminhoBase -or -not $formato) {
                Escrever-Log "Log '$item' sem configuracao (log.$item.caminho/.formato)." 'WARN'
                $arquivos += [pscustomobject]@{ nome = $item; caminho = '-'; status = 'sem_config' }
                continue
            }

            $nomeArquivo = Resolver-Formato $formato $dataRef
            $completo    = Join-Path $caminhoBase $nomeArquivo

            # Autentica uma vez por share antes do primeiro acesso
            $share = Get-ShareRaiz $completo
            if ($share -and -not $sharesFeitas.ContainsKey($share.ToLower())) {
                Autenticar-Share $share $winUser $winSenha
                $sharesFeitas[$share.ToLower()] = $true
            }

            Escrever-Log "Arquivo $item -> $completo"

            $motivo = ''
            if (Arquivo-Existe $completo ([ref]$motivo)) {
                try {
                    # Subpasta por log evita colisao entre logs de nome igual
                    $destPasta = Join-Path $staging $item
                    New-Item -ItemType Directory -Path $destPasta -Force | Out-Null
                    Copy-Item -LiteralPath $completo -Destination (Join-Path $destPasta $nomeArquivo) -Force -ErrorAction Stop
                    $arquivos += [pscustomobject]@{ nome = $item; caminho = $completo; status = 'ok' }
                    $algumArquivo = $true
                } catch {
                    Escrever-Log "Falha ao copiar ${completo}: $($_.Exception.Message)" 'WARN'
                    $arquivos += [pscustomobject]@{ nome = $item; caminho = $completo; status = 'nao_encontrado' }
                }
            } else {
                Escrever-Log "Arquivo indisponivel ($motivo): $completo" 'WARN'
                $arquivos += [pscustomobject]@{ nome = $item; caminho = $completo; status = 'nao_encontrado' }
            }
        }

        if (-not $algumArquivo) { throw 'Nenhum arquivo valido para compactar.' }

        Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zipPath -Force

        $html = Montar-HtmlResultado $pidSolic $dataRefFmt $agoraFmt $nomeZip $arquivos $AGENT_VERSION
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
function Executar-Ciclo {
    $props   = Ler-Properties $CONFIG_FILE
    $usuario = $props['email']
    $senha   = $props['senha']
    if (-not $usuario -or -not $senha) {
        Escrever-Log 'email/senha nao configurados em agent.properties' 'ERROR'
        return
    }

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
        Escrever-Log "Emails nao lidos encontrados: $($ids.Count)"

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

            if ($assunto -match [regex]::Escape($ASSUNTO_GATILHO)) {
                Escrever-Log "Tratando e-mail: $assunto"
                $corpo = Get-CorpoTexto $raw
                Processar-Solicitacao $corpo $props $usuario $senha
                Send-ComandoImap $ssl "s$n" "STORE $id +FLAGS (\Seen)"
                [void](Read-RespostaImap $ssl "s$n")
            } else {
                Escrever-Log "Ignorando e-mail: $assunto"
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

    Write-Host "`nLogs configurados:"
    foreach ($chave in ($props.Keys | Where-Object { $_ -like 'log.*.caminho' } | Sort-Object)) {
        $nome = $chave -replace '^log\.', '' -replace '\.caminho$', ''
        $arquivo = Join-Path $props[$chave] (Resolver-Formato $props["log.$nome.formato"])
        $motivo = ''
        if (Arquivo-Existe $arquivo ([ref]$motivo)) {
            Write-Host "  [OK]      $nome -> $arquivo" -ForegroundColor Green
        } else {
            Write-Host "  [AUSENTE] $nome -> $arquivo" -ForegroundColor Yellow
            Write-Host "            motivo: $motivo" -ForegroundColor DarkGray
        }
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
    foreach ($f in @('{integrador}.log', '{linx-webservices}.log', '{CSIDebugFile}.txt',
                     '{lgComandosSQL_}[YYYYmmdd].txt', '[ddmmyyyy].log')) {
        Write-Host "$f  ->  $(Resolver-Formato $f)"
    }
    return
}

$propsIni  = Ler-Properties $CONFIG_FILE
$intervalo = [int]($propsIni['intervalo_minutos'])
if ($intervalo -le 0) { $intervalo = 5 }
Escrever-Log "Server Agent SP v$AGENT_VERSION (PowerShell) iniciado. Intervalo de verificacao: $intervalo minuto(s)."
Escrever-Log "Identidade: $(Get-IdentidadeAtual) | acesso a rede como: $env:USERDOMAIN\$env:COMPUTERNAME`$ (quando SYSTEM)"

while ($true) {
    try {
        Executar-Ciclo
    } catch {
        Escrever-Log "Erro geral no ciclo: $($_.Exception.Message)" 'ERROR'
    }
    Start-Sleep -Seconds ($intervalo * 60)
}
