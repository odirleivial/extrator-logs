# testar_relay.py
# Valida a comunicacao BEC -> agente pelo relay, ponta a ponta, sem tocar em PDV
# real e sem abrir a janela do BEC.
#
#   python scripts\testar_relay.py
#
# Cobre Solicitar Logs e as seis funcionalidades de Manutencao PDV. Todas as
# solicitacoes vao para a fila ficticia 9999/999, que nenhum agente real consome.
#
# O que confere:
#   - a rota do BEC responde sucesso e devolve PID
#   - o payload chega no relay com tipo, corpo, usuario do Windows e o mesmo PID
#   - o agente resolve o tipo para o handler correto
#   - o nome da funcionalidade na trilha e igual pelos dois canais (e-mail e relay)
#   - com a flag em "email", nada vai para o relay
#   - SERVERS_EP_SP nunca usa o relay
import base64
import json
import os
import sys
import urllib.request
import urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'server_agent'))

LOJA_FICTICIA = '9999'
PDV_FICTICIO  = '999'

falhas = []


def checar(condicao, descricao, detalhe=''):
    if condicao:
        print(f'  [OK]    {descricao}')
    else:
        print(f'  [FALHA] {descricao}' + (f' -> {detalhe}' if detalhe else ''))
        falhas.append(descricao)


def ler_prop(nome):
    caminho = os.path.join(RAIZ, 'properties', 'config.properties')
    with open(caminho, encoding='utf-8') as f:
        for linha in f:
            if linha.startswith(nome + '='):
                return linha.split('=', 1)[1].strip()
    return ''


URL   = ler_prop('bec_tunnel_url').rstrip('/')
TOKEN = ler_prop('pinpad_tunnel_token')


