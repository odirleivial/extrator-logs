# atualizacao_agente.py
# Envio do pacote de atualização para os agentes, pela aba Administrador.
#
# O BEC anexa o pacote escolhido a um e-mail cujo assunto identifica o agente
# de destino. Cada agente lê a caixa e reage ao seu próprio assunto — mesma
# mecânica já usada em [Solicitação Log] / [Solicitação Log SP].
#
# Os assuntos ficam no config.properties (agente.<id>.assunto) para poderem ser
# alinhados com o que os agentes vierem a implementar, sem alterar o BEC.
import base64
import hashlib
import logging
import os
import shutil
import tempfile
import zipfile

from logger import USUARIO_WINDOWS, MAQUINA
from execucao import gerar_pid, registrar_execucao

logger = logging.getLogger('ExtratrorLogs')

# Limite de anexo do Gmail (25 MB). Pacotes maiores são recusados na tela,
# antes de qualquer tentativa de envio.
TAMANHO_MAXIMO_MB = 25
EXTENSOES_PERMITIDAS = ('.zip', '.exe')

# Teto do relay, medido contra o Worker em produção em 17/08/2026: o corpo JSON
# precisa ficar abaixo de ~25 MB (limite de valor do KV). Como o pacote viaja em
# base64, que infla 4/3, sobram ~18,6 MB de binário — 18 MB é a margem adotada.
# (18 MB de binário = 24,0 MB de JSON, aceito; 19 MB = 25,3 MB, recusado com 500.)
TAMANHO_MAXIMO_RELAY_MB = 18

# O Gmail recusa executáveis mesmo dentro de arquivos compactados — o pacote do
# agente traz .exe e .bat e chegava com o download desabilitado. Antes de anexar,
# o BEC reempacota o ZIP acrescentando um sufixo neutro ao nome dessas entradas;
# o agente remove o sufixo depois de extrair. Renomeia apenas: o conteúdo dos
# arquivos não é alterado, então o SHA256 de cada um continua o mesmo.
EXTENSOES_BLOQUEADAS = (
    '.exe', '.dll', '.bat', '.cmd', '.com', '.msi', '.msp', '.jar', '.pyd',
    '.scr', '.vbs', '.vbe', '.js', '.jse', '.wsf', '.wsh', '.ps1', '.psm1',
    '.hta', '.cpl', '.lnk', '.pif', '.reg', '.sys', '.ade', '.adp', '.apk',
    '.appx', '.chm', '.ins', '.isp', '.jnlp', '.mde', '.sct', '.shb',
)
SUFIXO_NEUTRO = '.becpkg'

# Usado quando o config.properties ainda não tem as chaves agente.*
AGENTES_PADRAO = [
    {'id': 'extrator', 'nome': 'Agent Extrator Log', 'assunto': '[Atualizacao Agente]'},
    {'id': 'sp', 'nome': 'Server Agent SP', 'assunto': '[Atualizacao Agente SP]'},
]

# Agentes que consomem a fila do relay. O Server Agent SP fica de fora por dois
# motivos somados: a rede de SP não alcança o relay, e a fila é endereçada por
# bec_loja/bec_pdv — que é a do Agent Extrator. Publicar o pacote do SP ali
# entregaria o build errado ao extrator.
AGENTES_COM_RELAY = ('extrator',)


def obter_agentes(props):
    """Lê os agentes de atualização do config.properties.

    Formato (espelha a convenção já usada pelas APIs):
        agentes_order=extrator,sp
        agente.extrator.nome=Agent Extrator Log
        agente.extrator.assunto=[Atualizacao Agente]
    """
    ordem = [a.strip() for a in props.get('agentes_order', '').split(',') if a.strip()]
    if not ordem:
        return list(AGENTES_PADRAO)

    agentes = []
    for id_agente in ordem:
        nome = props.get(f'agente.{id_agente}.nome', '').strip()
        assunto = props.get(f'agente.{id_agente}.assunto', '').strip()
        if nome and assunto:
            agentes.append({'id': id_agente, 'nome': nome, 'assunto': assunto})
    return agentes or list(AGENTES_PADRAO)


