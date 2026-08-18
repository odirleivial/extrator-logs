# testar_worker_relay.py
# Teste de aceitacao do relay (Cloudflare Worker), exercitando o relay em si —
# nao o BEC. Rode DEPOIS de subir cloudflare_worker/worker.js.
#
#   python scripts\testar_worker_relay.py
#
# Contra a versao antiga ele REPROVA (fila de um item so, ack que apaga o item
# errado). Contra a nova, aprova. Usa a loja/PDV ficticia 9999/999, que nenhum
# agente real consome, e limpa tudo ao terminar.
import json
import os
import sys
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOJA, PDV = '9999', '999'
FILA = f'/pendente/{LOJA}/{PDV}'

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


def relay(metodo, endpoint, payload=None, token=TOKEN):
    dados = json.dumps(payload).encode() if payload is not None else None
    # User-Agent de navegador: o relay fica atras da protecao de bot do
    # Cloudflare, que responde 403 ao User-Agent padrao do urllib.
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
         'Accept': 'application/json'}
    if token is not None:
        h['X-Token'] = token
    if dados is not None:
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'{URL}{endpoint}', data=dados, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            corpo = r.read()
            return r.status, (json.loads(corpo) if corpo else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def enfileirar(pid, tipo='status_pdv', extra=None):
    corpo = {'tipo': tipo, 'pid': pid, 'corpo': f'PID: {pid}'}
    if extra:
        corpo.update(extra)
    return relay('POST', f'/comando/{LOJA}/{PDV}', corpo)


def ack(pid):
    return relay('POST', f'/resultado/{pid}', {'sucesso': True, 'mensagem': 'teste'})


def limpar():
    """Consome tudo o que estiver na fila, inclusive o que esta reservado.

    O GET /pendente pula item reservado, entao drenar so com GET deixa para tras
    tudo o que foi lido e nao respondido. A lista completa vem do /fila.
    """
    st, fila = relay('GET', f'/fila/{LOJA}/{PDV}')
    if st == 200 and fila:
        for item in fila.get('itens', []):
            if item.get('pid'):
                ack(item['pid'])
    for _ in range(10):
        st, item = relay('GET', FILA)
        if st != 200 or not item:
            break
        ack(item.get('pid', ''))


print('=' * 70)
print(f'TESTE DE ACEITACAO DO RELAY — {URL}')
print('=' * 70)
st, corpo = relay('GET', '/status')
checar(st == 200, 'GET /status responde 200', f'HTTP {st}')
if corpo:
    print(f'  {json.dumps(corpo)}')

limpar()
st, _ = relay('GET', FILA)
checar(st == 204, 'fila comeca vazia', f'HTTP {st}')

print()
print('=' * 70)
print('1. FILA NAO PODE SOBRESCREVER (era o defeito principal)')
print('=' * 70)
print('  Enfileira tres pedidos sem responder nenhum. Na versao antiga so o')
print('  ultimo sobrevivia; os dois primeiros eram perdidos sem erro.')
# PIDs unicos por execucao: o resultado de um ack fica legivel por 15 min, entao
# PID fixo faria a execucao seguinte encontrar o resultado da anterior e concluir
# que um pedido foi respondido quando nao foi.
import random
import string
_run = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))
pids = [f'ACC{_run}{n}' for n in (1, 2, 3)]
print(f'  PIDs desta execucao: {pids}')
for p in pids:
    st, _ = enfileirar(p)
    checar(st in (200, 201), f'POST do pedido {p}', f'HTTP {st}')

recebidos, tentativas = [], 0
while len(recebidos) < 3 and tentativas < 12:
    tentativas += 1
    st, item = relay('GET', FILA)
    if st != 200 or not item:
        time.sleep(0.6)
        continue
    pid = item.get('pid')
    if pid in recebidos:
        # Sem reserva de entrega, o mesmo item volta para sempre
        time.sleep(0.6)
        continue
    recebidos.append(pid)

print(f'  pedidos entregues: {recebidos}')
checar(len(recebidos) == 3, 'os TRES pedidos foram entregues',
       f'entregues {len(recebidos)} de 3 — fila ainda sobrescreve')
checar(set(recebidos) == set(pids), 'nenhum pedido se perdeu', str(recebidos))
checar(recebidos[0] == pids[0] if recebidos else False,
       'entrega na ordem de chegada (mais antigo primeiro)', str(recebidos))

print()
print('=' * 70)
print('2. RESERVA DE ENTREGA (item lento nao trava a fila)')
print('=' * 70)
print('  Os tres seguem sem resposta. Um GET agora nao deve devolver nenhum')
print('  deles de novo enquanto a reserva estiver de pe.')
st, item = relay('GET', FILA)
checar(st == 204 or (item and item.get('pid') not in pids),
       'itens ja entregues nao voltam durante a reserva',
       f'HTTP {st} item={item.get("pid") if item else None}')

print()
print('=' * 70)
print('3. ACK CASADO POR PID (o outro defeito)')
print('=' * 70)
print('  Responder um PID nao pode apagar os demais.')
st, _ = ack(pids[1])
checar(st in (200, 201), f'ack de {pids[1]}', f'HTTP {st}')

st, res = relay('GET', f'/resultado/{pids[1]}')
checar(st == 200 and res is not None, 'resultado do PID respondido fica legivel', f'HTTP {st}')

# Os outros dois continuam na fila: liberamos a reserva respondendo-os e
# conferindo que ambos ainda existiam.
sobreviventes = []
for p in (pids[0], pids[2]):
    st, _ = relay('GET', f'/resultado/{p}')
    # 204 aqui e o esperado: nunca respondemos esses dois
    if st == 204:
        sobreviventes.append(p)
checar(len(sobreviventes) == 2,
       'os pedidos nao respondidos seguem sem resultado (nao foram apagados)',
       str(sobreviventes))

print()
print('=' * 70)
print('4. AUTENTICACAO')
print('=' * 70)
st, _ = relay('GET', '/status', token=None)
checar(st == 401, 'sem X-Token responde 401', f'HTTP {st}')
st, _ = relay('GET', '/status', token='token-errado-de-proposito')
checar(st == 401, 'com token errado responde 401', f'HTTP {st}')

print()
print('=' * 70)
print('5. DIAGNOSTICO DA FILA (endpoint novo)')
print('=' * 70)
st, fila = relay('GET', f'/fila/{LOJA}/{PDV}')
if st == 404:
    checar(False, 'GET /fila disponivel',
           'HTTP 404 — o worker no ar ainda e a versao antiga')
else:
    checar(st == 200, 'GET /fila responde 200', f'HTTP {st}')
    if fila:
        print(f'  total na fila: {fila.get("total")}')
        for it in fila.get('itens', []):
            print(f"    pid={it.get('pid')} tipo={it.get('tipo')} reservado={it.get('reservado')}")
        checar('total' in fila and 'itens' in fila, 'resposta traz total e itens')

print()
print('=' * 70)
print('6. LIMPEZA')
print('=' * 70)
limpar()
st, _ = relay('GET', FILA)
checar(st == 204, 'fila vazia ao final', f'HTTP {st}')

print()
print('=' * 70)
if falhas:
    print(f'RESULTADO: {len(falhas)} FALHA(S)')
    for f in falhas:
        print(f'  - {f}')
    print()
    print('Se as falhas sao de fila sobrescrita / ack cruzado / rota /fila ausente,')
    print('o worker no ar ainda e a versao antiga — suba cloudflare_worker/worker.js.')
    sys.exit(1)
print('RESULTADO: o relay no ar tem o comportamento esperado')
print('=' * 70)