def relay(metodo, endpoint, payload=None):
    dados = json.dumps(payload).encode() if payload is not None else None
    # User-Agent de navegador e obrigatorio: o relay fica atras da protecao de bot
    # do Cloudflare, que responde 403 ao User-Agent padrao do urllib. E o mesmo
    # cabecalho que o _worker_call do BEC e o agente enviam.
    cabecalhos = {
        'X-Token': TOKEN,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    if dados is not None:
        cabecalhos['Content-Type'] = 'application/json'
    req = urllib.request.Request(
        f'{URL}{endpoint}', data=dados, method=metodo, headers=cabecalhos,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            corpo = resp.read()
            return resp.status, (json.loads(corpo) if corpo else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def drenar():
    """Esvazia a fila ficticia, inclusive o que esta reservado.

    Duas particularidades do relay tornam isso menos obvio do que parece:
      - o GET /pendente apenas LE o item; quem o descarta e o POST /resultado
      - o item lido fica RESERVADO por 10 min e desaparece dos GETs seguintes

    Drenar so com GET, portanto, deixa para tras tudo o que este teste leu e nao
    respondeu — e a proxima execucao encontraria a fila suja. Por isso a lista vem
    do /fila, que mostra tambem os reservados, e cada PID e respondido.
    """
    status, fila = relay('GET', f'/fila/{LOJA_FICTICIA}/{PDV_FICTICIO}')
    if status == 200 and fila:
        for item in fila.get('itens', []):
            pid_item = item.get('pid')
            if pid_item:
                relay('POST', f'/resultado/{pid_item}',
                      {'sucesso': True, 'mensagem': 'drenagem de teste'})
    # Rede de seguranca para item sem PID, que o ack nao alcanca
    for _ in range(10):
        status, item = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
        if status != 200 or not item:
            break
        relay('POST', f'/resultado/{item.get("pid", "")}',
              {'sucesso': True, 'mensagem': 'drenagem de teste'})


# O relay precisa estar de pe: se a cota diaria do KV estourou, ou o worker no ar
# e uma versao quebrada, /pendente responde 500 e TODOS os testes abaixo falhariam
# em cascata — mascarando a causa real com dezenas de falhas sem sentido.
_st, _ = relay('GET', '/status')
if _st != 200:
    print(f'ABORTANDO: GET /status respondeu HTTP {_st}. O relay nao esta acessivel.')
    sys.exit(2)
_st, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
if _st not in (200, 204):
    print('=' * 70)
    print(f'ABORTANDO: GET /pendente respondeu HTTP {_st}, mas /status esta OK.')
    print()
    print('O relay responde, mas a rota da fila falha. Causa mais provavel:')
    print('  - cota diaria do Cloudflare KV estourada (erro 1101 no Worker), ou')
    print('  - o worker publicado e uma versao com defeito.')
    print()
    print('Confira em: Cloudflare Dashboard -> Workers & Pages -> bec-relay -> Logs')
    print('e o consumo em: Workers KV usage dashboard.')
    print()
    print('Este teste so faz sentido com a fila funcionando — nada foi executado.')
    print('=' * 70)
    sys.exit(2)

print('=' * 70)
print('1. FILA LIMPA ANTES DE COMECAR')
print('=' * 70)
drenar()
status, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 204, f'fila {LOJA_FICTICIA}/{PDV_FICTICIO} vazia', f'HTTP {status}')

print()
print('=' * 70)
print('2. BEC: POST /solicitar com logs_modo_comunicacao=tunnel')
print('=' * 70)

import extrator_logs

# Config sintetica: nao mexe no properties da maquina. Aponta a fila para o par
# ficticio, de modo que nenhum agente real receba esta solicitacao.
_config_real = extrator_logs.ler_config_completo


def _config_teste(*args, **kwargs):
    # Aceita argumento porque parametrizacao_pdv chama ler_properties(config_file),
    # enquanto o resto do BEC chama ler_config_completo() sem parametro.
    props = dict(_config_real(*args, **kwargs))
    props['logs_modo_comunicacao'] = 'tunnel'
    props['pdv_modo_comunicacao']  = 'tunnel'
    props['bec_loja'] = LOJA_FICTICIA
    props['bec_pdv']  = PDV_FICTICIO
    return props


extrator_logs.ler_config_completo = _config_teste

ENVIADO = {
    'loja': '0045',
    'pdv': '450',
    'logs': ['CSIDebugFile', 'linx-tef'],
    'email_destino': 'ovm.extrator.logs@gmail.com',
    'data': '2026-08-17',
}

cliente = extrator_logs.app.test_client()
resp = cliente.post('/solicitar', data={
    'loja': ENVIADO['loja'],
    'pdv': ENVIADO['pdv'],
    'logs': ENVIADO['logs'],
    'email_destino': ENVIADO['email_destino'],
    'data': ENVIADO['data'],
})
corpo_resp = resp.get_json() or {}
print(f'  HTTP {resp.status_code} | {corpo_resp.get("mensagem", "")}')
checar(resp.status_code == 200, 'rota respondeu 200', f'HTTP {resp.status_code}')
checar(corpo_resp.get('sucesso') is True, 'resposta com sucesso=True')
checar('relay' in corpo_resp.get('mensagem', '').lower(),
       'mensagem indica envio pelo relay', corpo_resp.get('mensagem', ''))
pid_enviado = corpo_resp.get('pid', '')
checar(bool(pid_enviado), 'PID devolvido para a tela', pid_enviado)

print()
print('=' * 70)
print('3. RELAY: payload chegou na fila do agente')
print('=' * 70)
status, item = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 200 and item is not None, 'item presente na fila', f'HTTP {status}')

if item:
    print(f'  payload: {json.dumps(item, ensure_ascii=False)}')
    checar(item.get('tipo') == 'solicitacao_log', "campo tipo == 'solicitacao_log'", str(item.get('tipo')))
    checar(item.get('pid') == pid_enviado, 'PID igual ao devolvido pela rota')
    checar(item.get('loja') == ENVIADO['loja'], 'loja preservada')
    checar(item.get('pdv') == ENVIADO['pdv'], 'pdv preservado')
    checar(item.get('destino') == ENVIADO['email_destino'], 'destino preservado')
    checar(item.get('data') == '17/08/2026', "data convertida para dd/mm/yyyy", str(item.get('data')))
    checar(bool(item.get('usuario')), 'usuario do Windows presente', str(item.get('usuario')))
    for nome in ENVIADO['logs']:
        checar(nome in item.get('logs', ''), f'log "{nome}" presente na lista')

print()
print('=' * 70)
print('4. AGENTE: corpo reconstruido volta com os mesmos campos')
print('=' * 70)

import agent_extrator_log as agente

if item:
    corpo = agente._corpo_solicitacao_log(item)
    print('  corpo reconstruido:')
    for linha in corpo.splitlines():
        print(f'    {linha}')
    checar(agente.extrair_campo(corpo, 'PID') == item['pid'], 'PID extraido do corpo')
    checar(agente.extrair_campo(corpo, 'Loja') == ENVIADO['loja'], 'Loja extraida do corpo')
    checar(agente.extrair_campo(corpo, 'PDV') == ENVIADO['pdv'], 'PDV extraido do corpo')
    checar(agente.extrair_campo(corpo, 'Destino') == ENVIADO['email_destino'], 'Destino extraido do corpo')
    checar(agente.extrair_campo(corpo, 'Data') == '17/08/2026', 'Data extraida do corpo')
    checar(agente.extrair_campo(corpo, 'Usuario') == item['usuario'], 'Usuario extraido do corpo')
    logs_extraidos = [x.strip() for x in agente.extrair_campo(corpo, 'Logs').split(',') if x.strip()]
    checar(logs_extraidos == ENVIADO['logs'], 'lista de logs identica a enviada', str(logs_extraidos))

print()
print('=' * 70)
print('4B. GUARDA CONTRA REAPRESENTACAO DO MESMO PEDIDO')
print('=' * 70)
print('  O relay mantem o item pendente ate receber o resultado, entao o mesmo')
print('  pedido reaparece em todo poll de 2s durante a extracao (que leva')
print('  minutos). Sem esta guarda, cada poll disparia uma nova extracao.')
pid_falso = 'PIDTESTE01'
checar(agente._reservar_pid(pid_falso) is True, 'primeira reserva do PID e aceita')
checar(agente._reservar_pid(pid_falso) is False, 'segunda reserva do mesmo PID e recusada')
agente._liberar_pid(pid_falso)
checar(agente._reservar_pid(pid_falso) is True, 'apos liberar, o PID pode ser reservado de novo')
agente._liberar_pid(pid_falso)
checar(pid_falso not in agente._pids_em_andamento, 'PID sai do conjunto ao liberar')

print()
print('=' * 70)
print('5. SERVERS_EP_SP continua fora do relay (nao deve enfileirar)')
print('=' * 70)
drenar()
# Sem credencial de e-mail valida o envio falha, mas o que importa e que NAO
# tenha ido para a fila do relay.
try:
    cliente.post('/solicitar', data={
        'loja': 'SERVERS_EP_SP', 'pdv': '', 'logs': ['integrador_idb'],
        'email_destino': ENVIADO['email_destino'], 'data': ENVIADO['data'],
    })
except Exception:
    pass
status, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 204, 'nada enfileirado para SERVERS_EP_SP', f'HTTP {status}')

