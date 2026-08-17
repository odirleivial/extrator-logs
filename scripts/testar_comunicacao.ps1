# testar_comunicacao.ps1
# Diagnostico de conectividade BEC <-> agentes, para avaliar a troca do e-mail
# por comunicacao direta.
#
# Rodar nas quatro combinacoes:
#   - maquina do tester (lado BEC), DENTRO e FORA da VPN
#   - maquina do agente de PDV (rede da loja)
#   - maquina do agente SP (rede de SP)
#
#   powershell -ExecutionPolicy Bypass -File testar_comunicacao.ps1
#
# Em maquina onde a ExecutionPolicy vem de GPO (o -ExecutionPolicy da linha de
# comando NAO vence GPO), use o lancador que dispensa arquivo .ps1:
#
#   testar_comunicacao.bat
#
# O arquivo de propriedades e localizado automaticamente (config.properties do
# BEC ou agent.properties do agente). Para apontar outro, defina antes:
#   $Config = 'C:\caminho\agent.properties'
# ou a variavel de ambiente BEC_CONFIG.
#
# Nenhum comando e enviado a dispositivo real: o round-trip usa a loja/PDV
# ficticia 9999/999.

# Sem param() de proposito: assim o mesmo codigo roda como arquivo .ps1 e
# tambem via Invoke-Expression / stdin, que e o caminho usado quando a
# ExecutionPolicy da maquina bloqueia scripts.
$ErrorActionPreference = 'Continue'

if (-not $Config) { $Config = $env:BEC_CONFIG }

# $PSScriptRoot fica vazio quando o codigo e executado via iex/stdin; nesse caso
# o lancador .bat define $aqui, e so caimos no diretorio atual se nem isso houver
if (-not $aqui) {
    $aqui = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
}
$raiz = Split-Path -Parent $aqui           # scripts/ -> raiz do projeto
if (-not $raiz) { $raiz = $aqui }

# Fallback quando o properties da maquina nao tem as chaves do relay (e o caso
# do agent.properties do agente SP, que nunca usou o modo tunnel)
$URL_RELAY_PADRAO = 'https://bec-relay.odirleivial.workers.dev'

# ------------------------------------------------- localiza o arquivo de config
if (-not $Config) {
    $candidatos = @(
        # rodando de dentro do projeto
        (Join-Path $raiz 'properties\config.properties'),
        (Join-Path $raiz 'server_agent\agent.properties'),
        (Join-Path $raiz 'server_agent_sp\agent.properties'),
        # rodando de uma maquina instalada (script copiado solto)
        (Join-Path $aqui 'agent.properties'),
        (Join-Path $aqui 'config.properties'),
        'C:\AgentExtratarLog\agent.properties',
        'C:\ServerAgentSP\agent.properties',
        "$env:LOCALAPPDATA\BEC\properties\config.properties"
    )
    $existentes = $candidatos | Where-Object { Test-Path $_ }
    # Prefere um properties que realmente tenha as chaves do relay; so entao
    # cai no primeiro que existir (senao o bloco 2 seria pulado sem necessidade)
    $Config = $existentes |
              Where-Object { Select-String -Path $_ -Pattern '^bec_tunnel_url=' -Quiet } |
              Select-Object -First 1
    if (-not $Config) { $Config = $existentes | Select-Object -First 1 }
}

function Prop($nome) {
    if (-not $Config) { return '' }
    $linha = Get-Content $Config -ErrorAction SilentlyContinue | Select-String "^$nome="
    if ($linha) { return $linha.ToString().Substring($nome.Length + 1).Trim() }
    return ''
}

function Titulo($t) { ""; "=" * 70; $t; "=" * 70 }

# ---------------------------------------------------------------- estado da rede
Titulo '1. ESTADO DA REDE DESTA MAQUINA'

"Maquina : $env:COMPUTERNAME"
"PS      : $($PSVersionTable.PSVersion)  |  ExecutionPolicy efetiva: $(Get-ExecutionPolicy)"
if ($Config) { "Config  : $Config" } else { "Config  : NAO ENCONTRADO - defina `$Config ou BEC_CONFIG" }
""

Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize | Out-String -Width 120

