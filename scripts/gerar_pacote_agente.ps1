# gerar_pacote_agente.ps1
# Compila o Agent Extrator de Log e monta o AgentExtratarLog_instalacao.zip na
# raiz do projeto — o pacote que a aba Administrador do BEC envia aos agentes
# na atualizacao automatica.
#
#   powershell -ExecutionPolicy Bypass -File scripts\gerar_pacote_agente.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\gerar_pacote_agente.ps1 -SemCompilar
#
# O ZIP tem de ser PLANO (sem subpastas): o atualizacao_agente.py do BEC nao
# inspeciona ZIPs aninhados, e o instalar_servico.bat espera os arquivos ao lado
# do exe. Manter esta lista igual a do pacote em uso.

param(
    [switch]$SemCompilar
)

$ErrorActionPreference = 'Stop'

$raiz    = Split-Path -Parent $PSScriptRoot
$agente  = Join-Path $raiz 'server_agent'
$destino = Join-Path $raiz 'AgentExtratarLog_instalacao.zip'

# Arquivos que compoem o pacote, na ordem em que aparecem no ZIP atual
$conteudo = @(
    'agent.properties',
    'agent_extrator_log.exe',
    'iniciar_servico.bat',
    'instalar_servico.bat',
    'nssm.exe',
    'parar_servico.bat',
    'remover_servico.bat'
)

Write-Host '======================================================================'
Write-Host ' Pacote de instalacao do Agent Extrator de Log'
Write-Host '======================================================================'

# ------------------------------------------------------------------ compilacao
if (-not $SemCompilar) {
    Write-Host 'Compilando agent_extrator_log.py com PyInstaller...'
    Push-Location $agente
    try {
        python -m PyInstaller --onefile --console --name agent_extrator_log `
               agent_extrator_log.py --distpath dist --workpath build | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller retornou $LASTEXITCODE" }
        Copy-Item 'dist\agent_extrator_log.exe' 'agent_extrator_log.exe' -Force
    } finally {
        Pop-Location
    }
    Write-Host '  [OK] exe compilado e copiado para server_agent\'
} else {
    Write-Host 'Compilacao ignorada (-SemCompilar): usando o exe que ja esta em server_agent\'
}

# --------------------------------------------------------- versao para conferir
$versao = 'desconhecida'
$linha = Get-Content (Join-Path $agente 'version.py') | Select-String '^__version__'
if ($linha) { $versao = ($linha.ToString() -split '"')[1] }
Write-Host "Versao do agente: $versao"

# ------------------------------------------------------------------ validacao
$faltando = @()
foreach ($arq in $conteudo) {
    if (-not (Test-Path (Join-Path $agente $arq))) { $faltando += $arq }
}
if ($faltando.Count -gt 0) {
    Write-Host "[ERRO] Arquivos ausentes em server_agent\: $($faltando -join ', ')"
    exit 1
}

# --------------------------------------------------------------- monta o ZIP
if (Test-Path $destino) { Remove-Item $destino -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$zip = [System.IO.Compression.ZipFile]::Open($destino, 'Create')
try {
    foreach ($arq in $conteudo) {
        $origem = Join-Path $agente $arq
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $origem, $arq, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        $kb = [math]::Round((Get-Item $origem).Length / 1KB, 1)
        Write-Host ("  + {0,-24} {1,10} KB" -f $arq, $kb)
    }
} finally {
    $zip.Dispose()
}

$mb = [math]::Round((Get-Item $destino).Length / 1MB, 2)
Write-Host '----------------------------------------------------------------------'
Write-Host "[OK] Pacote gerado: $destino  ($mb MB)"
Write-Host ''
Write-Host 'Proximo passo: aba Administrador do BEC -> Atualizar Agente,'
Write-Host 'selecionando este ZIP.'
Write-Host '  - modo relay (padrao p/ o Agent Extrator): o ZIP vai como esta,'
Write-Host '    em base64. Limite de 18 MB.'
Write-Host '  - modo e-mail (sempre no Server Agent SP): o BEC renomeia as'
Write-Host '    entradas .exe, que o Gmail recusa, e o agente desfaz ao aplicar.'
Write-Host '======================================================================'