print()
print('=' * 70)
print('7. MANUTENCAO PDV: as 6 funcionalidades vao pelo relay')
print('=' * 70)

# Cada entrada: rota do BEC, form, tipo esperado na fila e handler do agente
CASOS_PDV = [
    ('/enviar-config-pdv',        {'selecao': '0045:450', 'email_destino': ENVIADO['email_destino'],
                                   'parametros': 'ext.properties'},
     'parametrizacao_pdv',       'processar_parametrizacao'),
    ('/verificar-config-pdv',     {'selecao': '0045:450', 'email_destino': ENVIADO['email_destino'],
                                   'parametros': 'ext.properties'},
     'verificar_parametrizacao', 'processar_verificar_parametrizacao'),
    ('/relatorio-parametrizacao', {'selecao': '0045:450', 'email_destino': ENVIADO['email_destino']},
     'relatorio_parametrizacao', 'processar_relatorio_parametrizacao'),
    ('/versao-pdv',               {'selecao': '0045:450', 'email_destino': ENVIADO['email_destino']},
     'status_pdv',               'processar_status_pdv'),
    ('/reiniciar-pdv',            {'selecao': '0045:450', 'email_destino': ENVIADO['email_destino']},
     'reiniciar_pdv',            'processar_reiniciar_pdv'),
    ('/fechar-pdv',               {'selecao': '0045:450', 'email_destino': ENVIADO['email_destino']},
     'fechar_pdv',               'processar_fechar_pdv'),
]