def _precisa_neutralizar(nome_entrada):
    """True quando a entrada do ZIP tem extensão que o Gmail recusa."""
    base = nome_entrada.rsplit('/', 1)[-1].lower()
    return base.endswith(EXTENSOES_BLOQUEADAS)


def _reempacotar(caminho_zip, destino):
    """Reescreve o ZIP com as entradas bloqueadas renomeadas. Retorna os nomes originais.

    Preserva data/hora e método de compressão de cada entrada; só o nome muda.
    ZIPs aninhados não são inspecionados — os pacotes dos agentes são planos.
    """
    renomeados = []
    with zipfile.ZipFile(caminho_zip) as origem, \
         zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as saida:
        for info in origem.infolist():
            if info.is_dir():
                saida.writestr(info, b'')
                continue
            dados = origem.read(info.filename)
            novo = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            novo.compress_type = info.compress_type
            novo.external_attr = info.external_attr
            if _precisa_neutralizar(info.filename):
                novo.filename = info.filename + SUFIXO_NEUTRO
                renomeados.append(info.filename)
            saida.writestr(novo, dados)
    return renomeados


def _preparar_pacote(caminho, pasta_temp, nome_pacote):
    """Deixa o pacote em condição de passar pelo filtro do Gmail.

    ZIP: reempacota renomeando as entradas bloqueadas.
    EXE avulso: embrulha em um ZIP, também com o nome neutralizado.
    Retorna (caminho_a_anexar, nome_do_anexo, entradas_renomeadas).
    """
    if nome_pacote.lower().endswith('.exe'):
        nome_anexo = nome_pacote + '.zip'
        destino = os.path.join(pasta_temp, nome_anexo)
        with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(caminho, arcname=nome_pacote + SUFIXO_NEUTRO)
        return destino, nome_anexo, [nome_pacote]

    destino = os.path.join(pasta_temp, 'envio_' + nome_pacote)
    renomeados = _reempacotar(caminho, destino)
    if not renomeados:
        return caminho, nome_pacote, []  # nada bloqueado: envia o original
    os.replace(destino, caminho)
    return caminho, nome_pacote, renomeados


def _empacotar_para_relay(caminho, pasta_temp, nome_pacote):
    """Deixa o pacote pronto para a fila, sem a neutralização do Gmail.

    Pelo relay não há filtro de anexo, então o ZIP segue exatamente como o
    usuário escolheu. Um .exe avulso ainda é embrulhado, porque o agente sempre
    espera um ZIP para extrair.
    """
    if not nome_pacote.lower().endswith('.exe'):
        return caminho, nome_pacote

    nome_zip = nome_pacote + '.zip'
    destino = os.path.join(pasta_temp, nome_zip)
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(caminho, arcname=nome_pacote)
    return destino, nome_zip


