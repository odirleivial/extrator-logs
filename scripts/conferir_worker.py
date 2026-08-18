# conferir_worker.py
# Conferencia estatica do cloudflare_worker/worker.js, para pegar erro bobo antes
# de subir na Cloudflare. Nao substitui o teste real: depois do deploy, rode
# scripts/testar_worker_relay.py, que exercita o relay de verdade.
#
#   python scripts\conferir_worker.py
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQ = os.path.join(RAIZ, 'cloudflare_worker', 'worker.js')

falhas = []


def checar(cond, desc, detalhe=''):
    print(('  [OK]    ' if cond else '  [FALHA] ') + desc + ('' if cond or not detalhe else f' -> {detalhe}'))
    if not cond:
        falhas.append(desc)


src = open(ARQ, encoding='utf-8').read()


def descascar(texto):
    """Remove comentarios e literais de string, para o balanceamento nao tropecar
    em delimitador dentro de texto. Scanner de caracteres em vez de regex —
    literal de template com escape derruba qualquer regex simples."""
    saida = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        prox = texto[i + 1] if i + 1 < n else ''
        if c == '/' and prox == '*':
            fim = texto.find('*/', i + 2)
            i = n if fim < 0 else fim + 2
        elif c == '/' and prox == '/':
            fim = texto.find('\n', i)
            i = n if fim < 0 else fim
        elif c in '\'"`':
            fecha, i = c, i + 1
            while i < n:
                if texto[i] == '\\':
                    i += 2
                    continue
                if texto[i] == fecha:
                    i += 1
                    break
                i += 1
            saida.append('""')
        else:
            saida.append(c)
            i += 1
    return ''.join(saida)


print('=' * 66)
print('CONFERENCIA ESTATICA DO WORKER')
print('=' * 66)
print(f'  arquivo: {ARQ}')
print(f'  tamanho: {len(src)} bytes, {src.count(chr(10)) + 1} linhas')
print()

limpo = descascar(src)
pares = {'(': ')', '[': ']', '{': '}'}
pilha, erro = [], None
for c in limpo:
    if c in pares:
        pilha.append(c)
    elif c in pares.values():
        if not pilha or pares[pilha.pop()] != c:
            erro = f'fechamento inesperado: {c!r}'
            break
if not erro and pilha:
    erro = f'nao fechados: {pilha}'
checar(erro is None, 'delimitadores balanceados', erro or '')

print()
print('Rotas que o BEC e o agente chamam:')
for rota, metodo in [('/status', 'GET'), ('/comando/', 'POST'),
                     ('/pendente/', 'GET'), ('/resultado/', 'POST'),
                     ('/resultado/', 'GET'), ('/fila/', 'GET')]:
    checar(rota in src, f'{metodo:4} {rota} declarada')

print()
print('Correcoes que esta versao precisa ter:')
checar(re.search(r'if\s*\(\s*!token\s*\)', src) is not None,
       'recusa tudo quando TOKEN nao esta configurado (falha fechada)')
checar('padStart(13' in src,
       'chave por item (fila real, sem sobrescrever o item anterior)')
checar('fila:' in src,
       'fila indexada em uma chave por agente')
checar(re.search(r"KV\.get\('pid:'\s*\+\s*pid\)", src) is not None,
       'ack localiza a chave exata pelo PID')
checar('lease' in src,
       'reserva de entrega, para item lento nao travar a fila')


# O bug do no-op da versao anterior nao pode voltar
checar(".replace(':', ':')" not in src,
       "sem o replace(':', ':') que nao fazia nada")

print()
print('Custo de KV — o agente busca a cada poucos segundos, entao o poll ocioso')
print('e multiplicado por dezenas de milhares por dia:')

# Chamadas reais de KV.list (mencoes em comentario nao contam)
linhas_codigo = [l for l in src.splitlines()
                 if 'KV.list' in l and not l.strip().startswith('*') and not l.strip().startswith('//')]
checar(len(linhas_codigo) == 0,
       'nenhuma chamada a KV.list (limite free e de 1.000/dia)',
       f'{len(linhas_codigo)} chamada(s): {linhas_codigo}')

# O caminho ocioso precisa sair com uma unica leitura
trecho_pendente = src.split("/pendente/:loja/:pdv")[-1].split('POST /resultado')[0]
checar('if (original.length === 0) return new Response(null, { status: 204 });' in trecho_pendente,
       'fila vazia sai com 1 leitura, sem escrita')
checar('limpo.length !== original.length' in trecho_pendente,
       'so grava o indice quando a poda mudou algo (nao gasta escrita a toa)')

print()
print('Validades declaradas:')
achou_ttl = False
for m in re.finditer(r'const (TTL_\w+|LEASE_TTL|MAX_VARRIDURA)\s*=\s*(\d+)', src):
    achou_ttl = True
    nome, valor = m.group(1), int(m.group(2))
    obs = ''
    if nome.startswith('TTL') or nome == 'LEASE_TTL':
        obs = f'  ({valor // 60} min)'
        if valor < 60:
            obs += '  [ATENCAO: o KV exige minimo de 60s]'
            falhas.append(f'{nome} abaixo do minimo do KV')
    print(f'  {nome:14} = {valor}{obs}')
checar(achou_ttl, 'constantes de validade declaradas')

print()
print('=' * 66)
if falhas:
    print(f'RESULTADO: {len(falhas)} problema(s)')
    for f in falhas:
        print(f'  - {f}')
    sys.exit(1)
print('RESULTADO: conferencia estatica passou')
print('Proximo passo: subir na Cloudflare e rodar scripts/testar_worker_relay.py')
print('=' * 66)