rotas_bec = {str(r) for r in extrator_logs.app.url_map.iter_rules()}

for rota, form, tipo_esperado, handler_esperado in CASOS_PDV:
    print(f'  --- {tipo_esperado} ({rota}) ---')
    if rota not in rotas_bec:
        checar(False, f'rota {rota} existe no BEC',
               'nomes de rota divergentes - ajustar CASOS_PDV')
        continue

    drenar()
    r = cliente.post(rota, data=form)
    j = r.get_json() or {}
    checar(r.status_code == 200 and j.get('sucesso') is True,
           'rota respondeu sucesso', f'HTTP {r.status_code} {j.get("mensagem", "")}')

    status, it = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
    checar(status == 200 and it is not None, 'item chegou na fila', f'HTTP {status}')
    if not it:
        continue

    checar(it.get('tipo') == tipo_esperado, f"tipo == '{tipo_esperado}'", str(it.get('tipo')))
    checar(bool(it.get('corpo')), 'payload traz o corpo chave/valor')
    checar(bool(it.get('usuario')), 'usuario do Windows presente')
    # O PID do payload tem de ser o mesmo que a tela mostrou, senao a trilha de
    # acoes do agente registraria um PID diferente do informado ao usuario
    checar(it.get('pid') == j.get('pid'), 'PID do payload igual ao devolvido pela rota',
           f"payload={it.get('pid')} rota={j.get('pid')}")
    if it.get('corpo'):
        checar(agente.extrair_campo(it['corpo'], 'PID') == it['pid'], 'PID coerente dentro do corpo')

    # O agente resolve este tipo para o handler correto?
    checar(tipo_esperado in agente._TIPOS_RELAY, 'tipo reconhecido pelo agente')
    h = agente._handler_do_tipo(tipo_esperado)
    checar(h is not None and h.__name__ == handler_esperado,
           f'handler resolvido == {handler_esperado}', h.__name__ if h else 'None')

print()
print('=' * 70)
print('8. COERENCIA ENTRE OS DOIS CANAIS')
print('=' * 70)
# O nome da funcionalidade na trilha tem de ser o mesmo pelos dois caminhos,
# senao a mesma acao apareceria com nomes distintos conforme o canal.
nomes_por_assunto = dict((m, n) for m, n in agente.FUNCIONALIDADES_POR_ASSUNTO)
equivalencia = {
    'solicitacao_log':          '[Solicitação Log]',
    'parametrizacao_pdv':       '[Parametrização PDV]',
    'verificar_parametrizacao': '[Verificar Parametrização]',
    'relatorio_parametrizacao': '[Relatório Parametrização]',
    'status_pdv':               '[Status PDV]',
    'fechar_pdv':               '[Fechar PDV]',
    'reiniciar_pdv':            '[Reiniciar PDV]',
}
for tipo, marcador in equivalencia.items():
    nome_relay = agente._TIPOS_RELAY[tipo][0]
    nome_email = nomes_por_assunto.get(marcador)
    checar(nome_relay == nome_email,
           f'{tipo}: nome igual nos dois canais ({nome_relay})',
           f'relay={nome_relay} email={nome_email}')

# E o BEC roteia cada assunto para o tipo que o agente conhece?
for tipo, marcador in equivalencia.items():
    if tipo == 'solicitacao_log':
        continue  # nao passa pelo despachante por assunto
    checar(extrator_logs._tipo_relay_do_assunto(f'{marcador} - [XYZ]') == tipo,
           f'BEC roteia "{marcador}" para {tipo}')

print()
print('=' * 70)
print('9. FLAG EM MODO EMAIL NAO ENFILEIRA')
print('=' * 70)
drenar()
extrator_logs.ler_config_completo = (
    lambda *a, **k: {**_config_teste(*a, **k), 'pdv_modo_comunicacao': 'email'}
)
try:
    cliente.post('/versao-pdv', data={'selecao': '0045:450',
                                      'email_destino': ENVIADO['email_destino']})