def _enviar_pelo_relay(props, agente, destino, caminho, nome_pacote, enfileirar_no_relay):
    """Publica o pacote na fila do agente. Retorna (payload, http_status)."""
    tamanho = os.path.getsize(caminho)
    if tamanho > TAMANHO_MAXIMO_RELAY_MB * 1024 * 1024:
        return {'sucesso': False, 'mensagem':
                f'Pacote com {tamanho / 1024 / 1024:.1f} MB excede o limite de '
                f'{TAMANHO_MAXIMO_RELAY_MB} MB do relay (o pacote viaja em base64, '
                f'que ocupa 1/3 a mais).'}, 400

    pid = gerar_pid()
    sha256 = _sha256(caminho)
    # Sem SufixoNeutro: nada foi renomeado, e o agente trata a ausência como
    # "nenhum arquivo a restaurar".
    corpo = _montar_corpo(agente, pid, destino, nome_pacote, tamanho, sha256, [])

    with open(caminho, 'rb') as f:
        conteudo_b64 = base64.b64encode(f.read()).decode('ascii')

    logger.info(f"[Relay] Enfileirando atualização do agente '{agente['nome']}': {nome_pacote} "
                f"({tamanho / 1024 / 1024:.2f} MB, {len(conteudo_b64) / 1024 / 1024:.2f} MB em base64) PID={pid}")

    try:
        enfileirar_no_relay(props, 'atualizacao_agente', pid, corpo, {'arquivo': conteudo_b64})
    except Exception as e:
        logger.error(f"[Relay] Falha ao enfileirar atualização: {e}")
        return {'sucesso': False, 'mensagem': f'Erro ao enviar pelo relay: {e}'}, 500

    # Sem registrar_execucao aqui: seria um segundo item na MESMA fila, com o
    # mesmo PID, e o relay guarda um item só por loja/PDV — o registro
    # sobrescreveria o pacote. Quem registra a ação é o agente, ao consumi-lo.
    logger.info(f"[Relay] Atualização do agente '{agente['nome']}' enfileirada. PID={pid}")
    return {
        'sucesso': True,
        'pid': pid,
        'mensagem': f"Pacote '{nome_pacote}' enviado pelo relay para {agente['nome']}! "
                    f"PID: {pid}. O resultado da instalação chega por e-mail.",
    }, 200


def _sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, 'rb') as f:
        for bloco in iter(lambda: f.read(65536), b''):
            h.update(bloco)
    return h.hexdigest()


def _montar_corpo(agente, pid, destino, nome_pacote, tamanho_bytes, sha256, renomeados):
    """Corpo em chave/valor, no mesmo padrão dos demais e-mails lidos pelos agentes.

    Destino é o endereço para onde o agente responde com o resultado da instalação.
    SufixoNeutro indica ao agente que, após extrair, ele deve remover esse sufixo
    do nome dos arquivos listados para restaurar os executáveis.
    """
    corpo = (
        f"PID: {pid}\n"
        f"Usuario: {USUARIO_WINDOWS}\n"
        f"Maquina: {MAQUINA}\n"
        f"Destino: {destino}\n"
        f"Agente: {agente['nome']}\n"
        f"Pacote: {nome_pacote}\n"
        f"TamanhoBytes: {tamanho_bytes}\n"
        f"SHA256: {sha256}\n"
    )
    if renomeados:
        corpo += (
            f"SufixoNeutro: {SUFIXO_NEUTRO}\n"
            f"ArquivosNeutralizados: {len(renomeados)}\n"
            + ''.join(f"Neutralizado: {nome}\n" for nome in renomeados)
        )
    return corpo