$rotas10 = Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.DestinationPrefix -like '10.*' }
$naVpn = [bool]$rotas10

if ($naVpn) {
    "ESTADO: rotas para 10.x presentes (DENTRO da VPN, ou a propria rede interna)"
    $rotas10 | Select-Object DestinationPrefix, InterfaceAlias | Format-Table -AutoSize | Out-String -Width 120
} else {
    "ESTADO: FORA da VPN (nenhuma rota para 10.x)"
}

# ------------------------------------------------- via 1: relay publico (Worker)
Titulo '2. VIA RELAY PUBLICO (Cloudflare Worker) - independe de VPN'

$url = (Prop 'bec_tunnel_url').TrimEnd('/')
$tok = Prop 'pinpad_tunnel_token'
if (-not $tok) { $tok = $env:BEC_TOKEN }

if (-not $url) {
    $url = $URL_RELAY_PADRAO
    "AVISO: bec_tunnel_url ausente no properties - usando a URL padrao do relay."
}

# Header apenas quando ha token: em PS 5.1 um header de valor vazio faz o
# Invoke-WebRequest estourar NullReferenceException antes de sair da maquina,
# o que faria o teste reportar "inalcancavel" sem nem tentar a conexao
$H = if ($tok) { @{ 'X-Token' = $tok } } else { @{} }
"URL: $url"
if (-not $tok) {
    "AVISO: token ausente. O /status deve responder 401 - e isso ja resolve a"
    "       pergunta principal, porque um 401 prova que a requisicao CHEGOU no"
    "       relay. O round-trip completo fica de fora (defina BEC_TOKEN p/ ele)."
}

# 2a. health check - alcance do relay
$sw = [Diagnostics.Stopwatch]::StartNew()
$alcancou = $false
try {
    $r = Invoke-WebRequest -Uri "$url/status" -Headers $H -TimeoutSec 15 -UseBasicParsing
    $sw.Stop()
    $alcancou = $true
    "  [OK]    GET /status -> HTTP $($r.StatusCode) em $($sw.ElapsedMilliseconds)ms  $($r.Content)"
} catch {
    $sw.Stop()
    $codigo = $null
    $motivo = $null
    try { $codigo = $_.Exception.Response.StatusCode.value__ } catch {}
    try { $motivo = $_.Exception.Status.ToString() } catch {}

    if ($codigo) {
        # Respondeu HTTP (ainda que 401/403): a rede LIBERA a saida
        $alcancou = $true
        "  [OK]    GET /status -> HTTP $codigo em $($sw.ElapsedMilliseconds)ms"
        "          RELAY ALCANCAVEL: houve resposta HTTP, logo a rede libera"
        "          saida para este dominio."
    } else {
        "  [FALHA] GET /status -> $($_.Exception.Message)"
        if ($motivo) { "          Motivo (WebException.Status): $motivo" }
        "          RELAY INALCANCAVEL (sem resposta HTTP). Este e o teste"
        "          decisivo: a rede desta maquina nao libera HTTPS de saida"
        "          para *.workers.dev, ou exige proxy."
        "          Conferir proxy      : netsh winhttp show proxy"
        "          Conferir resolucao  : nslookup bec-relay.odirleivial.workers.dev"
        "          Testar porta 443    : Test-NetConnection bec-relay.odirleivial.workers.dev -Port 443"
    }
}