except Exception:
    pass
status, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 204, 'com a flag em email, nada vai para o relay', f'HTTP {status}')
extrator_logs.ler_config_completo = _config_teste

print()
print('=' * 70)
print('9B. REGISTRO DE EXECUCAO PELO RELAY')
print('=' * 70)
import execucao

drenar()
pid_reg = execucao.registrar_execucao(_config_teste(), 'Requisição API', detalhes={'API': 'teste'})
checar(bool(pid_reg), 'registrar_execucao devolveu PID', str(pid_reg))

# O envio roda em thread separada para nunca atrasar a operacao do usuario
import time
item_reg = None
for _ in range(20):
    status, item_reg = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
    if status == 200 and item_reg:
        break
    time.sleep(0.5)

checar(item_reg is not None, 'registro chegou na fila do relay')
if item_reg:
    checar(item_reg.get('tipo') == 'registro_execucao', "tipo == 'registro_execucao'",
           str(item_reg.get('tipo')))
    checar(item_reg.get('pid') == pid_reg, 'PID do payload igual ao devolvido')
    corpo_reg = item_reg.get('corpo', '')
    checar(agente.extrair_campo(corpo_reg, 'Funcionalidade') == 'Requisição API',
           'Funcionalidade no corpo', agente.extrair_campo(corpo_reg, 'Funcionalidade'))
    checar(bool(agente.extrair_campo(corpo_reg, 'Usuario')), 'usuario do Windows no corpo')
    checar(bool(agente.extrair_campo(corpo_reg, 'DataHora')), 'DataHora no corpo')

# O agente resolve e, crucialmente, NAO pre-registra a acao: quem registra e o
# handler, usando o nome que vem do corpo. Pre-registrar gravaria o nome errado e
# a deducao por PID engoliria o registro correto.
checar('registro_execucao' in agente._TIPOS_RELAY, 'tipo reconhecido pelo agente')
nome, assinatura, serializa, ack_antes = agente._TIPOS_RELAY['registro_execucao']
checar(nome is None, 'nome None: o handler registra a acao sozinho', str(nome))
checar(assinatura == 'simples', "assinatura 'simples' (imap, num, corpo)", assinatura)
checar(serializa is False, 'nao pega o lock das operacoes pesadas', str(serializa))
checar(ack_antes is False, 'responde ao relay so depois de executar', str(ack_antes))
h = agente._handler_do_tipo('registro_execucao')
checar(h is not None and h.__name__ == 'processar_registro_execucao',
       'handler resolvido == processar_registro_execucao', h.__name__ if h else 'None')

print()
print('=' * 70)
print('9B2. AGENTE EXECUTA O ITEM DE FATO (aridade e nome na trilha)')
print('=' * 70)
print('  Chama _tratar_item_relay como o polling faria. A aridade so quebraria em')
print('  runtime, entao vale exercitar o caminho inteiro ate a trilha de acoes.')

if item_reg:
    respostas = []
    agente._reservar_pid(item_reg['pid'])
    agente._tratar_item_relay(
        'registro_execucao', item_reg, _config_teste(), '', '',
        lambda ok, msg, extra=None: respostas.append((ok, msg, extra)),
    )
    checar(len(respostas) == 1, 'handler respondeu ao relay uma vez', str(respostas))
    checar(respostas and respostas[0][0] is True, 'resposta de sucesso',
           str(respostas[0]) if respostas else '')
    checar(item_reg['pid'] not in agente._pids_em_andamento,
           'PID liberado ao final')

    # A linha tem de sair com o nome vindo do CORPO, nao com o tipo
    linhas = []
    if os.path.exists(agente.ACOES_FILE):
        with open(agente.ACOES_FILE, encoding='utf-8', errors='replace') as f:
            linhas = [l for l in f if item_reg['pid'] in l]
    checar(len(linhas) == 1, 'exatamente uma linha na trilha para este PID',
           f'{len(linhas)} linha(s)')
    if linhas:
        print(f'    trilha: {linhas[0].strip()}')
        checar('[Requisição API]' in linhas[0],
               'nome na trilha veio do corpo, nao do tipo', linhas[0].strip())

