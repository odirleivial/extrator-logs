# testar_download_logs.py
# Exercita o caminho "Download" da Solicitacao de Logs, que troca o e-mail pelo
# R2: BEC enfileira -> agente sobe o ZIP -> BEC baixa.
#
#   python scripts\testar_download_logs.py
#
# Nao toca em PDV real: usa a fila ficticia 9999/999 e um ZIP sintetico, no lugar
# da extracao. Requer o worker com as rotas /arquivo/<pid> (R2) publicado.
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'server_agent'))

LOJA, PDV = '9999', '999'
falhas = []


def checar(cond, desc, detalhe=''):
    print(('  [OK]    ' if cond else '  [FALHA] ') + desc + ('' if cond or not detalhe else f' -> {detalhe}'))
    if not cond:
        falhas.append(desc)


def ler_prop(nome):
    with open(os.path.join(RAIZ, 'properties', 'config.properties'), encoding='utf-8') as f:
        for linha in f:
            if linha.startswith(nome + '='):
                return linha.split('=', 1)[1].strip()
    return ''


URL   = ler_prop('bec_tunnel_url').rstrip('/')
TOKEN = ler_prop('pinpad_tunnel_token')
H_BASE = {'X-Token': TOKEN,
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def relay(metodo, endpoint, corpo=None, binario=False):
    dados = corpo if binario else (json.dumps(corpo).encode() if corpo is not None else None)
    h = dict(H_BASE)
    if dados is not None:
        h['Content-Type'] = 'application/octet-stream' if binario else 'application/json'
    req = urllib.request.Request(f'{URL}{endpoint}', data=dados, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            bruto = r.read()
            if binario or metodo == 'GET' and endpoint.startswith('/arquivo/'):
                return r.status, bruto
            return r.status, (json.loads(bruto) if bruto else None)
    except urllib.error.HTTPError as e:
        # O corpo do erro importa: e como se distingue "rota inexistente" de
        # "arquivo inexistente", que respondem o mesmo 404.
        try:
            return e.code, json.loads(e.read() or b'{}')
        except Exception:
            return e.code, None


# ---------------------------------------------------------------------------
# Parte offline: o ramo de entrega dentro do agente. Roda sem rede, entao vale
# mesmo quando o worker com R2 ainda nao foi publicado.
# ---------------------------------------------------------------------------
import tempfile
import agent_extrator_log as agente

print('=' * 70)
print('0. AGENTE: ramo de entrega (sem rede)')
print('=' * 70)

pasta = tempfile.mkdtemp(prefix='logteste_')
log_falso = os.path.join(pasta, 'CSIDebugFile.txt')
with open(log_falso, 'w', encoding='utf-8') as f:
    f.write(('conteudo de log de teste' + chr(10)) * 100)

# Sem IP para o PDV, o agente usa o caminho do properties como caminho local —
# e o que permite exercitar a extracao inteira sem tocar em PDV de verdade.
props_falso = {
    'CSIDebugFile': log_falso,
    'versaoPDV': log_falso,
    'windows_user': '', 'windows_senha': '',
    'bec_tunnel_url': 'https://exemplo.invalido', 'pinpad_tunnel_token': 'x',
}

chamadas = {'email': 0, 'upload': 0}
_email_orig  = agente.enviar_email_com_anexo
_upload_orig = agente._subir_arquivo_para_r2
agente.enviar_email_com_anexo = lambda *a, **k: chamadas.__setitem__('email', chamadas['email'] + 1)
agente._subir_arquivo_para_r2 = lambda *a, **k: (chamadas.__setitem__('upload', chamadas['upload'] + 1), (True, ''))[1]

def corpo_para(entrega, pid):
    linhas = [f'PID: {pid}', 'Usuario: teste', 'Destino: ninguem@exemplo.com',
              'Loja: 0045', 'PDV: 450', 'Logs: CSIDebugFile', 'Data: ']
    if entrega:
        linhas.append(f'Entrega: {entrega}')
    return chr(10).join(linhas)

try:
    # --- entrega por download: sobe o arquivo, NAO manda e-mail ---
    extra = agente.processar_solicitacao_log(
        None, None, corpo_para('download', 'DLTESTE01'), props_falso, '', '')
    checar(chamadas['upload'] == 1, 'download: chamou o upload uma vez', str(chamadas))
    checar(chamadas['email'] == 0, 'download: NAO enviou e-mail', str(chamadas))
    checar(isinstance(extra, dict), 'download: devolveu metadados', str(type(extra)))
    if isinstance(extra, dict):
        checar(extra.get('arquivo', '').endswith('.zip'), 'metadados trazem o nome do zip', str(extra.get('arquivo')))
        checar(extra.get('tamanho', 0) > 0, 'metadados trazem o tamanho', str(extra.get('tamanho')))
        checar(len(extra.get('sha256', '')) == 64, 'metadados trazem sha256', str(extra.get('sha256'))[:16])
        checar(extra.get('incluidos') == 1, 'resumo: 1 arquivo incluido', str(extra.get('incluidos')))

    # --- entrega por e-mail (padrao): mantem o comportamento historico ---
    chamadas['email'] = chamadas['upload'] = 0
    extra2 = agente.processar_solicitacao_log(
        None, None, corpo_para(None, 'DLTESTE02'), props_falso, '', '')
    checar(chamadas['email'] == 1, 'sem Entrega: enviou e-mail (comportamento de sempre)', str(chamadas))
    checar(chamadas['upload'] == 0, 'sem Entrega: nao subiu nada para o R2', str(chamadas))
    checar(extra2 is None, 'sem Entrega: nao devolve metadados', str(extra2))
finally:
    agente.enviar_email_com_anexo = _email_orig
    agente._subir_arquivo_para_r2 = _upload_orig
    import shutil as _sh
    _sh.rmtree(pasta, ignore_errors=True)
    for f in os.listdir(agente.BASE_DIR):
        if f.startswith('LOG-0045-450-') and f.endswith('.zip'):
            try: os.remove(os.path.join(agente.BASE_DIR, f))
            except Exception: pass

print()
print('=' * 70)
print('PRE-REQUISITOS')
print('=' * 70)
st, _ = relay('GET', '/status')
checar(st == 200, 'relay responde /status', f'HTTP {st}')
if st != 200:
    print('\nRelay inacessivel — nada a testar.')
    sys.exit(2)

# Cuidado: a rota inexistente TAMBEM responde 404. Para saber se o worker
# publicado ja tem /arquivo, e preciso olhar o corpo: 'Rota nao encontrada'
# significa worker antigo; 'Arquivo nao encontrado' significa rota presente.
st, corpo = relay('GET', '/arquivo/__inexistente__')
erro_txt = (corpo or {}).get('erro', '') if isinstance(corpo, dict) else ''

if st == 503:
    print()
    print('  [FALHA] a rota /arquivo existe, mas o bucket R2 nao esta ligado.')
    print('          Cloudflare -> Workers & Pages -> bec-relay -> Settings ->')
    print('          Bindings -> R2 Bucket: variavel R2, bucket bec-relay-arquivos')
    sys.exit(2)
if 'Rota' in erro_txt:
    print()
    print('  [FALHA] o worker publicado nao tem a rota /arquivo (R2).')
    print(f'          resposta: HTTP {st} {erro_txt}')
    print()
    print('          Suba cloudflare_worker/worker.js e crie o bucket:')
    print('            1. R2 -> Create bucket -> bec-relay-arquivos')
    print('            2. Workers & Pages -> bec-relay -> Settings -> Bindings ->')
    print('               Add R2 bucket: variavel R2 -> bec-relay-arquivos')
    print('            3. Edit code -> colar worker.js -> Deploy')
    print()
    print('          Nada foi testado.')
    sys.exit(2)
checar(st == 404, 'rota /arquivo publicada e bucket R2 ligado', f'HTTP {st} {erro_txt}')

print()
print('=' * 70)
print('1. BEC: botao Download enfileira com Entrega=download')
print('=' * 70)

import extrator_logs

_config_real = extrator_logs.ler_config_completo


def _config_teste(*a, **k):
    props = dict(_config_real(*a, **k))
    props['logs_modo_comunicacao'] = 'tunnel'
    props['bec_loja'] = LOJA
    props['bec_pdv']  = PDV
    return props


extrator_logs.ler_config_completo = _config_teste
cliente = extrator_logs.app.test_client()

# limpa a fila
for _ in range(20):
    s_, it = relay('GET', f'/pendente/{LOJA}/{PDV}')
    if s_ != 200 or not it:
        break
    relay('POST', f"/resultado/{it.get('pid', '')}", {'sucesso': True, 'mensagem': 'limpeza'})

r = cliente.post('/solicitar', data={
    'loja': '0045', 'pdv': '450', 'logs': ['CSIDebugFile'],
    'email_destino': 'ovm.extrator.logs@gmail.com',
    'data': '2026-08-18', 'entrega': 'download',
})
j = r.get_json() or {}
print(f'  HTTP {r.status_code} | {j.get("mensagem", "")}')
checar(r.status_code == 200 and j.get('sucesso'), 'rota aceitou o pedido de download')
checar(j.get('entrega') == 'download', "resposta indica entrega=download", str(j.get('entrega')))
pid = j.get('pid', '')

st, item = relay('GET', f'/pendente/{LOJA}/{PDV}')
checar(st == 200 and item is not None, 'pedido chegou na fila', f'HTTP {st}')
if item:
    checar(item.get('entrega') == 'download', "payload traz entrega=download", str(item.get('entrega')))
    checar('Entrega: download' in item.get('corpo', ''),
           'corpo chave/valor traz "Entrega: download"')

    import agent_extrator_log as agente
    checar(agente.extrair_campo(item['corpo'], 'Entrega') == 'download',
           'agente extrai Entrega do corpo')

print()
print('=' * 70)
print('2. AGENTE: sobe o ZIP para o R2 (upload binario, sem base64)')
print('=' * 70)
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('CSIDebugFile.txt', b'linha de log de teste\n' * 2000)
zip_bytes = buf.getvalue()
print(f'  ZIP sintetico: {len(zip_bytes)} bytes')

import agent_extrator_log as agente
import hashlib
tmp = os.path.join(RAIZ, 'output', f'TESTE-{pid}.zip')
os.makedirs(os.path.dirname(tmp), exist_ok=True)
with open(tmp, 'wb') as f:
    f.write(zip_bytes)

ok, erro = agente._subir_arquivo_para_r2(_config_teste(), pid, tmp)
checar(ok, 'agente subiu o arquivo pelo relay', erro)

st, baixado = relay('GET', f'/arquivo/{pid}')
checar(st == 200, 'arquivo legivel no R2', f'HTTP {st}')
if st == 200:
    checar(baixado == zip_bytes, 'bytes identicos aos enviados',
           f'{len(baixado)} vs {len(zip_bytes)}')

print()
print('=' * 70)
print('3. AGENTE: publica o resultado com os metadados')
print('=' * 70)
meta = {
    'sucesso': True, 'mensagem': 'Solicitar Logs executada pelo agente',
    'arquivo': f'LOG-0045-450-{pid}.zip', 'tamanho': len(zip_bytes),
    'sha256': hashlib.sha256(zip_bytes).hexdigest(),
    'incluidos': 1, 'faltando': 0,
}
st, _ = relay('POST', f'/resultado/{pid}', meta)
checar(st == 200, 'resultado publicado no relay', f'HTTP {st}')

print()
print('=' * 70)
print('4. BEC: status fica "pronto" e o polling e repetivel')
print('=' * 70)
r1 = cliente.get(f'/solicitar/status/{pid}').get_json() or {}
print(f'  1a consulta: {json.dumps(r1, ensure_ascii=False)}')
checar(r1.get('pronto') is True, 'status pronto=True')
checar(r1.get('sucesso') is True, 'status sucesso=True')
checar(r1.get('arquivo') == meta['arquivo'], 'nome do arquivo veio no status')
checar(r1.get('tamanho') == len(zip_bytes), 'tamanho veio no status')

# A leitura no relay consome; o BEC precisa guardar para o polling nao perder
r2 = cliente.get(f'/solicitar/status/{pid}').get_json() or {}
checar(r2.get('pronto') is True,
       'segunda consulta ainda responde pronto (resultado memorizado no BEC)',
       json.dumps(r2, ensure_ascii=False))

print()
print('=' * 70)
print('5. BEC: download entrega o arquivo e limpa o bucket')
print('=' * 70)
resp = cliente.get(f'/solicitar/baixar/{pid}')
checar(resp.status_code == 200, 'rota de download respondeu 200', f'HTTP {resp.status_code}')
checar(resp.data == zip_bytes, 'conteudo entregue e identico ao gerado',
       f'{len(resp.data)} vs {len(zip_bytes)}')
disp = resp.headers.get('Content-Disposition', '')
checar('attachment' in disp and meta['arquivo'] in disp,
       'vai como anexo com o nome certo', disp)

st, _ = relay('GET', f'/arquivo/{pid}')
checar(st == 404, 'arquivo removido do R2 apos o download (nao ocupa espaco)', f'HTTP {st}')

print()
print('=' * 70)
print('6. LIMPEZA')
print('=' * 70)
for caminho in (tmp, os.path.join(RAIZ, 'output', meta['arquivo'])):
    if os.path.exists(caminho):
        os.remove(caminho)
        print(f'  removido {os.path.basename(caminho)}')
for _ in range(20):
    s_, it = relay('GET', f'/pendente/{LOJA}/{PDV}')
    if s_ != 200 or not it:
        break
    relay('POST', f"/resultado/{it.get('pid', '')}", {'sucesso': True, 'mensagem': 'limpeza'})
print('  fila drenada')

print()
print('=' * 70)
if falhas:
    print(f'RESULTADO: {len(falhas)} FALHA(S)')
    for f in falhas:
        print(f'  - {f}')
    sys.exit(1)
print('RESULTADO: o caminho de download pelo R2 funciona ponta a ponta')
print('=' * 70)