if (-not $tok -or -not $alcancou) {
    "  Round-trip completo nao executado (falta token, ou relay inalcancavel)."
} else {
    # 2b. round-trip completo com loja/PDV ficticia
    $p = 'DIAG' + (Get-Random -Minimum 10000 -Maximum 99999)
    $corpo = @{ pid = $p; comando = 'DIAGNOSTICO'; porta = 'COM99' } | ConvertTo-Json -Compress

    "  Round-trip com loja/PDV ficticia 9999/999, PID=$p"
    try {
        $r = Invoke-WebRequest -Uri "$url/comando/9999/999" -Method POST -Headers $H `
             -ContentType 'application/json' -Body $corpo -TimeoutSec 20 -UseBasicParsing
        "    [OK]    BEC envia comando         -> HTTP $($r.StatusCode)"
    } catch { "    [FALHA] BEC envia comando         -> $($_.Exception.Message)" }

    try {
        $r = Invoke-WebRequest -Uri "$url/pendente/9999/999" -Headers $H -TimeoutSec 20 -UseBasicParsing
        "    [OK]    agente busca pendente     -> HTTP $($r.StatusCode)  $($r.Content)"
    } catch { "    [FALHA] agente busca pendente     -> HTTP $($_.Exception.Response.StatusCode.value__)" }

    try {
        $r = Invoke-WebRequest -Uri "$url/resultado/$p" -Method POST -Headers $H `
             -ContentType 'application/json' `
             -Body (@{ sucesso = $true; mensagem = 'diagnostico' } | ConvertTo-Json -Compress) `
             -TimeoutSec 20 -UseBasicParsing
        "    [OK]    agente devolve resultado  -> HTTP $($r.StatusCode)"
    } catch { "    [FALHA] agente devolve resultado  -> $($_.Exception.Message)" }

    try {
        $r = Invoke-WebRequest -Uri "$url/resultado/$p" -Headers $H -TimeoutSec 20 -UseBasicParsing
        "    [OK]    BEC le resultado          -> HTTP $($r.StatusCode)  $($r.Content)"
    } catch { "    [FALHA] BEC le resultado          -> HTTP $($_.Exception.Response.StatusCode.value__)" }

    # 2c. autenticacao
    try {
        $r = Invoke-WebRequest -Uri "$url/status" -TimeoutSec 15 -UseBasicParsing
        "  [ATENCAO] /status respondeu HTTP $($r.StatusCode) SEM token"
    } catch {
        "  [OK]    sem token -> HTTP $($_.Exception.Response.StatusCode.value__) (recusado, correto)"
    }

    # 2d. limpeza da fila ficticia
    $limpos = 0
    for ($i = 1; $i -le 20; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "$url/pendente/9999/999" -Headers $H -TimeoutSec 30 -UseBasicParsing
            if ($r.StatusCode -eq 204) { break }
            $limpos++
        } catch { break }
    }
    "  Limpeza da fila ficticia: $limpos residuo(s) removido(s)"
}

# ------------------------------------------- perfil de saida para a internet
Titulo '2B. PERFIL DE SAIDA PARA A INTERNET'

@"
  Se o bloco 2 falhou, este bloco diz o FORMATO da restricao. O agente de hoje
  manda e-mail por SMTP do Gmail, entao alguma saida existe: comparar SMTP com
  HTTPS mostra se o bloqueio e por dominio, por porta, ou total.
"@

# 2b.1 resolucao DNS do relay
$hostRelay = try { ([Uri]$url).Host } catch { 'bec-relay.odirleivial.workers.dev' }
try {
    $ips = [System.Net.Dns]::GetHostAddresses($hostRelay) |
           Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
           ForEach-Object { $_.IPAddressToString }
    if ($ips) { "  DNS   $hostRelay -> $($ips -join ', ')  [resolve OK]" }
    else      { "  DNS   $hostRelay -> sem registro A" }
} catch {
    "  DNS   $hostRelay -> FALHA NA RESOLUCAO ($($_.Exception.Message))"
    "        DNS quebrado ja explica o ConnectFailure do bloco 2."
}

# 2b.2 comparacao de portas/destinos
$egress = @(
    @{ nome = 'smtp.gmail.com (o agente usa hoje)'; alvo = 'smtp.gmail.com';  porta = 587 },
    @{ nome = 'smtp.gmail.com SSL';                 alvo = 'smtp.gmail.com';  porta = 465 },
    @{ nome = 'relay do BEC (Cloudflare)';          alvo = $hostRelay;        porta = 443 },
    @{ nome = 'HTTPS generico (google.com)';        alvo = 'google.com';      porta = 443 },
    @{ nome = 'HTTPS generico (cloudflare.com)';    alvo = 'cloudflare.com';  porta = 443 },
    @{ nome = 'DNS publico Google';                 alvo = '8.8.8.8';         porta = 53  }
)