print()
print('=' * 70)
print('9C. REGISTRO DE EXECUCAO EM MODO EMAIL NAO ENFILEIRA')
print('=' * 70)
drenar()
props_email = {**_config_teste(), 'registro_modo_comunicacao': 'email',
               'email_envio': '', 'senha_envio': ''}
execucao.registrar_execucao(props_email, 'MDM - Consultar', detalhes={'x': '1'})
time.sleep(1.5)
status, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 204, 'com a flag em email, nada vai para o relay', f'HTTP {status}')

print()
print('=' * 70)
print('9D. PINPAD PELO RELAY (regressao: usuario, PID e item unico)')
print('=' * 70)
print('  O relay guarda UM item por loja/PDV: um segundo POST sobrescreve o')
print('  primeiro. O PinPad enfileirava o comando e, logo depois, o registro de')
print('  execucao com o MESMO PID - o registro apagava o comando, e o ack do')
print('  comando apagava o registro. Dai o PID sumir da trilha.')

drenar()
extrator_logs.ler_config_completo = (
    lambda *a, **k: {**_config_teste(*a, **k),
                     'modo_instalacao': 'pc',
                     'pinpad_modo_comunicacao': 'tunnel',
                     'registro_modo_comunicacao': 'tunnel'}
)
r = cliente.post('/pinpad/comando', json={'comando': 'cartao'})
j = r.get_json() or {}
checar(r.status_code == 200 and j.get('sucesso') is True, 'rota do PinPad respondeu sucesso',
       f'HTTP {r.status_code} {j.get("mensagem", "")}')

time.sleep(1.5)  # tempo para a thread do registro de execucao, se ainda existisse
status, item_pp = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 200 and item_pp is not None, 'comando do PinPad esta na fila', f'HTTP {status}')

if item_pp:
    print(f'  payload: {json.dumps(item_pp, ensure_ascii=False)}')
    checar(item_pp.get('tipo') == 'pinpad', "tipo == 'pinpad'", str(item_pp.get('tipo')))
    checar(item_pp.get('comando') == 'cartao', 'comando preservado')
    # A regressao: o item na fila tem de ser o COMANDO, nao o registro de execucao
    checar(item_pp.get('tipo') != 'registro_execucao',
           'o registro de execucao NAO sobrescreveu o comando')
    # E o campo que faltava, sem o qual o agente nao carimba log nem trilha
    checar(bool(item_pp.get('usuario')), 'usuario do Windows no payload do PinPad',
           str(item_pp.get('usuario')))
    checar(bool(item_pp.get('pid')), 'PID no payload do PinPad')

extrator_logs.ler_config_completo = _config_teste

print()
print('=' * 70)
print('9E. COBERTURA: todo tipo tratado carimba usuario/PID e trilha')
print('=' * 70)
# _tratar_item_relay cobre todos os tipos do dicionario; o PinPad e o unico
# tratado inline no loop, e foi onde o carimbo faltava.
for tipo, (nome, assinatura, serializa, ack) in sorted(agente._TIPOS_RELAY.items()):
    quem = 'o proprio handler' if nome is None else '_tratar_item_relay'
    extra = '  [ack antes]' if ack else ''
    print(f'  {tipo:26} trilha por: {quem}{extra}')
checar(all(n is not None or t == 'registro_execucao'
           for t, (n, _, _, _) in agente._TIPOS_RELAY.items()),
       'so o registro_execucao delega a trilha ao handler')
checar([t for t, (_, _, _, a) in agente._TIPOS_RELAY.items() if a] == ['atualizacao_agente'],
       'so a atualizacao responde ao relay antes de executar')

fonte_agente = open(os.path.join(RAIZ, 'server_agent', 'agent_extrator_log.py'),
                    encoding='utf-8').read()
trecho_pinpad = fonte_agente.split('_executar_pinpad_local(comando, porta_cmd)')[0][-1200:]
checar('definir_contexto(usuario, pid)' in trecho_pinpad,
       'ramo do PinPad define o contexto antes de executar')
