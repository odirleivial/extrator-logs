# ===========================================================================
# Teste de viabilidade: o powershell.exe consegue falar com o Gmail?
#
# Roda IMAP (login + busca) e SMTP (envio com anexo) usando apenas
# powershell.exe, que e assinado pela Microsoft. Se este teste passar e o
# server_agent_sp.exe continuar com WinError 10013, esta confirmado que o
# bloqueio e por processo (SentinelOne) e a solucao e reescrever o agente
# em PowerShell.
#
# Uso (prompt como Administrador, na pasta do agente):
#   powershell -ExecutionPolicy Bypass -File teste_powershell_gmail.ps1
# ===========================================================================

$ErrorActionPreference = 'Continue'
$props = @{}

# Procura o agent.properties sem depender de $PSScriptRoot — assim o script
# tambem funciona quando executado via pipe (executar_teste.bat), que e a forma
# de rodar quando a politica de execucao do dominio bloqueia arquivos .ps1.
$candidatos = @(
    (Join-Path (Get-Location) 'agent.properties'),
    'C:\ServerAgentSP\agent.properties',
    'C:\ServerAgentSP_instalacao\agent.properties'
)
$arqProps = $candidatos | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $arqProps) {
    Write-Host "[ERRO] agent.properties nao encontrado. Rode este teste na pasta do agente." -ForegroundColor Red
    exit 1
}
Write-Host "Configuracao: $arqProps"
Get-Content $arqProps | ForEach-Object {
    $linha = $_.Trim()
    if ($linha -and -not $linha.StartsWith('#') -and $linha.Contains('=')) {
        $i = $linha.IndexOf('=')
        $props[$linha.Substring(0, $i).Trim()] = $linha.Substring($i + 1).Trim()
    }
}
$usuario = $props['email']
$senha   = $props['senha']

Write-Host "============================================================"
Write-Host " Teste PowerShell -> Gmail   (conta: $usuario)"
Write-Host "============================================================"

# ---- 1. IMAP: conexao TLS + LOGIN + SEARCH UNSEEN ------------------------
Write-Host "`n[1/2] IMAP imap.gmail.com:993"
try {
    $cliente = New-Object System.Net.Sockets.TcpClient('imap.gmail.com', 993)
    $ssl = New-Object System.Net.Security.SslStream($cliente.GetStream(), $false)
    $ssl.AuthenticateAsClient('imap.gmail.com')
    $leitor  = New-Object System.IO.StreamReader($ssl)
    $escritor = New-Object System.IO.StreamWriter($ssl)
    $escritor.AutoFlush = $true

    $saudacao = $leitor.ReadLine()
    Write-Host "      servidor: $saudacao"

    $escritor.WriteLine("a1 LOGIN $usuario $senha")
    do { $resposta = $leitor.ReadLine() } while ($resposta -notmatch '^a1 ')
    if ($resposta -match '^a1 OK') {
        Write-Host "      [OK] LOGIN aceito" -ForegroundColor Green
    } else {
        Write-Host "      [FALHA] LOGIN: $resposta" -ForegroundColor Red
    }

    $escritor.WriteLine('a2 SELECT INBOX')
    do { $resposta = $leitor.ReadLine() } while ($resposta -notmatch '^a2 ')

    $escritor.WriteLine('a3 SEARCH UNSEEN')
    $naoLidos = ''
    do {
        $resposta = $leitor.ReadLine()
        if ($resposta -match '^\* SEARCH(.*)$') { $naoLidos = $matches[1].Trim() }
    } while ($resposta -notmatch '^a3 ')
    $qtd = if ($naoLidos) { ($naoLidos -split '\s+').Count } else { 0 }
    Write-Host "      [OK] $qtd e-mail(s) nao lido(s) na caixa" -ForegroundColor Green

    $escritor.WriteLine('a4 LOGOUT')
    $cliente.Close()
    Write-Host "      IMAP OK" -ForegroundColor Green
} catch {
    Write-Host "      [FALHA] IMAP -> $($_.Exception.Message)" -ForegroundColor Red
}

# ---- 2. SMTP: envio com anexo -------------------------------------------
Write-Host "`n[2/2] SMTP smtp.gmail.com:587 (envio com anexo)"
try {
    $anexo = Join-Path $env:TEMP 'teste_agente_sp.txt'
    "Teste de envio do Server Agent SP via PowerShell em $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')" |
        Out-File -FilePath $anexo -Encoding utf8

    $cred = New-Object System.Management.Automation.PSCredential(
        $usuario, (ConvertTo-SecureString $senha -AsPlainText -Force))

    Send-MailMessage -From $usuario -To 'ovmachado@ext.leroymerlin.com.br' `
        -Subject '[Teste PowerShell] Server Agent SP' `
        -Body 'Se voce recebeu este e-mail, o powershell.exe consegue enviar pelo Gmail neste servidor.' `
        -SmtpServer 'smtp.gmail.com' -Port 587 -UseSsl -Credential $cred `
        -Attachments $anexo -Encoding UTF8

    Remove-Item $anexo -Force -ErrorAction SilentlyContinue
    Write-Host "      [OK] E-mail enviado para ovmachado@ext.leroymerlin.com.br" -ForegroundColor Green
} catch {
    Write-Host "      [FALHA] SMTP -> $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n============================================================"
Write-Host " Se os dois passaram, o caminho e reescrever o agente em"
Write-Host " PowerShell (powershell.exe e confiavel para o SentinelOne)."
Write-Host "============================================================"