foreach ($e in $egress) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $cli = New-Object System.Net.Sockets.TcpClient
    $ok = $false
    try { $ok = $cli.ConnectAsync($e.alvo, $e.porta).Wait(4000) } catch {}
    $sw.Stop()
    try { $cli.Close() } catch {}
    $res = if ($ok) { 'LIBERADO   ' } else { 'bloqueado  ' }
    "  {0,-36} :{1,-5} {2} ({3}ms)" -f $e.nome, $e.porta, $res, $sw.ElapsedMilliseconds
}

# 2b.3 proxy configurado
""
"  Proxy WinHTTP : " + ((netsh winhttp show proxy 2>&1 | Select-String -Pattern 'Proxy|proxy' | ForEach-Object { $_.ToString().Trim() }) -join ' | ')
try {
    $k = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
    "  Proxy do IE   : ProxyEnable=$($k.ProxyEnable)  ProxyServer=$($k.ProxyServer)  AutoConfigURL=$($k.AutoConfigURL)"
} catch {
    "  Proxy do IE   : nao foi possivel ler o registro"
}

@"

  Como ler:
   - SMTP liberado e HTTPS do relay bloqueado -> bloqueio por DOMINIO/destino.
     Pedir liberacao do host do relay no firewall/proxy resolve, e e um pedido
     pequeno (um dominio, porta 443).
   - Todo HTTPS bloqueado e so SMTP liberado  -> a saida e whitelist restrita.
     Melhor caminho: relay hospedado DENTRO da rede da empresa.
   - Proxy configurado -> a saida existe mas exige passar pelo proxy; o agente
     precisaria mandar as chamadas com -Proxy / WebProxy.
"@

# --------------------------------------------- via 2: acesso direto as redes
Titulo '3. ACESSO DIRETO AS REDES DOS AGENTES - depende de VPN'

$alvos = @(
    @{ rede = 'Loja (PDVs)'; nome = 'PDV 450 / maquina do agente'; ip = '10.56.90.14';   portas = @(445, 4000, 8080) },
    @{ rede = 'Loja (PDVs)'; nome = 'PDV 277';                     ip = '10.56.90.10';   portas = @(445, 4000) },
    @{ rede = 'SP';          nome = 'Proctrans / Tesouraria';       ip = '10.56.62.140';  portas = @(445, 4003) },
    @{ rede = 'SP';          nome = 'Wildfly / webservices';        ip = '10.56.62.152';  portas = @(445, 8080) },
    @{ rede = 'Servicos';    nome = 'SiTef';                        ip = '10.206.112.34'; portas = @(4096) }
)

foreach ($a in $alvos) {
    foreach ($porta in $a.portas) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $cli = New-Object System.Net.Sockets.TcpClient
        $ok = $false
        try { $ok = $cli.ConnectAsync($a.ip, $porta).Wait(3000) } catch {}
        $sw.Stop()
        try { $cli.Close() } catch {}
        $res = if ($ok) { 'ALCANCAVEL  ' } else { 'inalcancavel' }
        "  [{0,-11}] {1,-30} {2,-15} :{3,-5} {4} ({5}ms)" -f $a.rede, $a.nome, $a.ip, $porta, $res, $sw.ElapsedMilliseconds
    }
}

# ---------------------------------------------------------------- conclusao
Titulo '4. LEITURA DO RESULTADO'

@"
  O que cada bloco responde:

  Bloco 2 OK   -> esta maquina alcanca o relay. Se der OK nas duas maquinas de
                  agente E na maquina do tester fora da VPN, a comunicacao sem
                  e-mail e viavel para todos os cenarios.
  Bloco 2 FALHA-> a rede desta maquina bloqueia saida para *.workers.dev. Seria
                  preciso liberar o dominio no proxy/firewall, ou usar um host
                  proprio da empresa como relay.
  Bloco 3      -> so fica ALCANCAVEL dentro da VPN (ou rodando de dentro da
                  propria rede interna). Serve para medir o que a comunicacao
                  direta por IP cobriria, e o que ela deixa de fora.
"@

if ($naVpn) {
    "  Esta execucao teve rotas 10.x. Rode tambem FORA da VPN para comparar."
} else {
    "  Esta execucao foi fora da VPN. Rode tambem DENTRO da VPN para comparar."
}
""