checar("registrar_acao_usuario('PinPad'" in trecho_pinpad,
       'ramo do PinPad registra a acao na trilha')

print()
print('=' * 70)
print('9F. ATUALIZACAO DO AGENTE PELO RELAY')
print('=' * 70)
import io, hashlib, zipfile as _zip

drenar()
# Pacote sintetico com o nome do executavel que o agente exige
buf = io.BytesIO()
with _zip.ZipFile(buf, 'w', _zip.ZIP_DEFLATED) as z:
    z.writestr('agent_extrator_log.exe', b'conteudo falso do exe' * 500)
    z.writestr('instalar_servico.bat', b'@echo off')
pacote_bytes = buf.getvalue()
sha_esperado = hashlib.sha256(pacote_bytes).hexdigest()

extrator_logs.ler_config_completo = (
    lambda *a, **k: {**_config_teste(*a, **k),
                     'admin_habilitado': 'true',
                     'atualizacao_modo_comunicacao': 'tunnel'}
)
r = cliente.post('/admin/atualizar-agente', data={
    'agente': 'extrator',
    'email_destino': ENVIADO['email_destino'],
    'pacote': (io.BytesIO(pacote_bytes), 'AgentExtratarLog_instalacao.zip'),
}, content_type='multipart/form-data')
j = r.get_json() or {}
print(f'  HTTP {r.status_code} | {j.get("mensagem", "")}')
checar(r.status_code == 200 and j.get('sucesso') is True, 'rota respondeu sucesso',
       f'HTTP {r.status_code} {j.get("mensagem", "")}')

status, item_at = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 200 and item_at is not None, 'pacote chegou na fila', f'HTTP {status}')

if item_at:
    checar(item_at.get('tipo') == 'atualizacao_agente', "tipo == 'atualizacao_agente'",
           str(item_at.get('tipo')))
    checar(item_at.get('pid') == j.get('pid'), 'PID do payload igual ao devolvido')
    b64 = item_at.get('arquivo', '')
    checar(bool(b64), 'payload traz o campo arquivo (base64)')
    recebido = base64.b64decode(b64) if b64 else b''
    checar(recebido == pacote_bytes, 'bytes do pacote chegaram intactos',
           f'{len(recebido)} vs {len(pacote_bytes)} bytes')
    corpo_at = item_at.get('corpo', '')
    checar(agente.extrair_campo(corpo_at, 'SHA256').lower() == sha_esperado,
           'SHA256 do corpo confere com o pacote enviado')
    checar(agente.extrair_campo(corpo_at, 'TamanhoBytes') == str(len(pacote_bytes)),
           'TamanhoBytes confere')
    # Pelo relay nao ha filtro do Gmail: nada deve ser renomeado
    checar('SufixoNeutro' not in corpo_at,
           'sem neutralizacao de .exe (o Gmail nao esta no caminho)')
    # E o registro de execucao nao pode ter sobrescrito o pacote
    checar(item_at.get('tipo') != 'registro_execucao',
           'registro de execucao nao sobrescreveu o pacote')

print()
print('  --- ack ANTES de executar (o restart mataria a resposta) ---')
nome_at, assinatura_at, serializa_at, ack_antes_at = agente._TIPOS_RELAY['atualizacao_agente']
checar(ack_antes_at is True, 'tipo marcado como ack_antes')
checar(assinatura_at == 'atualizacao', "assinatura 'atualizacao'", assinatura_at)
h = agente._handler_do_tipo('atualizacao_agente')
checar(h is not None and h.__name__ == 'processar_atualizacao',
       'handler resolvido == processar_atualizacao', h.__name__ if h else 'None')

# Simula o despacho ate o ponto do ack, com o handler trocado por um espiao que
# registra a ordem dos eventos. Se o ack vier depois, o item ficaria preso no
# relay e voltaria a cada poll apos o reinicio.
eventos = []
_orig = agente.processar_atualizacao
def _espiao(*a, **k):
    eventos.append('handler')
    return True