def enviar_atualizacao(props, form, files, enviar_email_com_anexos, enfileirar_no_relay=None):
    """Valida a seleção e envia o pacote ao agente. Retorna (payload, http_status).

    O canal é escolhido por `atualizacao_modo_comunicacao`. O modo relay vale só
    para o Agent Extrator: o Server Agent SP não alcança o relay da rede dele.
    """
    id_agente = form.get('agente', '').strip()
    agentes = obter_agentes(props)
    agente = next((a for a in agentes if a['id'] == id_agente), None)
    if not agente:
        return {'sucesso': False, 'mensagem': 'Selecione o agente que deseja atualizar.'}, 400

    destino = form.get('email_destino', '').strip()
    if not destino:
        return {'sucesso': False, 'mensagem': 'Selecione o e-mail que receberá o resultado da instalação.'}, 400

    arquivo = files.get('pacote')
    if not arquivo or not arquivo.filename:
        return {'sucesso': False, 'mensagem': 'Escolha o pacote de instalação a enviar.'}, 400

    nome_pacote = os.path.basename(arquivo.filename)
    if not nome_pacote.lower().endswith(EXTENSOES_PERMITIDAS):
        permitidas = ', '.join(EXTENSOES_PERMITIDAS)
        return {'sucesso': False, 'mensagem': f'Formato não aceito. Envie um arquivo {permitidas}.'}, 400

    # A fila do relay é endereçada por bec_loja/bec_pdv, que é a do Agent Extrator.
    # Mandar o pacote do agente SP por ali entregaria o build errado ao extrator,
    # então o relay fica restrito ao agente que de fato consome essa fila.
    via_relay = (
        enfileirar_no_relay is not None
        and id_agente in AGENTES_COM_RELAY
        and props.get('atualizacao_modo_comunicacao', 'email').strip().lower() == 'tunnel'
    )

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')
    if not via_relay and (not remetente or not senha):
        return {'sucesso': False, 'mensagem': 'E-mail remetente não configurado (aba Administrador).'}, 400

    pasta_temp = tempfile.mkdtemp(prefix='bec_pacote_')
    caminho = os.path.join(pasta_temp, nome_pacote)
    try:
        arquivo.save(caminho)
        if os.path.getsize(caminho) == 0:
            return {'sucesso': False, 'mensagem': 'O pacote selecionado está vazio.'}, 400

        if not zipfile.is_zipfile(caminho) and not nome_pacote.lower().endswith('.exe'):
            return {'sucesso': False, 'mensagem': 'O arquivo não é um ZIP válido.'}, 400

        if via_relay:
            caminho, nome_relay = _empacotar_para_relay(caminho, pasta_temp, nome_pacote)
            return _enviar_pelo_relay(props, agente, destino, caminho, nome_relay,
                                      enfileirar_no_relay)

        caminho, nome_anexo, renomeados = _preparar_pacote(caminho, pasta_temp, nome_pacote)

        # Tamanho e hash são apurados depois do reempacotamento: é esse arquivo
        # que segue anexado e que o agente vai conferir.
        tamanho = os.path.getsize(caminho)
        if tamanho > TAMANHO_MAXIMO_MB * 1024 * 1024:
            return {'sucesso': False, 'mensagem':
                    f'Pacote com {tamanho / 1024 / 1024:.1f} MB excede o limite de '
                    f'{TAMANHO_MAXIMO_MB} MB por e-mail.'}, 400

        pid = gerar_pid()
        sha256 = _sha256(caminho)
        assunto = f"{agente['assunto']} - [{pid}]"
        corpo = _montar_corpo(agente, pid, destino, nome_anexo, tamanho, sha256, renomeados)
        corpo_html = f"<pre style=\"font-family:Consolas,monospace;font-size:13px\">{corpo}</pre>"

        logger.info(f"Enviando atualização do agente '{agente['nome']}': {nome_anexo} "
                    f"({tamanho / 1024 / 1024:.2f} MB, {len(renomeados)} arquivo(s) neutralizado(s)) PID={pid}")

        enviar_email_com_anexos(remetente, senha, remetente, assunto, corpo, corpo_html, [caminho])

        logger.info(f"Atualização do agente '{agente['nome']}' enviada. PID={pid}")
        registrar_execucao(props, 'Atualizar Agente', pid, {
            'Agente': agente['nome'],
            'Destino': destino,
            'Pacote': nome_anexo,
            'TamanhoBytes': tamanho,
            'SHA256': sha256,
            'ArquivosNeutralizados': len(renomeados),
        })

        aviso = (f" {len(renomeados)} executável(is) renomeado(s) para passar pelo Gmail."
                 if renomeados else '')
        return {
            'sucesso': True,
            'pid': pid,
            'mensagem': f"Pacote '{nome_anexo}' enviado para {agente['nome']}! PID: {pid}.{aviso}",
        }, 200
    except Exception as e:
        logger.error(f"Erro ao enviar atualização do agente '{agente['nome']}': {str(e)}")
        return {'sucesso': False, 'mensagem': f'Erro ao enviar: {str(e)}'}, 500
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)