agente.processar_atualizacao = _espiao
try:
    agente._reservar_pid(item_at['pid'])
    agente._tratar_item_relay('atualizacao_agente', item_at, _config_teste(), '', '',
                              lambda ok, msg, extra=None: eventos.append('ack'))
finally:
    agente.processar_atualizacao = _orig

print(f'  ordem dos eventos: {eventos}')
checar(eventos[:2] == ['ack', 'handler'], 'ack acontece ANTES do handler', str(eventos))
checar(eventos.count('ack') == 1, 'ack enviado uma unica vez', str(eventos))

print()
print('  --- agente processa o pacote vindo do relay (ate a borda do restart) ---')
print('  _disparar_script e neutralizado: ele pararia o servico de verdade.')
if item_at:
    disparos = []
    _orig_disparar = agente._disparar_script
    _orig_registrar = agente._registrar_pid_aplicado
    agente._disparar_script = lambda script: (disparos.append(script), True)[1]
    agente._registrar_pid_aplicado = lambda pid, pacote='': None
    try:
        dados_zip = base64.b64decode(item_at['arquivo'])
        ok = agente.processar_atualizacao(
            None, None, None, item_at['corpo'], _config_teste(), '', dados_zip)
        checar(ok is True, 'processar_atualizacao concluiu com sucesso', str(ok))
        checar(len(disparos) == 1, 'script de atualizacao foi preparado', str(len(disparos)))

        pasta = os.path.join(agente.ATUALIZACAO_DIR, item_at['pid'])
        zip_gravado = os.path.join(pasta, 'AgentExtratarLog_instalacao.zip')
        checar(os.path.exists(zip_gravado), 'ZIP gravado a partir do base64', zip_gravado)
        if os.path.exists(zip_gravado):
            checar(open(zip_gravado, 'rb').read() == pacote_bytes,
                   'ZIP gravado e identico ao enviado')
        exe = os.path.join(pasta, 'extraido', 'agent_extrator_log.exe')
        checar(os.path.exists(exe), 'exe encontrado no pacote extraido', exe)
        ctx = os.path.join(pasta, 'contexto.txt')
        checar(os.path.exists(ctx), 'contexto gravado para a nova versao responder')
    finally:
        agente._disparar_script = _orig_disparar
        agente._registrar_pid_aplicado = _orig_registrar
        import shutil as _sh
        _sh.rmtree(os.path.join(agente.ATUALIZACAO_DIR, item_at['pid']), ignore_errors=True)

    # SHA divergente tem de barrar o pacote
    corpo_ruim = item_at['corpo'].replace(
        agente.extrair_campo(item_at['corpo'], 'SHA256'), 'f' * 64)
    ok_ruim = agente.processar_atualizacao(
        None, None, None, corpo_ruim, _config_teste(), '', base64.b64decode(item_at['arquivo']))
    checar(ok_ruim is False, 'pacote com SHA256 divergente e recusado', str(ok_ruim))
    import shutil as _sh2
    _sh2.rmtree(os.path.join(agente.ATUALIZACAO_DIR,
                             agente.extrair_campo(corpo_ruim, 'PID')), ignore_errors=True)

print()
print('  --- agente SP nunca vai pelo relay ---')
drenar()
r_sp = cliente.post('/admin/atualizar-agente', data={
    'agente': 'sp',
    'email_destino': ENVIADO['email_destino'],
    'pacote': (io.BytesIO(pacote_bytes), 'ServerAgentSP_instalacao.zip'),
}, content_type='multipart/form-data')
status, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 204, 'pacote do agente SP NAO foi para a fila do extrator',
       f'HTTP {status}')

extrator_logs.ler_config_completo = _config_teste

print()
print('=' * 70)
print('10. LIMPEZA')
print('=' * 70)
drenar()
status, _ = relay('GET', f'/pendente/{LOJA_FICTICIA}/{PDV_FICTICIO}')
checar(status == 204, 'fila ficticia vazia no final', f'HTTP {status}')

print()
print('=' * 70)
if falhas:
    print(f'RESULTADO: {len(falhas)} FALHA(S)')
    for f in falhas:
        print(f'  - {f}')
    sys.exit(1)
print('RESULTADO: todos os testes passaram')
print('=' * 70)
