import imaplib, email, smtplib
import os, time, csv, zipfile, re, logging, ctypes, ctypes.wintypes, threading
import base64 as _base64
import winreg
try:
    import urllib.request as _urllib_req
    import urllib.error as _urllib_err
    import json as _json
except ImportError:
    pass
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime

import sys
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from version import __version__ as AGENTE_VERSAO
except ImportError:
    AGENTE_VERSAO = 'desconhecida'

CONFIG_FILE = os.path.join(BASE_DIR, 'agent.properties')
CSV_LOG     = os.path.join(BASE_DIR, 'historico_envio_logs.csv')
CSV_PARAM   = os.path.join(BASE_DIR, 'historico_parametrizacao.csv')
LOG_DIR     = os.path.join(BASE_DIR, 'log')

os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Impressora: helpers que leem hashes do agent.properties (props)
def _imp_virtual_hash(props):
    return props.get('impressora.virtual.hash', '')

def _imp_fisica_portas(props):
    return {k[len('impressora.fisica.'):].upper(): v
            for k, v in props.items() if k.startswith('impressora.fisica.')}

# ---------------------------------------------------------------------------
# Logging diário
# ---------------------------------------------------------------------------
# O dia corrente é sempre gravado no arquivo fixo agente_extrator.log. Na virada
# do dia (ou no primeiro log após reinício em outra data), o arquivo fixo é
# renomeado para agente_extrator_<data-anterior>.log e um novo arquivo fixo é
# iniciado para o dia atual.
LOG_FILE_BASE = 'agente_extrator'
LOG_FILE      = os.path.join(LOG_DIR, f'{LOG_FILE_BASE}.log')

_log_date = None
_logger = logging.getLogger('agente')
_logger.setLevel(logging.INFO)

# Cada linha do log leva o usuário do Windows e o PID da solicitação em curso —
# ambos vêm do corpo do e-mail que está sendo tratado. Ficam em threading.local
# porque o polling do PinPad roda em paralelo e não pode herdar o contexto de um
# e-mail que a thread principal esteja processando.
SEM_CONTEXTO = '-'
_contexto = threading.local()

def definir_contexto(usuario=None, pid=None):
    _contexto.usuario = (usuario or '').strip() or SEM_CONTEXTO
    _contexto.pid     = (pid or '').strip() or SEM_CONTEXTO

def limpar_contexto():
    definir_contexto()

class _FiltroContexto(logging.Filter):
    """Injeta usuario/pid no registro para o formatter poder usá-los."""
    def filter(self, record):
        record.usuario = getattr(_contexto, 'usuario', SEM_CONTEXTO)
        record.pid     = getattr(_contexto, 'pid', SEM_CONTEXTO)
        return True

_logger.addFilter(_FiltroContexto())

def _arquivar_log_anterior(data_anterior):
    """Renomeia o log fixo para agente_extrator_<data_anterior>.log.
    Se já existir arquivo para essa data (ex.: reinícios no mesmo dia), anexa o
    conteúdo em vez de sobrescrever."""
    if not os.path.exists(LOG_FILE):
        return
    destino = os.path.join(LOG_DIR, f'{LOG_FILE_BASE}_{data_anterior}.log')
    try:
        if os.path.exists(destino):
            with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as src, \
                 open(destino, 'a', encoding='utf-8') as dst:
                dst.write(src.read())
            os.remove(LOG_FILE)
        else:
            os.replace(LOG_FILE, destino)
    except Exception as e:
        print(f'[LOG] Falha ao arquivar log anterior ({data_anterior}): {e}')

def _atualizar_handler():
    global _log_date
    hoje = datetime.now().strftime('%Y-%m-%d')
    if _log_date == hoje:
        return

    # Fecha o handler atual para liberar o arquivo antes de renomear
    for h in _logger.handlers[:]:
        h.close()
        _logger.removeHandler(h)

    # Descobre a que dia pertencem as informações já gravadas no log fixo:
    # em rollover no mesmo processo é o _log_date; num reinício é a data de
    # modificação do arquivo fixo existente.
    if _log_date is not None:
        data_anterior = _log_date
    elif os.path.exists(LOG_FILE):
        data_anterior = datetime.fromtimestamp(
            os.path.getmtime(LOG_FILE)).strftime('%Y-%m-%d')
    else:
        data_anterior = None

    if data_anterior and data_anterior != hoje:
        _arquivar_log_anterior(data_anterior)

    _log_date = hoje
    handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - [%(usuario)s] - [%(pid)s] - [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    _logger.addHandler(handler)

_debug_mode = False  # Ativado via debug=true em agent.properties

def log(msg, level='info'):
    _atualizar_handler()
    print(msg)
    getattr(_logger, level)(msg)

def logd(msg):
    """Log nível DEBUG — só grava quando debug=true em agent.properties."""
    if _debug_mode:
        log(f'[DEBUG] {msg}', 'debug')

# ---------------------------------------------------------------------------
# Trilha de ações por usuário
# ---------------------------------------------------------------------------
# Arquivo cumulativo (nunca rotacionado) com uma linha por ação — uma por PID:
#   2026-08-14 11:33:00 - [odirl] - [HvRiORiQcj] - [Requisição API]
# Registra tanto o que chega para o agente executar quanto o que o BEC executa
# sozinho e comunica pelo e-mail [Registro Execucao]. Como o BEC usa o mesmo PID
# nos dois e-mails de uma mesma ação (ex.: Atualizar Agente), a checagem por PID
# garante que a ação apareça uma única vez.
ACOES_FILE = os.path.join(LOG_DIR, 'acoes_usuarios.log')

_pids_registrados = None

def _carregar_pids_registrados():
    """Lê da própria trilha os PIDs já gravados, para não duplicar linha quando o
    mesmo e-mail voltar a ser lido (reinício antes de marcá-lo como lido)."""
    global _pids_registrados
    if _pids_registrados is not None:
        return _pids_registrados
    _pids_registrados = set()
    if os.path.exists(ACOES_FILE):
        try:
            with open(ACOES_FILE, 'r', encoding='utf-8', errors='replace') as f:
                for linha in f:
                    partes = linha.split(' - ')
                    if len(partes) >= 3:
                        _pids_registrados.add(partes[2].strip().strip('[]'))
        except Exception as e:
            print(f'[ACOES] Falha ao ler {ACOES_FILE}: {e}')
    return _pids_registrados

def _normalizar_data_hora(texto):
    """Converte a DataHora do e-mail para o padrão do log. Sem valor válido, usa agora."""
    texto = (texto or '').strip()
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(texto, fmt).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def registrar_acao_usuario(funcionalidade, pid, usuario, data_hora=''):
    """Acrescenta a ação na trilha cumulativa. Retorna False se o PID já constar.

    Nunca propaga exceção: falhar ao registrar não pode impedir a execução da
    funcionalidade que o usuário pediu.
    """
    pid     = (pid or '').strip() or SEM_CONTEXTO
    usuario = (usuario or '').strip() or SEM_CONTEXTO
    funcionalidade = (funcionalidade or '').strip() or 'Desconhecida'

    registrados = _carregar_pids_registrados()
    # PID ausente não serve para identificar a ação — nesses casos sempre grava
    if pid != SEM_CONTEXTO and pid in registrados:
        return False

    try:
        quando = _normalizar_data_hora(data_hora)
        with open(ACOES_FILE, 'a', encoding='utf-8') as f:
            f.write(f'{quando} - [{usuario}] - [{pid}] - [{funcionalidade}]\n')
        if pid != SEM_CONTEXTO:
            registrados.add(pid)
        return True
    except Exception as e:
        print(f'[ACOES] Falha ao registrar ação em {ACOES_FILE}: {e}')
        return False

# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def ler_properties(arquivo):
    props = {}
    with open(arquivo, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                props[key.strip()] = value.strip()
    return props

def gravar_csv_log(dados):
    existe = os.path.exists(CSV_LOG)
    with open(CSV_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(['PID', 'Destino', 'Loja', 'PDV', 'Logs', 'ArquivoZip', 'DataHora', 'Status', 'Erro'])
        writer.writerow(dados)

def gravar_csv_param(dados):
    existe = os.path.exists(CSV_PARAM)
    with open(CSV_PARAM, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(['PID', 'Loja', 'PDV', 'Parametro', 'Arquivo', 'Constante', 'ValorNovo', 'DataHora', 'Status', 'Erro'])
        writer.writerow(dados)

def decodifica_assunto(assunto_header):
    assunto, charset = decode_header(assunto_header)[0]
    if isinstance(assunto, bytes):
        assunto = assunto.decode(charset or 'utf-8', errors='replace')
    return assunto

def extrair_corpo(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                charset = part.get_content_charset() or 'utf-8'
                return part.get_payload(decode=True).decode(charset, errors='replace')
    else:
        charset = msg.get_content_charset() or 'utf-8'
        return msg.get_payload(decode=True).decode(charset, errors='replace')
    return ''

def enviar_email_com_anexo(remetente, senha, destino, assunto, corpo, arquivo_anexo, corpo_html=''):
    msg = MIMEMultipart('mixed')
    msg['From'] = remetente
    msg['To'] = destino
    msg['Subject'] = assunto
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(corpo, 'plain', 'utf-8'))
    if corpo_html:
        alt.attach(MIMEText(corpo_html, 'html', 'utf-8'))
    msg.attach(alt)
    with open(arquivo_anexo, 'rb') as f:
        part = MIMEBase('application', 'zip')
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(arquivo_anexo)}"')
    msg.attach(part)
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.starttls()
        smtp.login(remetente, senha)
        smtp.sendmail(remetente, destino, msg.as_string())

def enviar_email_texto(remetente, senha, destino, assunto, corpo):
    msg = MIMEMultipart()
    msg['From'] = remetente
    msg['To'] = destino
    msg['Subject'] = assunto
    msg.attach(MIMEText(corpo, 'plain', 'utf-8'))
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.starttls()
        smtp.login(remetente, senha)
        smtp.sendmail(remetente, destino, msg.as_string())

def enviar_email_html(remetente, senha, destino, assunto, corpo_html, corpo_texto=''):
    msg = MIMEMultipart('alternative')
    msg['From'] = remetente
    msg['To'] = destino
    msg['Subject'] = assunto
    if corpo_texto:
        msg.attach(MIMEText(corpo_texto, 'plain', 'utf-8'))
    msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.starttls()
        smtp.login(remetente, senha)
        smtp.sendmail(remetente, destino, msg.as_string())

# ---------------------------------------------------------------------------
# Extração de campos do corpo do e-mail
# ---------------------------------------------------------------------------
class _NETRESOURCEW(ctypes.Structure):
    _fields_ = [
        ('dwScope',       ctypes.wintypes.DWORD),
        ('dwType',        ctypes.wintypes.DWORD),
        ('dwDisplayType', ctypes.wintypes.DWORD),
        ('dwUsage',       ctypes.wintypes.DWORD),
        ('lpLocalName',   ctypes.wintypes.LPWSTR),
        ('lpRemoteName',  ctypes.wintypes.LPWSTR),
        ('lpComment',     ctypes.wintypes.LPWSTR),
        ('lpProvider',    ctypes.wintypes.LPWSTR),
    ]

def autenticar_unc(base_pdv, windows_user, windows_senha):
    """Autentica na share administrativa do PDV usando WNetAddConnection2 no processo atual."""
    share = f'\\\\{base_pdv}\\C$'

    if not windows_user or not windows_senha:
        log(f'Aviso: windows_user/windows_senha não configurados em agent.properties', 'warning')

    # Remove conexão anterior para evitar conflito de credenciais (erro 1219)
    ctypes.windll.mpr.WNetCancelConnection2W(share, 0, True)

    nr = _NETRESOURCEW()
    nr.dwType      = 1  # RESOURCETYPE_DISK
    nr.lpRemoteName = share

    resultado = ctypes.windll.mpr.WNetAddConnection2W(
        ctypes.byref(nr),
        windows_senha or None,
        windows_user  or None,
        0
    )

    if resultado == 0:
        log(f'Autenticado em {share} (usuario={windows_user or "padrao"})')
    elif resultado == 1219:
        # Credencial já existe para esse servidor — conexão válida
        log(f'Conexão já estabelecida em {share}')
    else:
        raise PermissionError(f'Falha ao autenticar em {share}: erro WNet={resultado}')

def extrair_campo(corpo, campo):
    m = re.search(rf'{re.escape(campo)}:\s*(.+)', corpo)
    return m.group(1).strip() if m else ''

def _marcar_lido(imap, num):
    """Marca a mensagem como lida — sem efeito quando a solicitação não veio de
    e-mail.

    Os handlers `processar_*` atendem dois canais: o e-mail e a fila do relay. No
    caminho do relay não existe mensagem para marcar, e `imap`/`num` chegam como
    None.
    """
    if imap is None or num is None:
        return
    imap.store(num, '+FLAGS', '\\Seen')

def ler_versao_pdv(base_pdv, props):
    """Lê a versão do PDV no arquivo versaoPDV.dat."""
    caminho_relativo = props.get('versaoPDV', r'\p2k\Bin\versaoPDV.dat')
    if base_pdv:
        caminho = os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\'))
    else:
        caminho = caminho_relativo
    try:
        with open(caminho, 'r', encoding='utf-8', errors='replace') as f:
            return f.read().strip() or 'N/D'
    except Exception:
        return 'N/D'

def _ips_locais():
    """Retorna o conjunto de IPs desta máquina."""
    import socket
    ips = {'127.0.0.1', 'localhost'}
    try:
        hostname = socket.gethostname()
        ips.add(socket.gethostbyname(hostname))
        for info in socket.getaddrinfo(hostname, None):
            ips.add(info[4][0])
    except Exception:
        pass
    return ips

def verificar_processo_pdv(base_pdv, props):
    """Verifica se o LINX STOREX está em execução tentando conexão TCP na porta 4000.
    O VerificadorLockPDV usa essa mesma porta para detectar instância ativa."""
    if not base_pdv:
        return 'N/D'
    import socket
    try:
        host = '127.0.0.1' if base_pdv in _ips_locais() else base_pdv
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        resultado = s.connect_ex((host, 4000))
        s.close()
        return 'true' if resultado == 0 else 'false'
    except Exception as e:
        log(f'Erro ao verificar porta 4000 em {base_pdv}: {e}', 'warning')
        return 'N/D'

def ler_status_online(base_pdv, props):
    """Lê a última ocorrência de indicaOnLine no CSIDebugFile. Retorna (ativo, status_server)."""
    caminho_relativo = props.get('CSIDebugFile', r'\p2k\bin\CSIDebugFile.txt')
    if base_pdv:
        caminho = os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\'))
    else:
        caminho = caminho_relativo
    try:
        with open(caminho, 'r', encoding='utf-8', errors='replace') as f:
            conteudo = f.read()
        matches = re.findall(
            r'PDV\s*::\s*indicaOnLine\s*->\s*Ativo=(\w+)\s+StatusServer=(\w+)',
            conteudo
        )
        if matches:
            ativo, status_server = matches[-1]
            return ativo.lower(), status_server.lower()
        return 'N/D', 'N/D'
    except Exception as e:
        log(f'Erro ao ler CSIDebugFile em {base_pdv}: {e}', 'warning')
        return 'N/D', 'N/D'

# ---------------------------------------------------------------------------
# Logs históricos (dias anteriores)
# ---------------------------------------------------------------------------
# Configuração no agent.properties:
#   historico.<log>.caminho = pasta base no PDV
#   historico.<log>.formato = nome do arquivo/pasta com tokens
#   historico.<log>.tipo    = arquivo | pasta
#
# Tokens do formato: [..] formato de data | (..) variável LOJA/PDV | {..} texto
# fixo. O texto fora dos delimitadores também é literal.
_RE_TOKEN_FORMATO = re.compile(r'\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\}')
_RE_TOKEN_SO_DATA = re.compile(r'^[ymdhs\-_/.: ]+$')

def _token_eh_data(conteudo):
    """True quando o token representa um formato de data — só contém marcadores
    de data e separadores. A classificação é pelo conteúdo e não pelo tipo de
    delimitador, para tolerar [..] e {..} trocados na configuração."""
    c = conteudo.lower()
    return bool(c) and bool(_RE_TOKEN_SO_DATA.match(c)) and bool(re.search(r'[ymd]', c))

def _formatar_token_data(conteudo, data):
    """Converte um formato tipo 'yyyy-mm-dd' para a data informada."""
    s = conteudo.replace('yyyy', '%Y').replace('yy', '%y')
    s = s.replace('mm', '%m').replace('dd', '%d')
    s = s.replace('hh', '%H').replace('ss', '%S')
    return data.strftime(s)

def _resolver_formato(formato, data, loja, pdv):
    """Resolve o formato do nome de arquivo/pasta histórico para uma data.
    Ex.: '{MFDE}(LOJA)(PDV)[yyyymmdd].zip' -> 'MFDE004545020260802.zip'"""
    partes = []
    pos = 0
    for m in _RE_TOKEN_FORMATO.finditer(formato):
        partes.append(formato[pos:m.start()])
        conteudo = next(g for g in m.groups() if g is not None)
        chave = conteudo.strip().upper()
        if chave == 'LOJA':
            partes.append(loja)
        elif chave == 'PDV':
            partes.append(pdv)
        elif _token_eh_data(conteudo):
            partes.append(_formatar_token_data(conteudo, data))
        else:
            partes.append(conteudo)
        pos = m.end()
    partes.append(formato[pos:])
    return ''.join(partes)

def _resolver_log_historico(log_item, props, data, loja, pdv, base_pdv):
    """Monta o caminho do log de um dia anterior.
    Retorna (caminho_absoluto, caminho_relativo, tipo) ou (None, None, None)
    quando o log não tem configuração de histórico no properties."""
    base    = props.get(f'historico.{log_item}.caminho', '')
    formato = props.get(f'historico.{log_item}.formato', '')
    tipo    = props.get(f'historico.{log_item}.tipo', 'arquivo').strip().lower()
    if not base or not formato:
        return None, None, None

    nome = _resolver_formato(formato, data, loja.zfill(4), pdv.zfill(3))
    caminho_relativo = os.path.join(base.rstrip('\\/'), nome)
    if base_pdv:
        caminho_absoluto = os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\'))
    else:
        caminho_absoluto = caminho_relativo
    return caminho_absoluto, caminho_relativo, tipo

def _parsear_data_solicitacao(texto):
    """Converte o campo 'Data' do e-mail em date. Retorna None se ausente/inválida."""
    texto = (texto or '').strip()
    if not texto:
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None

# ---------------------------------------------------------------------------
# Funcionalidade 1: Solicitação de Logs
# ---------------------------------------------------------------------------
def processar_solicitacao_log(imap, num, corpo, props, email_user, email_pass):
    """Extrai os logs pedidos e devolve o zip por e-mail.

    Serve os dois canais de entrada: o e-mail [Solicitação Log] e a fila do relay
    (modo tunnel). No caminho do relay não existe mensagem para marcar como lida,
    então `imap` e `num` chegam como None — a resposta com os arquivos continua
    sendo por e-mail nos dois casos.
    """
    pid      = extrair_campo(corpo, 'PID')
    destino  = extrair_campo(corpo, 'Destino')
    loja     = extrair_campo(corpo, 'Loja')
    pdv      = extrair_campo(corpo, 'PDV')
    logs     = extrair_campo(corpo, 'Logs')
    data_txt = extrair_campo(corpo, 'Data')

    # Data de referência: quando anterior à data atual, os logs vêm dos arquivos
    # históricos (configuração historico.<log>.*); caso contrário, do dia corrente.
    hoje      = datetime.now().date()
    data_ref  = _parsear_data_solicitacao(data_txt)
    if data_ref and data_ref > hoje:
        log(f'Data solicitada ({data_ref:%d/%m/%Y}) é futura — usando os logs do dia atual.', 'warning')
        data_ref = None
    historico = data_ref is not None and data_ref < hoje
    data_logs = data_ref or hoje

    log(f'[SolicitacaoLog] PID={pid} | Loja={loja} | PDV={pdv} | Logs={logs} | '
        f'Data={data_logs:%d/%m/%Y} ({"histórico" if historico else "dia atual"})')

    data_atual    = datetime.now().strftime('%d%m%Y%H%M%S')
    agora_fmt     = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    data_logs_fmt = data_logs.strftime('%d/%m/%Y')
    if historico:
        nome_zip = f'LOG-{loja}-{pdv}-HIST{data_logs:%Y%m%d}-{data_atual}.zip'
    else:
        nome_zip = f'LOG-{loja}-{pdv}-{data_atual}.zip'
    nome_zip_path = os.path.join(BASE_DIR, nome_zip)
    status_envio  = 'Sucesso'
    erro_msg      = ''

    # Rastreia status de cada arquivo: {'nome': str, 'caminho': str, 'status': 'ok'|'nao_encontrado'|'sem_config'}
    arquivos_status = []

    try:
        base_pdv      = props.get(f'PDV_{pdv}', '')
        windows_user  = props.get('windows_user', '')
        windows_senha = props.get('windows_senha', '')

        if not base_pdv:
            log(f'Aviso: IP não configurado para PDV {pdv}', 'warning')
        else:
            autenticar_unc(base_pdv, windows_user, windows_senha)

        versao = ler_versao_pdv(base_pdv, props)
        log(f'Versão do PDV {pdv}: {versao}')

        lista_logs = [l.strip() for l in logs.split(',') if l.strip()]
        caminhos_arquivos = []
        # Pastas a compactar, deduplicadas: quando vários logs apontam para a
        # mesma pasta, ela entra no zip uma única vez.
        pastas_a_compactar = {}

        for log_item in lista_logs:
            if historico:
                caminho_absoluto, caminho_relativo, tipo = _resolver_log_historico(
                    log_item, props, data_ref, loja, pdv, base_pdv)
                if not caminho_absoluto:
                    log(f'Aviso: log "{log_item}" sem configuração de histórico no properties.', 'warning')
                    arquivos_status.append({'nome': log_item, 'caminho': '—', 'status': 'sem_config'})
                    continue
            else:
                tipo = 'arquivo'
                caminho_relativo = props.get(log_item)
                if not caminho_relativo:
                    log(f'Aviso: caminho do log "{log_item}" não encontrado no properties.', 'warning')
                    arquivos_status.append({'nome': log_item, 'caminho': '—', 'status': 'sem_config'})
                    continue

                if log_item.strip().upper() == 'MFDE':
                    loja_fmt  = loja.zfill(4)
                    pdv_fmt   = pdv.zfill(3)
                    nome_mfde = f"MFDE{loja_fmt}{pdv_fmt}{datetime.now().strftime('%Y%m%d')}"
                    base_mfde = props.get('MFDE', 'Logs')
                    caminho_absoluto = os.path.join(f'\\\\{base_pdv}\\C$', base_mfde, nome_mfde) if base_pdv else os.path.join(base_mfde, nome_mfde)
                else:
                    caminho_absoluto = os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\')) if base_pdv else caminho_relativo

            prefixo = '[Histórico] ' if historico else ''
            log(f'{prefixo}{"Pasta" if tipo == "pasta" else "Arquivo"} {log_item} -> {caminho_absoluto}')

            existe = os.path.isdir(caminho_absoluto) if tipo == 'pasta' else os.path.isfile(caminho_absoluto)
            if not existe:
                log(f'Aviso: {"pasta" if tipo == "pasta" else "arquivo"} não encontrado: {caminho_absoluto}', 'warning')
                arquivos_status.append({'nome': log_item, 'caminho': caminho_relativo, 'status': 'nao_encontrado'})
                continue

            if tipo == 'pasta':
                chave = os.path.normcase(os.path.normpath(caminho_absoluto))
                if chave in pastas_a_compactar:
                    log(f'Pasta já incluída por outro log — não será compactada novamente: {caminho_absoluto}')
                else:
                    pastas_a_compactar[chave] = caminho_absoluto
            else:
                caminhos_arquivos.append(caminho_absoluto)
            arquivos_status.append({'nome': log_item, 'caminho': caminho_relativo, 'status': 'ok'})

        if not caminhos_arquivos and not pastas_a_compactar:
            raise FileNotFoundError('Nenhum arquivo válido para compactar.')

        with zipfile.ZipFile(nome_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            for arquivo in caminhos_arquivos:
                zipf.write(arquivo, arcname=os.path.basename(arquivo))
            for caminho_pasta in pastas_a_compactar.values():
                raiz = os.path.basename(caminho_pasta.rstrip('\\/')) or 'pasta'
                for dirpath, _, nomes in os.walk(caminho_pasta):
                    for nome_arq in nomes:
                        completo = os.path.join(dirpath, nome_arq)
                        rel      = os.path.relpath(completo, caminho_pasta)
                        zipf.write(completo, arcname=os.path.join(raiz, rel))
                log(f'Pasta compactada no anexo: {caminho_pasta} -> {raiz}/')

        n_ok    = sum(1 for a in arquivos_status if a['status'] == 'ok')
        n_erro  = len(arquivos_status) - n_ok

        # ---- HTML do e-mail ----
        # O card de data fica destacado em âmbar quando os logs são de um dia anterior
        if historico:
            bg_data, cor_data, rotulo_data = '#fef3c7', '#92400e', 'Data (histórico)'
        else:
            bg_data, cor_data, rotulo_data = '#f8fafc', '#1e3a5f', 'Data'

        linhas_html = ''
        linhas_txt  = ''
        for a in arquivos_status:
            if a['status'] == 'ok':
                bg_row  = ''
                badge   = "<span style='background:#dcfce7;color:#1a7f4b;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10004; Incluído</span>"
            elif a['status'] == 'nao_encontrado':
                bg_row  = "background:#fff5f5;"
                badge   = "<span style='background:#fee2e2;color:#b91c1c;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10006; Não encontrado</span>"
            else:
                bg_row  = "background:#fff5f5;"
                badge   = "<span style='background:#fee2e2;color:#b91c1c;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10006; Sem configuração</span>"

            linhas_html += f"""
            <tr style='{bg_row}'>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:12px;color:#1e293b'>{a['nome']}</td>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280'>{a['caminho']}</td>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap'>{badge}</td>
            </tr>"""
            st_txt = 'OK' if a['status'] == 'ok' else ('NÃO ENCONTRADO' if a['status'] == 'nao_encontrado' else 'SEM CONFIGURAÇÃO')
            linhas_txt += f"\n  [{st_txt}] {a['nome']}  ({a['caminho']})"

        corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='640' cellpadding='0' cellspacing='0' style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Solicitação de Logs</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:14%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Loja</p>
          <p style='margin:4px 0 0;font-size:18px;font-weight:bold;color:#1e3a5f'>{loja}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:12%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PDV</p>
          <p style='margin:4px 0 0;font-size:18px;font-weight:bold;color:#1e3a5f'>{pdv}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:{bg_data};border-radius:6px;text-align:center;width:26%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>{rotulo_data}</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:{cor_data}'>{data_logs_fmt}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:20%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Versão</p>
          <p style='margin:4px 0 0;font-size:13px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{versao}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:24%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{pid}</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:16px 28px'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='background:#dcfce7;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#1a7f4b'>{n_ok}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#1a7f4b;font-weight:bold'>INCLUÍDO(S)</p>
        </td>
        <td width='12'></td>
        <td style='background:#fee2e2;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b91c1c'>{n_erro}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b91c1c;font-weight:bold'>NÃO ENCONTRADO(S)</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:0 28px 8px'>
    <table width='100%' cellpadding='0' cellspacing='0' style='border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;font-size:13px'>
      <thead>
        <tr style='background:#f8fafc'>
          <th style='padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Arquivo</th>
          <th style='padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Caminho</th>
          <th style='padding:10px 14px;text-align:center;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Status</th>
        </tr>
      </thead>
      <tbody>{linhas_html}
      </tbody>
    </table>
  </td></tr>

  <tr><td style='padding:8px 28px 24px'>
    <div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:12px 16px'>
      <p style='margin:0;font-size:12px;color:#0369a1'>
        <strong>Anexo:</strong> {nome_zip}
      </p>
    </div>
  </td></tr>

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora_fmt} &nbsp;|&nbsp; Agent Extrator Log</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

        corpo_txt = (
            f"Solicitação de Logs\n"
            f"PID: {pid} | Loja: {loja} | PDV: {pdv}\n"
            f"Data dos logs: {data_logs_fmt}"
            f"{' (histórico)' if historico else ''}\n"
            f"Resumo: {n_ok} incluído(s) | {n_erro} não encontrado(s)\n"
            f"Anexo: {nome_zip}\n"
            f"{'=' * 60}"
            + linhas_txt
        )

        enviar_email_com_anexo(email_user, email_pass, destino,
                               f'[Logs][{loja}][{pdv}][{pid}]',
                               corpo_txt, nome_zip_path, corpo_html)
        log(f'Email enviado para {destino} | Arquivo: {nome_zip}')
        _marcar_lido(imap, num)

    except Exception as e:
        status_envio = 'Erro'
        erro_msg = str(e)
        log(f'Erro ao processar log PID={pid}: {erro_msg}', 'error')

    gravar_csv_log([pid, destino, loja, pdv, logs, nome_zip, datetime.now().isoformat(), status_envio, erro_msg])

# ---------------------------------------------------------------------------
# Funcionalidade 2: Parametrização PDV
# ---------------------------------------------------------------------------
def alterar_constante_properties(caminho_arquivo, constante, novo_valor):
    """Localiza a linha com 'constante=...' (ativa ou comentada com #) e substitui o valor,
    descomentando se necessário."""
    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(f'Arquivo não encontrado: {caminho_arquivo}')

    with open(caminho_arquivo, 'r', encoding='utf-8', errors='replace') as f:
        linhas = f.readlines()

    # Bate com linha ativa OU comentada: (#)constante=valor
    padrao = re.compile(rf'^(\s*)#?(\s*{re.escape(constante)}\s*=\s*)(.*)$')
    alterado = False
    novas_linhas = []
    for linha in linhas:
        m = padrao.match(linha)
        if m:
            # reconstrói sem o # (descomenta) e com o novo valor
            novas_linhas.append(f'{m.group(1)}{m.group(2)}{novo_valor}\n')
            alterado = True
        else:
            novas_linhas.append(linha)

    if not alterado:
        raise KeyError(f'Constante "{constante}" não encontrada em {caminho_arquivo}')

    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        f.writelines(novas_linhas)

def comentar_constante_properties(caminho_arquivo, constante):
    """Comenta a linha com 'constante=...' prefixando com '#'. Ignora se já comentada ou ausente."""
    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(f'Arquivo não encontrado: {caminho_arquivo}')
    with open(caminho_arquivo, 'r', encoding='utf-8', errors='replace') as f:
        linhas = f.readlines()
    padrao = re.compile(rf'^(\s*)({re.escape(constante)}\s*=.*)$')
    novas_linhas = []
    for linha in linhas:
        m = padrao.match(linha)
        if m:
            novas_linhas.append(f'{m.group(1)}#{m.group(2)}\n')
        else:
            novas_linhas.append(linha)
    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        f.writelines(novas_linhas)

def sobrescrever_arquivo(caminho_arquivo, novo_valor):
    """Remove todo o conteúdo do arquivo e escreve apenas o novo valor."""
    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(f'Arquivo não encontrado: {caminho_arquivo}')
    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        f.write(novo_valor + '\n')

def _descobrir_porta_bematech(ip):
    """Descobre a porta COM da impressora Bematech MP-4200 TH no PDV remoto via registro remoto.
    Retorna string como 'COM3' ou None se não encontrar."""
    def _eh_bematech(s):
        s = s.lower()
        return 'bematech' in s or 'mp-4200' in s or 'mp4200' in s

    def _extrair_com(desc):
        m = re.search(r'\(COM(\d+)\)', desc, re.IGNORECASE)
        return f'COM{m.group(1)}' if m else None

    try:
        conn = winreg.ConnectRegistry(ip, winreg.HKEY_LOCAL_MACHINE)

        # ── Abordagem 1: classe serial – lê PortName direto ou via Device Parameters ──
        SERIAL_CLASS = r'SYSTEM\CurrentControlSet\Control\Class\{4D36E978-E325-11CE-BFC1-08002BE10318}'
        try:
            base = winreg.OpenKey(conn, SERIAL_CLASS)
            idx = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(base, idx); idx += 1
                    if sub_name == 'Properties':
                        continue
                    try:
                        sub = winreg.OpenKey(base, sub_name)
                        for val_name in ('FriendlyName', 'DriverDesc'):
                            try:
                                desc, _ = winreg.QueryValueEx(sub, val_name)
                                log(f'[Impressora] classe/{sub_name} {val_name}={desc}')
                                if _eh_bematech(desc):
                                    p = _extrair_com(desc)
                                    if p:
                                        return p
                                    for loc_key in (sub,):
                                        for pn_loc in (loc_key, None):
                                            try:
                                                key = (winreg.OpenKey(loc_key, 'Device Parameters')
                                                       if pn_loc is None else loc_key)
                                                porta, _ = winreg.QueryValueEx(key, 'PortName')
                                                log(f'[Impressora] PortName={porta}')
                                                return porta.strip()
                                            except OSError:
                                                pass
                            except FileNotFoundError:
                                pass
                    except OSError:
                        pass
                except OSError:
                    break
        except OSError:
            pass

        # ── Abordagem 2: árvore ENUM – FriendlyName contém "(COMx)" ──────────────
        log(f'[Impressora] Classe serial sem PortName; buscando na árvore ENUM de {ip}')
        try:
            enum = winreg.OpenKey(conn, r'SYSTEM\CurrentControlSet\Enum')
            n_bus = winreg.QueryInfoKey(enum)[0]
            for bi in range(n_bus):
                try:
                    bus_name = winreg.EnumKey(enum, bi)
                    bus_key  = winreg.OpenKey(enum, bus_name)
                    for di in range(winreg.QueryInfoKey(bus_key)[0]):
                        try:
                            dev_name = winreg.EnumKey(bus_key, di)
                            dev_key  = winreg.OpenKey(bus_key, dev_name)
                            for ii in range(winreg.QueryInfoKey(dev_key)[0]):
                                try:
                                    inst_name = winreg.EnumKey(dev_key, ii)
                                    inst_key  = winreg.OpenKey(dev_key, inst_name)
                                    for fn_key in ('FriendlyName', 'DeviceDesc'):
                                        try:
                                            fn, _ = winreg.QueryValueEx(inst_key, fn_key)
                                            if _eh_bematech(fn):
                                                log(f'[Impressora] ENUM {bus_name}\\{dev_name} {fn_key}={fn}')
                                                p = _extrair_com(fn)
                                                if p:
                                                    return p
                                                try:
                                                    dp = winreg.OpenKey(inst_key, 'Device Parameters')
                                                    porta, _ = winreg.QueryValueEx(dp, 'PortName')
                                                    log(f'[Impressora] PortName={porta}')
                                                    return porta.strip()
                                                except OSError:
                                                    pass
                                        except FileNotFoundError:
                                            pass
                                except OSError:
                                    pass
                        except OSError:
                            pass
                except OSError:
                    pass
        except OSError:
            pass

        log(f'[Impressora] Bematech não encontrada no registro de {ip}', 'warning')
    except Exception as e:
        log(f'[Impressora] Erro ao acessar registro remoto de {ip}: {e}', 'error')
    return None


def _processar_parametrizacao_pdv(pid, loja, pdv, base_pdv, lista_params, props, email_user, email_pass, destino):
    """Processa parametrização para um único PDV e envia e-mail de resultado."""
    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')

    if not base_pdv:
        log(f'[Parametrizacao] Aviso: IP não configurado para PDV {pdv}', 'warning')
    else:
        try:
            autenticar_unc(base_pdv, windows_user, windows_senha)
        except PermissionError as e:
            log(str(e), 'error')

    versao    = ler_versao_pdv(base_pdv, props)
    agora_fmt = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    log(f'[Parametrizacao] Versão do PDV {pdv}: {versao}')

    resultados = []

    for param in lista_params:

        # ── Impressora Virtual ──────────────────────────────────────────────
        if param == 'impressora_virtual':
            status_param = 'Sucesso'
            erro_param   = ''
            arq_periferico = 'p2k\\bin\\parametrosGeraisPerifericos.properties'
            arq_geral      = 'p2k\\bin\\parametrosGeraisPDV.properties'
            try:
                loja_sem_zeros = loja.lstrip('0') or '0'
                token = int(loja_sem_zeros + pdv) * 8963
                log(f'[Impressora Virtual] Loja={loja_sem_zeros} PDV={pdv} token={token}')

                if base_pdv:
                    path_per = os.path.join(f'\\\\{base_pdv}\\C$', arq_periferico)
                    path_ger = os.path.join(f'\\\\{base_pdv}\\C$', arq_geral)
                else:
                    path_per = arq_periferico
                    path_ger = arq_geral

                alterar_constante_properties(path_per, 'IMPRESSORA_PRINCIPAL', _imp_virtual_hash(props))
                alterar_constante_properties(path_ger, 'TOKEN_HABILITA_IMP_NAO_FISCAL_VIRTUAL', str(token))
                alterar_constante_properties(path_ger, 'VALIDA_PAPEL_INICIO_VENDA', 'false')

                log(f'[OK] impressora_virtual: IMPRESSORA_PRINCIPAL, TOKEN e VALIDA_PAPEL configurados')
                resultados.append({'param': 'impressora_virtual', 'constante': 'IMPRESSORA_PRINCIPAL / TOKEN / VALIDA_PAPEL',
                                   'arquivo': arq_periferico, 'esperado': f'token={token}', 'atual': f'token={token}',
                                   'status': 'OK', 'erro': ''})
            except Exception as e:
                erro_param   = str(e)
                status_param = 'Erro'
                log(f'Erro impressora_virtual: {erro_param}', 'error')
                resultados.append({'param': 'impressora_virtual', 'constante': '', 'arquivo': arq_periferico,
                                   'esperado': '', 'atual': '', 'status': 'ERRO', 'erro': erro_param})
            gravar_csv_param([pid, loja, pdv, param, arq_periferico, 'IMPRESSORA_PRINCIPAL',
                              'virtual', datetime.now().isoformat(), status_param, erro_param])
            continue

        # ── Impressora Física ───────────────────────────────────────────────
        if param == 'impressora_fisica':
            status_param = 'Sucesso'
            erro_param   = ''
            arq_periferico = 'p2k\\bin\\parametrosGeraisPerifericos.properties'
            arq_geral      = 'p2k\\bin\\parametrosGeraisPDV.properties'
            try:
                porta = _descobrir_porta_bematech(base_pdv) if base_pdv else None
                if not porta:
                    raise RuntimeError('Impressora Bematech MP-4200 TH não encontrada no PDV remoto')
                hash_porta = _imp_fisica_portas(props).get(porta.upper())
                if not hash_porta:
                    raise RuntimeError(f'Porta {porta} não configurada em agent.properties (impressora.fisica.{porta})')

                if base_pdv:
                    path_per = os.path.join(f'\\\\{base_pdv}\\C$', arq_periferico)
                    path_ger = os.path.join(f'\\\\{base_pdv}\\C$', arq_geral)
                else:
                    path_per = arq_periferico
                    path_ger = arq_geral

                alterar_constante_properties(path_per, 'IMPRESSORA_PRINCIPAL', hash_porta)
                alterar_constante_properties(path_ger, 'VALIDA_PAPEL_INICIO_VENDA', 'true')
                comentar_constante_properties(path_ger, 'TOKEN_HABILITA_IMP_NAO_FISCAL_VIRTUAL')
                log(f'[OK] impressora_fisica: porta={porta} | IMPRESSORA_PRINCIPAL, VALIDA_PAPEL=true, TOKEN comentado')
                resultados.append({'param': 'impressora_fisica', 'constante': 'IMPRESSORA_PRINCIPAL / VALIDA_PAPEL / TOKEN',
                                   'arquivo': arq_periferico, 'esperado': porta, 'atual': porta,
                                   'status': 'OK', 'erro': ''})
            except Exception as e:
                erro_param   = str(e)
                status_param = 'Erro'
                log(f'Erro impressora_fisica: {erro_param}', 'error')
                resultados.append({'param': 'impressora_fisica', 'constante': '', 'arquivo': arq_periferico,
                                   'esperado': '', 'atual': '', 'status': 'ERRO', 'erro': erro_param})
            gravar_csv_param([pid, loja, pdv, param, arq_periferico, 'IMPRESSORA_PRINCIPAL',
                              'fisica', datetime.now().isoformat(), status_param, erro_param])
            continue

        # ── Parâmetros genéricos (agent.properties) ─────────────────────────
        caminho_relativo = props.get(f'{param}.arquivo')
        constante        = props.get(f'{param}.constante')
        novo_valor       = props.get(f'{param}.valor')
        status_param     = 'Sucesso'
        erro_param       = ''

        eh_dat = param == 'serv-config-comunicacao.dat'
        log(f'Processando parametro: {param} | {"sobrescrita" if eh_dat else f"constante={constante}"} | valor={novo_valor}')

        try:
            if not caminho_relativo:
                raise KeyError(f'"{param}.arquivo" não definido no agent.properties')
            if novo_valor is None:
                raise KeyError(f'"{param}.valor" não definido no agent.properties')
            if not eh_dat and not constante:
                raise KeyError(f'"{param}.constante" não definido no agent.properties')

            if base_pdv:
                caminho_absoluto = os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\'))
            else:
                caminho_absoluto = caminho_relativo

            if eh_dat:
                log(f'Sobrescrevendo: {caminho_absoluto} | valor={novo_valor}')
                sobrescrever_arquivo(caminho_absoluto, novo_valor)
                log(f'[OK] {param}: arquivo sobrescrito com "{novo_valor}"')
                resultados.append({'param': param, 'constante': '', 'arquivo': caminho_relativo or '',
                                   'esperado': novo_valor or '', 'atual': novo_valor or '',
                                   'status': 'OK', 'erro': ''})
            else:
                log(f'Editando: {caminho_absoluto} | {constante}={novo_valor}')
                alterar_constante_properties(caminho_absoluto, constante, novo_valor)
                log(f'[OK] {param}: {constante} alterado para "{novo_valor}"')
                resultados.append({'param': param, 'constante': constante or '', 'arquivo': caminho_relativo or '',
                                   'esperado': novo_valor or '', 'atual': novo_valor or '',
                                   'status': 'OK', 'erro': ''})

        except Exception as e:
            status_param = 'Erro'
            erro_param   = str(e)
            log(f'Erro no parametro {param}: {erro_param} — continuando para o proximo', 'error')
            resultados.append({'param': param, 'constante': constante or '', 'arquivo': caminho_relativo or '',
                               'esperado': novo_valor or '', 'atual': '',
                               'status': 'ERRO', 'erro': erro_param})

        gravar_csv_param([pid, loja, pdv, param, caminho_relativo or '', constante or '',
                          novo_valor or '', datetime.now().isoformat(), status_param, erro_param])

    if not destino:
        return

    n_ok   = sum(1 for r in resultados if r['status'] == 'OK')
    n_erro = len(resultados) - n_ok

    def truncar(valor, limite=40):
        if not valor: return '—', ''
        return (valor[:limite] + '…', valor) if len(valor) > limite else (valor, valor)

    ICO = {'OK': '✔',       'ERRO': '✖'}
    # Estilos repetidos por célula viram classes no <style> — o Gmail corta a
    # exibição de e-mails acima de ~102 KB, e a repetição inflava o HTML.
    CLS = {'OK': 'ok', 'ERRO': 'er'}

    linhas_html = ''
    linhas_txt  = ''
    for r in resultados:
        st      = r['status']
        cls_row = '' if st == 'OK' else " class='rw'"
        badge   = f"<span class='b {CLS[st]}'>{ICO[st]} {st}</span>"
        label_c = (f"<br><span class='cn'>{r['constante']}</span>"
                   if r['constante'] else '')
        esp_s, esp_f = truncar(r['esperado'])
        if r['erro']:
            atu_s, atu_f = truncar(r['erro'])
            atu_cls = 'c e'
        else:
            atu_s, atu_f = truncar(r['atual'])
            atu_cls = 'c'

        linhas_html += f"""
            <tr{cls_row}>
              <td class='k'><span class='m'>{r['param']}</span>{label_c}</td>
              <td class='c' title='{esp_f}'><span class='v'>{esp_s}</span></td>
              <td class='{atu_cls}' title='{atu_f}'><span class='v'>{atu_s}</span></td>
              <td class='s'>{badge}</td>
            </tr>"""
        linhas_txt += f"\n[{st}] {r['param']} | {r['esperado'] or r['erro']}"

    corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<style>
.k{{padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px;max-width:160px}}
.c{{padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px;max-width:140px;color:#374151}}
.e{{color:#b91c1c}}
.s{{padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px;text-align:center;white-space:nowrap}}
.v{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.m{{font-family:monospace;font-size:12px;color:#1e293b}}
.cn{{color:#6b7280;font-size:11px}}
.b{{font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block}}
.ok{{background:#dcfce7;color:#1a7f4b}}
.er{{background:#fee2e2;color:#b91c1c}}
.rw{{background:#fff5f5}}
.h{{padding:9px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb}}
.hc{{padding:9px 14px;text-align:center;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb;width:90px}}
</style></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='640' cellpadding='0' cellspacing='0'
       style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Parametrização PDV</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:20%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Loja</p>
          <p style='margin:4px 0 0;font-size:18px;font-weight:bold;color:#1e3a5f'>{loja}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:16%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PDV</p>
          <p style='margin:4px 0 0;font-size:18px;font-weight:bold;color:#1e3a5f'>{pdv}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:26%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Versão</p>
          <p style='margin:4px 0 0;font-size:13px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{versao}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:30%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:13px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{pid}</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:16px 28px'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='background:#dcfce7;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#1a7f4b'>{n_ok}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#1a7f4b;font-weight:bold'>SUCESSO</p>
        </td>
        <td width='12'></td>
        <td style='background:#fee2e2;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b91c1c'>{n_erro}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b91c1c;font-weight:bold'>ERRO</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:0 28px 24px'>
    <table width='100%' cellpadding='0' cellspacing='0'
           style='border:1px solid #e5e7eb;border-radius:6px;overflow:hidden'>
      <thead>
        <tr style='background:#f8fafc'>
          <th class='h'>Parâmetro</th>
          <th class='h'>Esperado</th>
          <th class='h'>Atual</th>
          <th class='hc'>Status</th>
        </tr>
      </thead>
      <tbody>{linhas_html}
      </tbody>
    </table>
  </td></tr>

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora_fmt} &nbsp;|&nbsp; Agent Extrator Log</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

    corpo_txt = (
        f'Parametrização PDV\nPID: {pid} | Loja: {loja} | PDV: {pdv} | Versão: {versao}\n'
        f'Resumo: {n_ok} sucesso | {n_erro} erro'
        + linhas_txt
    )

    try:
        enviar_email_html(email_user, email_pass, destino,
                          f'[Parametrização][{loja}][{pdv}][{pid}]',
                          corpo_html, corpo_txt)
        log(f'[Parametrizacao] Resposta enviada para {destino} — Loja {loja} PDV {pdv}')
    except Exception as e:
        log(f'[Parametrizacao] Erro ao enviar resposta: {e}', 'error')


def processar_parametrizacao(imap, num, corpo, props, email_user, email_pass):
    pid        = extrair_campo(corpo, 'PID')
    destino    = extrair_campo(corpo, 'Destino')
    selecao    = extrair_campo(corpo, 'Selecao')
    parametros = extrair_campo(corpo, 'Parametros')

    log(f'[Parametrizacao] PID={pid} | Selecao={selecao} | Parametros={parametros}')

    lista_params = [p.strip() for p in parametros.split(',') if p.strip()]
    grupos = _parsear_selecao(selecao, props)

    for loja, pdvs in grupos.items():
        for pdv in pdvs:
            base_pdv = props.get(f'PDV_{pdv}', '')
            if not base_pdv:
                log(f'[Parametrizacao] IP não configurado para PDV {pdv}', 'warning')
            _processar_parametrizacao_pdv(pid, loja, pdv, base_pdv, lista_params, props, email_user, email_pass, destino)

    _marcar_lido(imap, num)

# ---------------------------------------------------------------------------
# Funcionalidade 3: Verificar Parametrização
# ---------------------------------------------------------------------------
def ler_valor_constante(caminho_arquivo, constante):
    """Lê o valor atual de uma constante em um arquivo .properties."""
    padrao = re.compile(rf'^\s*{re.escape(constante)}\s*=\s*(.*)$')
    with open(caminho_arquivo, 'r', encoding='utf-8', errors='replace') as f:
        for linha in f:
            m = padrao.match(linha)
            if m:
                return m.group(1).strip()
    return None

def processar_verificar_parametrizacao(imap, num, corpo, props, email_user, email_pass):
    pid     = extrair_campo(corpo, 'PID')
    destino = extrair_campo(corpo, 'Destino')
    selecao = extrair_campo(corpo, 'Selecao')

    log(f'[VerificarParametrizacao] PID={pid} | Destino={destino} | Selecao={selecao}')

    if not selecao:
        log('Campo Selecao ausente no e-mail de verificação.', 'error')
        _marcar_lido(imap, num)
        return

    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')
    agora         = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    grupos = _parsear_selecao(selecao, props)

    ICO = {'OK': '✔', 'DIVERGENTE': '⚠', 'ERRO': '✖'}
    # Classe do badge por status. O relatório cresce com o número de PDVs e o
    # Gmail corta a exibição do e-mail acima de ~102 KB — repetir o estilo em
    # cada célula estourava esse limite. As classes ficam no <style> do <head>.
    CLS = {'OK': 'ok', 'DIVERGENTE': 'dv', 'ERRO': 'er'}

    def truncar(valor, limite=45):
        if not valor:
            return '—', ''
        if len(valor) <= limite:
            return valor, valor
        return valor[:limite] + '…', valor

    total_ok = total_div = total_erro = 0
    secoes_html = ''
    secoes_txt  = ''

    for loja, pdvs in grupos.items():
        secoes_html += f"""
        <tr><td colspan='4' class='lj'><span class='ljn'>Loja {loja}</span></td></tr>"""
        secoes_txt += f'\n\n=== Loja {loja} ==='

        for pdv in pdvs:
            base_pdv = props.get(f'PDV_{pdv}', '')
            if not base_pdv:
                log(f'Aviso: IP não configurado para PDV {pdv}', 'warning')
            else:
                try:
                    autenticar_unc(base_pdv, windows_user, windows_senha)
                except PermissionError as e:
                    log(str(e), 'error')

            resultados = _verificar_pdv_params(base_pdv, props)
            resultados.append(_verificar_impressora_pdv(base_pdv, loja, pdv, props))

            n_ok  = sum(1 for r in resultados if r['status'] == 'OK')
            n_div = sum(1 for r in resultados if r['status'] == 'DIVERGENTE')
            n_err = sum(1 for r in resultados if r['status'] == 'ERRO')
            total_ok += n_ok; total_div += n_div; total_erro += n_err

            resumo_badges = (
                f"<span class='b ok mr'>{n_ok} OK</span>"
                f"<span class='b dv mr'>{n_div} DIV</span>"
                f"<span class='b er'>{n_err} ERRO</span>"
            )

            secoes_html += f"""
        <tr><td colspan='4' style='padding:8px 28px 4px'>
          <span class='pdv'>PDV {pdv}</span>
          &nbsp;&nbsp;{resumo_badges}
        </td></tr>"""
            secoes_txt += f'\n  --- PDV {pdv} ---'

            linhas_html = ''
            for r in resultados:
                st  = r['status']
                ico = ICO[st]; cls = CLS[st]
                nome_exibido = r['constante'] if r.get('constante') else r['param']
                esp_curto, esp_full = truncar(r.get('esperado', ''))
                if r.get('erro') and not r.get('atual'):
                    atu_curto, atu_full = truncar(r['erro'])
                    atu_cls = 'c e'
                else:
                    atu_curto, atu_full = truncar(r.get('atual', ''))
                    atu_cls = 'c'
                linhas_html += f"""
            <tr>
              <td class='p'><span class='m'>{nome_exibido}</span></td>
              <td class='c' title='{esp_full}'><span class='v'>{esp_curto}</span></td>
              <td class='{atu_cls}' title='{atu_full}'><span class='v'>{atu_curto}</span></td>
              <td class='s'><span class='b {cls}'>{ico} {st}</span></td>
            </tr>"""
                secoes_txt += (
                    f"\n  [{st}] {nome_exibido}"
                    f"\n    Esperado: {r.get('esperado', '')}"
                    f"\n    Atual   : {r.get('atual', '') or r.get('erro', '')}\n"
                )

            secoes_html += f"""
        <tr><td colspan='4' style='padding:0 28px 12px'>
          <table width='100%' cellpadding='0' cellspacing='0' class='t'>
            <thead><tr style='background:#f8fafc'>
              <th class='h'>Parâmetro</th>
              <th class='h'>Esperado</th>
              <th class='h'>Atual</th>
              <th class='hc'>Status</th>
            </tr></thead>
            <tbody>{linhas_html}</tbody>
          </table>
        </td></tr>"""

    if destino:
        corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<style>
.p{{padding:8px 14px;border-bottom:1px solid #e5e7eb;font-size:11px;max-width:150px}}
.c{{padding:8px 14px;border-bottom:1px solid #e5e7eb;font-size:11px;max-width:150px;color:#374151}}
.e{{color:#b91c1c}}
.s{{padding:8px 14px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap}}
.v{{white-space:nowrap;overflow:hidden;display:block;max-width:140px;text-overflow:ellipsis}}
.m{{font-family:monospace;color:#1e293b}}
.b{{font-weight:bold;font-size:10px;padding:2px 8px;border-radius:10px;display:inline-block}}
.mr{{margin-right:4px}}
.ok{{background:#dcfce7;color:#1a7f4b}}
.dv{{background:#fef3c7;color:#b45309}}
.er{{background:#fee2e2;color:#b91c1c}}
.h{{padding:8px 14px;text-align:left;color:#6b7280;font-size:10px;text-transform:uppercase;border-bottom:1px solid #e5e7eb}}
.hc{{padding:8px 14px;text-align:center;color:#6b7280;font-size:10px;text-transform:uppercase;border-bottom:1px solid #e5e7eb}}
.t{{border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;font-size:12px}}
.lj{{padding:14px 28px 6px;background:#f8fafc;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb}}
.ljn{{font-size:12px;font-weight:bold;color:#1e3a5f;text-transform:uppercase;letter-spacing:.5px}}
.pdv{{font-size:11px;font-weight:bold;color:#374151}}
</style></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='700' cellpadding='0' cellspacing='0' style='background:#ffffff;border-radius:8px;
       overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Verificar Parametrização</h1>
    <p style='margin:6px 0 0;color:#bfdbfe;font-size:12px;font-family:monospace'>PID: {pid}</p>
  </td></tr>

  <tr><td style='padding:16px 28px'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='background:#dcfce7;border-radius:6px;padding:10px;text-align:center;width:32%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#1a7f4b'>{total_ok}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#1a7f4b;font-weight:bold'>OK</p>
        </td>
        <td width='10'></td>
        <td style='background:#fef3c7;border-radius:6px;padding:10px;text-align:center;width:32%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b45309'>{total_div}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b45309;font-weight:bold'>DIVERGENTE</p>
        </td>
        <td width='10'></td>
        <td style='background:#fee2e2;border-radius:6px;padding:10px;text-align:center;width:32%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b91c1c'>{total_erro}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b91c1c;font-weight:bold'>ERRO</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <table width='700' cellpadding='0' cellspacing='0'>{secoes_html}
  </table>

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora} &nbsp;|&nbsp; Agent Extrator Log</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

        corpo_txt = (
            f"Verificar Parametrização | PID: {pid}\n"
            f"Resumo: {total_ok} OK | {total_div} DIVERGENTE | {total_erro} ERRO\n"
            f"{'=' * 60}"
            + secoes_txt
        )

        try:
            enviar_email_html(email_user, email_pass, destino,
                              f'[Verificar Parametrização][{pid}]',
                              corpo_html, corpo_txt)
            log(f'Resposta de verificação enviada para {destino}')
        except Exception as e:
            log(f'Erro ao enviar resposta de verificação: {e}', 'error')

    _marcar_lido(imap, num)

# ---------------------------------------------------------------------------
# Funcionalidade 4: Relatório Parametrização
# ---------------------------------------------------------------------------
def _verificar_impressora_pdv(base_pdv, loja, pdv, props):
    """Verifica o estado da impressora de um PDV.
    Retorna dict {param, constante, esperado, atual, status, erro}."""
    arq_per_rel = 'p2k\\bin\\parametrosGeraisPerifericos.properties'
    arq_per_abs = os.path.join(f'\\\\{base_pdv}\\C$', arq_per_rel) if base_pdv else arq_per_rel

    r = {'param': 'impressora', 'constante': 'IMPRESSORA_PRINCIPAL',
         'esperado': '', 'atual': '', 'status': 'ERRO', 'erro': ''}
    try:
        if not os.path.exists(arq_per_abs):
            r['erro'] = 'Arquivo parametrosGeraisPerifericos.properties não encontrado'
            return r

        valor          = ler_valor_constante(arq_per_abs, 'IMPRESSORA_PRINCIPAL') or ''
        hash_virtual   = _imp_virtual_hash(props)
        portas_fisicas = _imp_fisica_portas(props)
        r['atual'] = valor

        if hash_virtual and valor == hash_virtual:
            arq_ger_abs = os.path.join(f'\\\\{base_pdv}\\C$', 'p2k\\bin\\parametrosGeraisPDV.properties') if base_pdv else 'p2k\\bin\\parametrosGeraisPDV.properties'
            loja_sem_zeros = loja.lstrip('0') or '0'
            token_esp = str(int(loja_sem_zeros + pdv) * 8963)
            token_atu = ler_valor_constante(arq_ger_abs, 'TOKEN_HABILITA_IMP_NAO_FISCAL_VIRTUAL') if os.path.exists(arq_ger_abs) else None
            papel_atu = ler_valor_constante(arq_ger_abs, 'VALIDA_PAPEL_INICIO_VENDA') if os.path.exists(arq_ger_abs) else None
            token_ok  = token_atu is not None and token_atu.strip() == token_esp
            papel_ok  = papel_atu is not None and papel_atu.strip().lower() == 'false'
            r['param']    = 'impressora (Virtual)'
            r['esperado'] = hash_virtual
            if token_ok and papel_ok:
                r['status'] = 'OK'
            else:
                problemas = []
                if not token_ok:
                    problemas.append(f'TOKEN esperado {token_esp}, lido {token_atu!r}')
                if not papel_ok:
                    problemas.append(f'VALIDA_PAPEL esperado false, lido {papel_atu!r}')
                r['status'] = 'DIVERGENTE'
                r['erro']   = '; '.join(problemas)
        else:
            porta = next((p for p, h in portas_fisicas.items() if h == valor), None)
            if porta:
                r['param']    = f'impressora (Física {porta})'
                r['esperado'] = valor
                r['status']   = 'OK'
            elif valor:
                r['param']    = 'impressora'
                r['esperado'] = '(virtual ou física conhecida)'
                r['status']   = 'DIVERGENTE'
                r['erro']     = 'Hash de IMPRESSORA_PRINCIPAL não reconhecido'
            else:
                r['param']    = 'impressora'
                r['esperado'] = '(virtual ou física conhecida)'
                r['status']   = 'DIVERGENTE'
                r['erro']     = 'IMPRESSORA_PRINCIPAL ausente ou vazio'
    except Exception as e:
        r['erro'] = str(e)
    return r


def _verificar_pdv_params(base_pdv, props):
    """Verifica todos os parâmetros de um PDV. Retorna lista de dicts {param, status, erro}."""
    params = sorted({k[:-8] for k in props if k.endswith('.arquivo')})
    resultados = []
    for param in params:
        caminho_relativo = props.get(f'{param}.arquivo', '')
        constante        = props.get(f'{param}.constante', '')
        valor_esperado   = props.get(f'{param}.valor', '')
        eh_dat           = not constante

        caminho_absoluto = (
            os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\'))
            if base_pdv else caminho_relativo
        )

        r = {'param': param, 'constante': constante, 'esperado': valor_esperado,
             'atual': '', 'status': 'ERRO', 'erro': ''}
        try:
            if not os.path.exists(caminho_absoluto):
                r['erro'] = 'Arquivo não encontrado'
            elif eh_dat:
                with open(caminho_absoluto, 'r', encoding='utf-8', errors='replace') as f:
                    r['atual'] = f.read().strip()
                r['status'] = 'OK' if r['atual'] == valor_esperado.strip() else 'DIVERGENTE'
            else:
                atual = ler_valor_constante(caminho_absoluto, constante)
                if atual is None:
                    r['erro'] = f'Constante "{constante}" não encontrada'
                else:
                    r['atual']  = atual
                    r['status'] = 'OK' if atual == valor_esperado.strip() else 'DIVERGENTE'
        except Exception as e:
            r['erro'] = str(e)

        resultados.append(r)
    return resultados


def processar_relatorio_parametrizacao(imap, num, corpo, props, email_user, email_pass):
    pid     = extrair_campo(corpo, 'PID')
    destino = extrair_campo(corpo, 'Destino')
    selecao = extrair_campo(corpo, 'Selecao')   # formato: "0007:53,277|0019:192,194"

    log(f'[RelatorioParametrizacao] PID={pid} | Destino={destino} | Selecao={selecao}')

    if not selecao:
        log('Campo Selecao ausente no e-mail de relatório.', 'error')
        _marcar_lido(imap, num)
        return

    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')
    agora         = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    # Parseia seleção filtrando lojas ignoradas
    grupos = _parsear_selecao(selecao, props)

    ICO = {'OK': '✔',       'DIVERGENTE': '⚠',       'ERRO': '✖'}
    # Estilos repetidos por célula viram classes no <style> — o relatório cresce
    # com o número de PDVs e o Gmail corta a exibição acima de ~102 KB.
    CLS = {'OK': 'ok', 'DIVERGENTE': 'dv', 'ERRO': 'er'}

    total_ok = total_div = total_erro = 0
    secoes_html = ''
    secoes_txt  = ''

    for loja, pdvs in grupos.items():
        secoes_html += f"""
        <tr><td colspan='2' class='lj'><span class='ljn'>Loja {loja}</span></td></tr>"""
        secoes_txt += f'\n\n=== Loja {loja} ==='

        for pdv in pdvs:
            base_pdv = props.get(f'PDV_{pdv}', '')
            if not base_pdv:
                log(f'Aviso: IP não configurado para PDV {pdv}', 'warning')
            else:
                try:
                    autenticar_unc(base_pdv, windows_user, windows_senha)
                except PermissionError as e:
                    log(str(e), 'error')

            versao_pdv = ler_versao_pdv(base_pdv, props)
            resultados = _verificar_pdv_params(base_pdv, props)
            resultados.append(_verificar_impressora_pdv(base_pdv, loja, pdv, props))
            n_ok  = sum(1 for r in resultados if r['status'] == 'OK')
            n_div = sum(1 for r in resultados if r['status'] == 'DIVERGENTE')
            n_err = sum(1 for r in resultados if r['status'] == 'ERRO')
            total_ok += n_ok; total_div += n_div; total_erro += n_err

            # Badges de resumo do PDV
            resumo_badges = (
                f"<span class='b ok mr'>{n_ok} OK</span>"
                f"<span class='b dv mr'>{n_div} DIV</span>"
                f"<span class='b er'>{n_err} ERR</span>"
            )

            linhas_params = ''
            for r in resultados:
                st = r['status']
                descricao = r['erro'] if r['erro'] else ''
                linhas_params += f"""
                <tr>
                  <td class='pm'>{r['param']}</td>
                  <td class='st'>
                    <span class='b {CLS[st]}'>{ICO[st]} {st}</span>
                    {'<br><span class="ed">' + descricao[:50] + '…</span>' if descricao else ''}
                  </td>
                </tr>"""

            secoes_html += f"""
        <tr><td colspan='2' style='padding:10px 28px'>
          <table width='100%' cellpadding='0' cellspacing='0' class='t'>
            <thead>
              <tr style='background:#f8fafc'>
                <th class='th'>
                  PDV {pdv}
                  <span class='sub'>({base_pdv or 'IP não configurado'})</span>
                  <span class='sub'>— Versão {versao_pdv}</span>
                </th>
                <th class='thr'>{resumo_badges}</th>
              </tr>
            </thead>
            <tbody>{linhas_params}
            </tbody>
          </table>
        </td></tr>"""

            secoes_txt += f'\n  PDV {pdv} ({base_pdv}) - Versão {versao_pdv}: {n_ok} OK | {n_div} DIVERGENTE | {n_err} ERRO'
            for r in resultados:
                secoes_txt += f'\n    [{r["status"]}] {r["param"]}' + (f' — {r["erro"]}' if r["erro"] else '')

    if destino:
        corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<style>
.pm{{padding:7px 14px;border-bottom:1px solid #f0f0f0;font-family:monospace;font-size:11px;color:#374151}}
.st{{padding:7px 14px;border-bottom:1px solid #f0f0f0;text-align:right;white-space:nowrap}}
.b{{font-size:10px;font-weight:bold;padding:2px 8px;border-radius:10px}}
.mr{{margin-right:4px}}
.ok{{background:#dcfce7;color:#1a7f4b}}
.dv{{background:#fef3c7;color:#b45309}}
.er{{background:#fee2e2;color:#b91c1c}}
.ed{{font-size:10px;color:#b91c1c}}
.t{{border:1px solid #e5e7eb;border-radius:6px;overflow:hidden}}
.th{{padding:8px 14px;text-align:left;font-size:12px;color:#1e3a5f;font-weight:bold}}
.thr{{padding:8px 14px;text-align:right}}
.sub{{font-size:11px;font-weight:normal;color:#6b7280;margin-left:4px}}
.lj{{padding:14px 28px 6px;background:#f8fafc;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb}}
.ljn{{font-size:12px;font-weight:bold;color:#1e3a5f;text-transform:uppercase;letter-spacing:.5px}}
</style></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='640' cellpadding='0' cellspacing='0'
       style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Relatório Parametrização</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:38%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{pid}</p>
        </td>
        <td width='12'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:59%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Gerado em</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f'>{agora}</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:16px 28px'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='background:#dcfce7;border-radius:6px;padding:10px;text-align:center;width:32%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#1a7f4b'>{total_ok}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#1a7f4b;font-weight:bold'>OK</p>
        </td>
        <td width='10'></td>
        <td style='background:#fef3c7;border-radius:6px;padding:10px;text-align:center;width:32%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b45309'>{total_div}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b45309;font-weight:bold'>DIVERGENTE</p>
        </td>
        <td width='10'></td>
        <td style='background:#fee2e2;border-radius:6px;padding:10px;text-align:center;width:32%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b91c1c'>{total_erro}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b91c1c;font-weight:bold'>ERRO</p>
        </td>
      </tr>
    </table>
  </td></tr>

  {secoes_html}

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora} &nbsp;|&nbsp; Agent Extrator Log</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

        corpo_txt = (
            f'Relatório Parametrização\nPID: {pid}\nGerado em: {agora}\n'
            f'Total: {total_ok} OK | {total_div} DIVERGENTE | {total_erro} ERRO'
            + secoes_txt
        )

        try:
            enviar_email_html(email_user, email_pass, destino,
                              f'[Relatório Parametrização][{pid}]',
                              corpo_html, corpo_txt)
            log(f'Relatório enviado para {destino}')
        except Exception as e:
            log(f'Erro ao enviar relatório: {e}', 'error')

    _marcar_lido(imap, num)

# ---------------------------------------------------------------------------
# Funcionalidade 5: PinPad
# ---------------------------------------------------------------------------
def processar_pinpad(imap, num, corpo, props, email_user, email_pass):
    pid     = extrair_campo(corpo, 'PID')
    comando = extrair_campo(corpo, 'Comando')
    porta   = extrair_campo(corpo, 'Porta') or props.get('pinpad_porta', 'COM10')

    log(f'[PinPad] PID={pid} | Comando={comando} | Porta={porta}')

    comandos_validos = {'senha', 'enter', 'limpa', 'cartao'}
    if comando not in comandos_validos:
        log(f'[PinPad] Comando inválido: "{comando}"', 'error')
        _marcar_lido(imap, num)
        return

    ps_script = (
        f"$port=New-Object System.IO.Ports.SerialPort '{porta}',115200,None,8,one;"
        f"try{{$port.Open();Start-Sleep -m 500;$port.WriteLine('{comando}');$port.Close();"
        f"echo 'Comando [{comando}] enviado para {porta}'}}catch{{echo \"ERRO: $_\"}}"
    )

    # Localiza powershell.exe considerando redirecionamento WoW64
    import subprocess as _sp
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    ps_exe  = 'powershell.exe'
    for sub in (r'SysNative\WindowsPowerShell\v1.0', r'System32\WindowsPowerShell\v1.0'):
        candidato = os.path.join(sysroot, sub, 'powershell.exe')
        if os.path.exists(candidato):
            ps_exe = candidato
            break

    try:
        resultado = _sp.run(
            [ps_exe, '-NoProfile', '-NonInteractive', '-Command', ps_script],
            capture_output=True, text=True, timeout=10
        )
        saida = (resultado.stdout or resultado.stderr or '').strip()
        if 'ERRO' in saida.upper() or resultado.returncode != 0:
            log(f'[PinPad] ERRO ao executar [{comando}]: {saida}', 'error')
        else:
            log(f'[PinPad] [{comando}] executado com sucesso: {saida}')
    except Exception as e:
        log(f'[PinPad] Exceção ao executar [{comando}]: {e}', 'error')

    _marcar_lido(imap, num)


# ---------------------------------------------------------------------------
# Funcionalidade 6: Status PDV
# ---------------------------------------------------------------------------
def verificar_ping(ip):
    """Retorna True se o host responder ao ping, False caso contrário."""
    if not ip:
        return False
    try:
        import subprocess
        resultado = subprocess.run(
            ['ping', '-n', '2', '-w', '1000', ip],
            capture_output=True, text=True, timeout=10
        )
        return resultado.returncode == 0
    except Exception as e:
        log(f'Erro ao pingar {ip}: {e}', 'warning')
        return False

def verificar_servicos(props):
    """Verifica conectividade TCP dos serviços configurados como servico.<Nome>=host:porta.
    Retorna lista de dicts {nome, endereco, ok} na ordem em que aparecem no arquivo."""
    import socket
    servicos = []
    for chave, valor in props.items():
        if not chave.startswith('servico.'):
            continue
        nome = chave[len('servico.'):]
        endereco = valor.strip()
        ok = False
        try:
            host, porta = endereco.rsplit(':', 1)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            ok = s.connect_ex((host.strip(), int(porta))) == 0
            s.close()
        except Exception as e:
            log(f'Erro ao verificar serviço {nome} ({endereco}): {e}', 'warning')
        servicos.append({'nome': nome, 'endereco': endereco, 'ok': ok})
    return servicos

def _lojas_ignoradas(props):
    return [l.strip() for l in props.get('ignorar_lojas', '').split(',') if l.strip()]

def _parsear_selecao(selecao, props):
    """Parseia 'loja:pdv,...|loja:pdv,...' filtrando lojas ignoradas. Retorna dict {loja: [pdvs]}."""
    ignorar = _lojas_ignoradas(props)
    grupos = {}
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs_str = parte.split(':', 1)
            loja = loja.strip()
            if loja in ignorar:
                log(f'Loja {loja} ignorada conforme configuração.', 'warning')
                continue
            grupos[loja] = [p.strip() for p in pdvs_str.split(',') if p.strip()]
    return grupos

def _versao_key(v):
    """Converte string de versão em tupla comparável. Ex: '7.0.117.61.r4' → (7,0,117,61,4)."""
    import re
    partes = []
    for seg in v.split('.'):
        nums = re.sub(r'[^\d]', '', seg)
        partes.append(int(nums) if nums else 0)
    return tuple(partes)

def processar_status_pdv(imap, num, corpo, props, email_user, email_pass):
    pid     = extrair_campo(corpo, 'PID')
    destino = extrair_campo(corpo, 'Destino')
    selecao = extrair_campo(corpo, 'Selecao')

    log(f'[StatusPDV] PID={pid} | Destino={destino} | Selecao={selecao}')

    if not selecao:
        log('Campo Selecao ausente no e-mail de status.', 'error')
        _marcar_lido(imap, num)
        return

    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')
    agora         = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    grupos = _parsear_selecao(selecao, props)

    # --- 1ª passagem: coleta versões de todos os PDVs ---
    registros = []   # lista de dicts: {loja, pdv, base_pdv, versao}
    for loja, pdvs in grupos.items():
        for pdv in pdvs:
            base_pdv = props.get(f'PDV_{pdv}', '')
            if not base_pdv:
                log(f'Aviso: IP não configurado para PDV {pdv}', 'warning')
            else:
                try:
                    autenticar_unc(base_pdv, windows_user, windows_senha)
                except PermissionError as e:
                    log(str(e), 'error')
            ping_ok = verificar_ping(base_pdv)
            if ping_ok:
                versao = ler_versao_pdv(base_pdv, props)
                ligado = verificar_processo_pdv(base_pdv, props)
                ativo, status_server = ler_status_online(base_pdv, props)
            else:
                versao = 'N/D'
                ligado = '— N/D'
                ativo = '— N/D'
                status_server = '— N/D'
            # Online consolidado: exige indicaOnLine e StatusServer verdadeiros na
            # última ocorrência do CSIDebugFile e o PDV ligado; senão false.
            online = 'true' if (ligado == 'true' and ativo == 'true'
                                and status_server == 'true') else 'false'
            registros.append({
                'loja': loja, 'pdv': pdv, 'base_pdv': base_pdv,
                'versao': versao, 'ligado': ligado, 'online': online,
                'ping': 'true' if ping_ok else 'false'
            })

    servicos = verificar_servicos(props)

    # Determina se há múltiplas versões válidas e qual é a mais nova
    versoes_validas = {r['versao'] for r in registros if r['versao'] != 'N/D'}
    multiplas = len(versoes_validas) > 1
    versao_mais_nova = max(versoes_validas, key=_versao_key) if versoes_validas else None

    def badge_versao(versao):
        if versao == 'N/D':
            return ('#b91c1c', '#fee2e2', '✖')
        if not multiplas or versao == versao_mais_nova:
            return ('#1a7f4b', '#dcfce7', '✔')
        return ('#1e40af', '#dbeafe', '↓')

    def badge_bool(valor):
        """Retorna (cor_txt, cor_bg, icone) para campos true/false/N/D."""
        if valor == 'true':
            return ('#1a7f4b', '#dcfce7', '✔')
        if valor == 'false':
            return ('#b91c1c', '#fee2e2', '✖')
        return ('#6b7280', '#f3f4f6', '—')

    # --- 2ª passagem: monta HTML agrupado por loja ---
    secoes_html = ''
    secoes_txt  = ''
    total_ok = total_erro = 0
    loja_atual = None

    for r in registros:
        if r['versao'] != 'N/D': total_ok   += 1
        else:                     total_erro += 1

        if r['loja'] != loja_atual:
            loja_atual = r['loja']
            secoes_html += f"""
        <tr><td colspan='6' style='padding:10px 28px 5px;background:#f8fafc;
            border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb'>
          <span style='font-size:14px;font-weight:bold;color:#1e3a5f;text-transform:uppercase;
                       letter-spacing:.5px'>Loja {loja_atual}</span>
        </td></tr>"""
            secoes_txt += f'\n\n=== Loja {loja_atual} ==='

        cor_v, bg_v, ico_v = badge_versao(r['versao'])
        cor_p, bg_p, ico_p = badge_bool(r['ping'])
        cor_l, bg_l, ico_l = badge_bool(r['ligado'])
        cor_o, bg_o, ico_o = badge_bool(r['online'])
        bg_row = "background:#fff5f5;" if r['versao'] == 'N/D' else ''

        def mini_badge(bg, cor, ico, texto):
            return (f"<span style='background:{bg};color:{cor};font-size:11px;"
                    f"padding:3px 8px;border-radius:10px;white-space:nowrap'>{ico} {texto}</span>")

        td = "padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:center"
        secoes_html += f"""
        <tr style='{bg_row}'>
          <td style='padding:8px 28px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#374151'>
            PDV {r['pdv']}
          </td>
          <td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#6b7280'>
            {r['base_pdv'] or '—'}
          </td>
          <td style='{td}'>{mini_badge(bg_p, cor_p, ico_p, r['ping'])}</td>
          <td style='{td}'>{mini_badge(bg_l, cor_l, ico_l, r['ligado'])}</td>
          <td style='{td}'>{mini_badge(bg_o, cor_o, ico_o, r['online'])}</td>
          <td style='{td}'>
            <span style='background:{bg_v};color:{cor_v};font-size:11px;
                         padding:3px 8px;border-radius:10px;white-space:nowrap'>{ico_v} {r['versao']}</span>
          </td>
        </tr>"""
        secoes_txt += (f"\n  PDV {r['pdv']} ({r['base_pdv']}): "
                       f"Ping={r['ping']} Ligado={r['ligado']} Online={r['online']} "
                       f"Versao={r['versao']}")

    # --- Cards de status dos serviços monitorados (SiTef, Proctrans, ...) ---
    servicos_cells = ''
    servicos_txt = ''
    for i, s in enumerate(servicos):
        if s['ok']:
            bg, cor, ico, rotulo = '#dcfce7', '#1a7f4b', '✔', 'ONLINE'
        else:
            bg, cor, ico, rotulo = '#fee2e2', '#b91c1c', '✖', 'OFFLINE'
        if i > 0:
            servicos_cells += "<td width='12'></td>"
        servicos_cells += f"""
        <td style='background:{bg};border-radius:6px;padding:10px;text-align:center'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>{s['nome']}</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:{cor}'>{ico} {rotulo}</p>
          <p style='margin:2px 0 0;font-size:10px;color:#9ca3af;font-family:monospace'>{s['endereco']}</p>
        </td>"""
        servicos_txt += f"\n  {s['nome']} ({s['endereco']}): {'Online' if s['ok'] else 'Offline'}"

    servicos_html = ''
    if servicos_cells:
        servicos_html = f"""
  <tr><td style='padding:16px 28px 0'>
    <p style='margin:0 0 8px;font-size:11px;color:#6b7280;text-transform:uppercase;
              letter-spacing:.5px;font-weight:bold'>Serviços</p>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>{servicos_cells}
      </tr>
    </table>
  </td></tr>"""

    if destino:
        corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='680' cellpadding='0' cellspacing='0'
       style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Status dos PDVs</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:38%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{pid}</p>
        </td>
        <td width='12'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:59%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Gerado em</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f'>{agora}</p>
        </td>
      </tr>
    </table>
  </td></tr>
{servicos_html}
  <tr><td style='padding:16px 28px'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='background:#dcfce7;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#1a7f4b'>{total_ok}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#1a7f4b;font-weight:bold'>LIDOS</p>
        </td>
        <td width='12'></td>
        <td style='background:#fee2e2;border-radius:6px;padding:10px;text-align:center;width:48%'>
          <p style='margin:0;font-size:22px;font-weight:bold;color:#b91c1c'>{total_erro}</p>
          <p style='margin:2px 0 0;font-size:11px;color:#b91c1c;font-weight:bold'>NÃO LIDOS</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:0 28px 24px'>
    <table width='100%' cellpadding='0' cellspacing='0'
           style='border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;table-layout:fixed'>
      <colgroup>
        <col style='width:15%'><col style='width:16%'><col style='width:13%'>
        <col style='width:14%'><col style='width:14%'><col style='width:28%'>
      </colgroup>
      <thead>
        <tr style='background:#f8fafc'>
          <th style='padding:9px 28px;text-align:left;color:#6b7280;font-size:10px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;font-weight:500'>PDV</th>
          <th style='padding:9px 10px;text-align:left;color:#6b7280;font-size:10px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;font-weight:500'>IP</th>
          <th style='padding:9px 10px;text-align:center;color:#6b7280;font-size:10px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;font-weight:500'>Ping</th>
          <th style='padding:9px 10px;text-align:center;color:#6b7280;font-size:10px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;font-weight:500'>Ligado</th>
          <th style='padding:9px 10px;text-align:center;color:#6b7280;font-size:10px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;font-weight:500'>Online</th>
          <th style='padding:9px 10px;text-align:center;color:#6b7280;font-size:10px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;font-weight:500'>Versão</th>
        </tr>
      </thead>
      <tbody>{secoes_html}
      </tbody>
    </table>
  </td></tr>

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora} &nbsp;|&nbsp; Agent Extrator Log</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

        corpo_txt = (
            f'Status dos PDVs\nPID: {pid}\nGerado em: {agora}\n'
            + (f'Serviços:{servicos_txt}\n' if servicos_txt else '')
            + f'Lidos: {total_ok} | Não lidos: {total_erro}'
            + secoes_txt
        )

        try:
            enviar_email_html(email_user, email_pass, destino,
                              f'[Status PDV][{pid}]',
                              corpo_html, corpo_txt)
            log(f'Relatório de status enviado para {destino}')
        except Exception as e:
            log(f'Erro ao enviar relatório de status: {e}', 'error')

    _marcar_lido(imap, num)

# ---------------------------------------------------------------------------
# Funcionalidade 7: Fechar / Reiniciar PDV
# ---------------------------------------------------------------------------
def _encontrar_sys32_exe(nome):
    """Localiza um executável em System32 considerando redirecionamento WoW64."""
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    for sub in ('SysNative', 'System32', 'SysWOW64'):
        caminho = os.path.join(sysroot, sub, nome)
        if os.path.exists(caminho):
            return caminho
    return nome

def _fechar_javaw(base_pdv, props):
    """Encerra java.exe no PDV. Retorna (sucesso, mensagem)."""
    import subprocess
    taskkill = _encontrar_sys32_exe('taskkill.exe')
    try:
        if base_pdv in _ips_locais():
            cmd = [taskkill, '/F', '/IM', 'java.exe']
        else:
            windows_user  = props.get('windows_user', '')
            windows_senha = props.get('windows_senha', '')
            cmd = [taskkill, '/S', base_pdv, '/F', '/IM', 'java.exe']
            if windows_user:
                cmd += ['/U', windows_user, '/P', windows_senha]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        saida = (r.stdout + r.stderr).strip()
        return r.returncode == 0, saida
    except Exception as e:
        return False, str(e)

def _iniciar_pdv_bat(base_pdv, props):
    """Inicia \p2k\Bin\pdv.bat no PDV via schtasks apontando direto para o bat.
    Sem criação de arquivos auxiliares — o pdv.bat já existe na máquina."""
    import subprocess
    from datetime import datetime, timedelta

    schtasks      = _encontrar_sys32_exe('schtasks.exe')
    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')
    eh_local      = base_pdv in _ips_locais()
    conn_args     = [] if eh_local else ['/S', base_pdv, '/U', windows_user, '/P', windows_senha]

    task_nome  = 'AgtStartPDV'
    cmd_tarefa = r'\p2k\Bin\pdv.bat DIRETO'
    st = (datetime.now() + timedelta(minutes=2)).strftime('%H:%M')

    log(f'[IniciarPDV] schtasks={schtasks} local={eh_local} cmd={cmd_tarefa}')
    try:
        # 1. Criar tarefa apontando diretamente para pdv.bat
        r = subprocess.run(
            [schtasks, '/create'] + conn_args +
            ['/TN', task_nome, '/TR', cmd_tarefa,
             '/SC', 'ONCE', '/ST', st,
             '/RU', windows_user, '/RP', windows_senha,
             '/F'],
            capture_output=True, text=True, timeout=20
        )
        log(f'[IniciarPDV] create rc={r.returncode} | {(r.stdout + r.stderr).strip()}')
        if r.returncode != 0:
            return False, f'schtasks /create: {(r.stdout + r.stderr).strip()}'

        # 2. Executar imediatamente
        r = subprocess.run(
            [schtasks, '/run'] + conn_args + ['/TN', task_nome],
            capture_output=True, text=True, timeout=20
        )
        sucesso = r.returncode == 0
        msg = (r.stdout + r.stderr).strip()
        log(f'[IniciarPDV] run rc={r.returncode} | {msg}')

        # 3. Monitorar status por 60s em modo debug
        if _debug_mode:
            logd(f'Monitorando task "{task_nome}" por 60s...')
            for tentativa in range(1, 13):
                time.sleep(5)
                rq = subprocess.run(
                    [schtasks, '/query', '/TN', task_nome, '/FO', 'LIST', '/V'] + conn_args,
                    capture_output=True, text=True, timeout=20
                )
                logd(f'Query #{tentativa} rc={rq.returncode}:\n{(rq.stdout + rq.stderr).strip()}')

        # 4. Remover tarefa
        subprocess.run(
            [schtasks, '/delete'] + conn_args + ['/TN', task_nome, '/F'],
            capture_output=True, text=True, timeout=20
        )
        return sucesso, msg
    except Exception as e:
        return False, str(e)

def processar_fechar_pdv(imap, num, corpo, props):
    pid     = extrair_campo(corpo, 'PID')
    selecao = extrair_campo(corpo, 'Selecao')
    log(f'[FecharPDV] PID={pid} | Selecao={selecao}')

    grupos = _parsear_selecao(selecao, props)
    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')

    for loja, pdvs in grupos.items():
        for pdv in pdvs:
            base_pdv = props.get(f'PDV_{pdv}', '')
            if not base_pdv:
                log(f'[FecharPDV] IP não configurado para PDV {pdv}', 'warning')
                continue
            if base_pdv not in _ips_locais():
                try:
                    autenticar_unc(base_pdv, windows_user, windows_senha)
                except PermissionError as e:
                    log(str(e), 'error')
            sucesso, msg = _fechar_javaw(base_pdv, props)
            status = 'OK' if sucesso else 'ERRO'
            log(f'[FecharPDV] Loja {loja} PDV {pdv} ({base_pdv}): {status} — {msg}')

    _marcar_lido(imap, num)

def _reiniciar_maquina(base_pdv, props):
    """Reinicia a máquina do PDV via shutdown.exe. Retorna (sucesso, mensagem).
    O pdv.bat sobe sozinho no logon (rotina já configurada na máquina),
    evitando os problemas de sessão/foreground do reinício via processo."""
    import subprocess
    shutdown_exe = _encontrar_sys32_exe('shutdown.exe')
    try:
        if base_pdv in _ips_locais():
            cmd = [shutdown_exe, '/r', '/t', '0', '/f']
        else:
            # shutdown.exe usa a sessão admin já autenticada via UNC (autenticar_unc),
            # não aceita credenciais diretamente como parâmetro.
            cmd = [shutdown_exe, '/r', '/m', f'\\\\{base_pdv}', '/t', '0', '/f']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        saida = (r.stdout + r.stderr).strip()
        return r.returncode == 0, saida
    except Exception as e:
        return False, str(e)

def processar_reiniciar_pdv(imap, num, corpo, props):
    pid     = extrair_campo(corpo, 'PID')
    selecao = extrair_campo(corpo, 'Selecao')
    log(f'[ReiniciarPDV] PID={pid} | Selecao={selecao}')

    grupos = _parsear_selecao(selecao, props)
    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')

    # Monta lista (loja, pdv, base_pdv) e adia a máquina local para o final —
    # reiniciá-la primeiro mataria o agente e interromperia os demais PDVs da seleção.
    itens = []
    for loja, pdvs in grupos.items():
        for pdv in pdvs:
            base_pdv = props.get(f'PDV_{pdv}', '')
            if not base_pdv:
                log(f'[ReiniciarPDV] IP não configurado para PDV {pdv}', 'warning')
                continue
            itens.append((loja, pdv, base_pdv))

    itens.sort(key=lambda item: item[2] in _ips_locais())

    for loja, pdv, base_pdv in itens:
        if base_pdv not in _ips_locais():
            try:
                autenticar_unc(base_pdv, windows_user, windows_senha)
            except PermissionError as e:
                log(str(e), 'error')
        sucesso, msg = _reiniciar_maquina(base_pdv, props)
        log(f'[ReiniciarPDV] Reiniciar máquina Loja {loja} PDV {pdv} ({base_pdv}): '
            f'{"OK" if sucesso else "ERRO"} — {msg}')

    _marcar_lido(imap, num)

# ---------------------------------------------------------------------------
# Funcionalidade 9: Registro de execução do BEC
# ---------------------------------------------------------------------------
# Funcionalidades que o BEC executa sozinho (Exportar Oracle, Requisição API,
# MDM, PinPad em modo direto) não passam por nenhum agente. Para que apareçam na
# mesma trilha das demais, o BEC avisa por um e-mail [Registro Execucao], que o
# agente apenas registra e marca como lido.
def processar_registro_execucao(imap, num, corpo):
    pid            = extrair_campo(corpo, 'PID')
    usuario        = extrair_campo(corpo, 'Usuario')
    funcionalidade = extrair_campo(corpo, 'Funcionalidade')
    data_hora      = extrair_campo(corpo, 'DataHora')

    log(f'[RegistroExecucao] Funcionalidade={funcionalidade} | Usuario={usuario} | '
        f'DataHora={data_hora}')

    if registrar_acao_usuario(funcionalidade, pid, usuario, data_hora):
        log(f'[RegistroExecucao] Ação registrada na trilha de usuários.')
    else:
        log(f'[RegistroExecucao] PID já constava na trilha — linha não duplicada.')

    _marcar_lido(imap, num)

# ---------------------------------------------------------------------------
# Loop principal de leitura de e-mails
# ---------------------------------------------------------------------------
# Nome da funcionalidade por assunto, para a trilha de ações por usuário.
# [Registro Execucao] fica de fora: o nome vem do corpo do próprio e-mail.
FUNCIONALIDADES_POR_ASSUNTO = (
    ('[Solicitação Log]',        'Solicitar Logs'),
    ('[Parametrização PDV]',     'Parametrização PDV'),
    ('[Verificar Parametrização]', 'Verificar Parametrização'),
    ('[Relatório Parametrização]', 'Relatório Parametrização'),
    ('[Status PDV]',             'Status PDV'),
    ('[Fechar PDV]',             'Fechar PDV'),
    ('[Reiniciar PDV]',          'Reiniciar PDV'),
    ('[PinPad]',                 'PinPad'),
    ('[Atualizacao Agente]',     'Atualizar Agente'),
)

def _funcionalidade_do_assunto(assunto):
    for marcador, nome in FUNCIONALIDADES_POR_ASSUNTO:
        if marcador in assunto:
            return nome
    return None

# ---------------------------------------------------------------------------
# Funcionalidade 8: Atualização automática do agente
# ---------------------------------------------------------------------------
# O BEC envia o pacote de instalação anexado a um e-mail [Atualizacao Agente].
# O agente confere remetente, tamanho e SHA256, extrai o pacote e delega a troca
# dos arquivos a um script externo — o próprio .exe está em uso e não pode se
# sobrescrever. O script roda pelo Agendador de Tarefas (portanto fora da árvore
# de processos do serviço, que seria encerrada junto com ele), para o serviço,
# copia os arquivos, e reinicia. Se o serviço não voltar, desfaz pelo backup.
SERVICO_NOME      = 'AgentExtratarLog'
EXECUTAVEL_AGENTE = 'agent_extrator_log.exe'
ATUALIZACAO_DIR   = os.path.join(BASE_DIR, 'atualizacao')
PIDS_APLICADOS    = os.path.join(BASE_DIR, 'atualizacoes_aplicadas.txt')
TAREFA_ATUALIZACAO = 'BEC_Atualiza_AgentExtratorLog'

# O script de atualização grava o desfecho em resultado.txt e o agente, ao subir
# com a nova versão, encontra o arquivo e responde por e-mail. É esse caminho —
# e não o próprio script — que confirma qual build ficou de fato em execução.
ARQUIVO_RESULTADO = 'resultado.txt'
ARQUIVO_CONTEXTO  = 'contexto.txt'
FLAG_RESULTADO_ENVIADO = 'resultado_enviado.flag'

# Guardam a configuração da máquina: são mantidos como estão na atualização.
PRESERVAR_NA_ATUALIZACAO = ('agent.properties',)


def _sha256_arquivo(caminho):
    import hashlib
    h = hashlib.sha256()
    with open(caminho, 'rb') as f:
        for bloco in iter(lambda: f.read(65536), b''):
            h.update(bloco)
    return h.hexdigest()


def _remetente_autorizado(msg, props, email_user):
    """Só aceita pacote de remetentes conhecidos.

    Padrão: a própria conta monitorada (o BEC envia de e para ela). Endereços
    extras podem ser listados em atualizacao.remetentes no agent.properties.
    """
    from email.utils import parseaddr
    remetente = parseaddr(msg.get('From', ''))[1].strip().lower()
    autorizados = {(email_user or '').strip().lower()}
    autorizados.update(
        e.strip().lower()
        for e in props.get('atualizacao.remetentes', '').split(',') if e.strip()
    )
    autorizados.discard('')
    return remetente in autorizados, remetente


def _extrair_anexo_zip(msg, destino):
    """Grava o primeiro anexo .zip da mensagem em destino. Retorna o caminho ou None."""
    for parte in msg.walk():
        if parte.get_content_maintype() == 'multipart':
            continue
        nome = parte.get_filename()
        if not nome:
            continue
        nome = decodifica_assunto(nome) if '=?' in nome else nome
        if not nome.lower().endswith('.zip'):
            continue
        caminho = os.path.join(destino, os.path.basename(nome))
        with open(caminho, 'wb') as f:
            f.write(parte.get_payload(decode=True))
        return caminho
    return None


def _restaurar_sufixo(pasta, sufixo):
    """Desfaz a neutralização feita pelo BEC (ex.: nssm.exe.becpkg -> nssm.exe)."""
    if not sufixo:
        return 0
    restaurados = 0
    for raiz, _dirs, arquivos in os.walk(pasta):
        for arquivo in arquivos:
            if arquivo.endswith(sufixo):
                origem = os.path.join(raiz, arquivo)
                final  = os.path.join(raiz, arquivo[:-len(sufixo)])
                os.replace(origem, final)
                restaurados += 1
    return restaurados


def _pid_ja_aplicado(pid):
    """Evita reinstalar o mesmo pacote caso o e-mail volte a ser lido."""
    if not os.path.exists(PIDS_APLICADOS):
        return False
    with open(PIDS_APLICADOS, 'r', encoding='utf-8', errors='replace') as f:
        return any(linha.strip().endswith(pid) for linha in f)


def _registrar_pid_aplicado(pid, pacote):
    with open(PIDS_APLICADOS, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S};{pacote};{pid}\n")


def _gravar_contexto(pasta, dados):
    """Guarda os dados da solicitação para o agente responder após o reinício."""
    with open(os.path.join(pasta, ARQUIVO_CONTEXTO), 'w', encoding='utf-8') as f:
        for chave, valor in dados.items():
            f.write(f'{chave}={valor}\n')


def _ler_chave_valor(caminho):
    dados = {}
    if not os.path.exists(caminho):
        return dados
    with open(caminho, 'r', encoding='utf-8', errors='replace') as f:
        for linha in f:
            linha = linha.strip()
            if '=' in linha:
                chave, valor = linha.split('=', 1)
                dados[chave.strip()] = valor.strip()
    return dados


def enviar_resultados_atualizacao(props, email_user, email_pass):
    """Responde por e-mail o desfecho das atualizações concluídas.

    Roda na subida do agente: o script de atualização já terminou e deixou o
    resultado gravado. Como quem envia é o processo recém-iniciado, a versão
    informada é comprovadamente a que está em execução.
    """
    if not os.path.isdir(ATUALIZACAO_DIR):
        return

    for nome in sorted(os.listdir(ATUALIZACAO_DIR)):
        pasta = os.path.join(ATUALIZACAO_DIR, nome)
        arquivo_resultado = os.path.join(pasta, ARQUIVO_RESULTADO)
        flag = os.path.join(pasta, FLAG_RESULTADO_ENVIADO)
        if not os.path.isfile(arquivo_resultado) or os.path.exists(flag):
            continue

        try:
            resultado = _ler_chave_valor(arquivo_resultado)
            contexto  = _ler_chave_valor(os.path.join(pasta, ARQUIVO_CONTEXTO))

            destino = contexto.get('Destino', '').strip()
            if not destino:
                log(f'[Atualizacao] Resultado de {nome} sem destino; e-mail não enviado.', 'warning')
                open(flag, 'w').close()
                continue

            status = resultado.get('STATUS', 'DESCONHECIDO')
            pid    = contexto.get('PID', nome)

            corpo = (
                f"PID: {pid}\n"
                f"Agente: Agent Extrator Log\n"
                f"Maquina: {os.environ.get('COMPUTERNAME', '')}\n"
                f"Status: {status}\n"
                f"VersaoInstalada: {AGENTE_VERSAO}\n"
                f"VersaoAnterior: {contexto.get('VersaoAnterior', 'desconhecida')}\n"
                f"Pacote: {contexto.get('Pacote', '')}\n"
                f"Detalhe: {resultado.get('DETALHE', '')}\n"
                f"ConcluidoEm: {resultado.get('DATAHORA', '')}\n"
                f"SolicitadoPor: {contexto.get('Usuario', '')}\n"
            )

            assunto = f'[Resultado Atualizacao Agente] - [{status}] - [{pid}]'
            log_atualizacao = os.path.join(pasta, 'atualizacao.log')
            anexo = log_atualizacao if os.path.exists(log_atualizacao) else ''

            if anexo:
                enviar_email_com_anexo(email_user, email_pass, destino, assunto, corpo, anexo)
            else:
                enviar_email_texto(email_user, email_pass, destino, assunto, corpo)

            open(flag, 'w').close()
            log(f'[Atualizacao] Resultado {status} (PID={pid}) enviado para {destino}. '
                f'Versão em execução: {AGENTE_VERSAO}')
        except Exception as e:
            log(f'[Atualizacao] Falha ao enviar resultado de {nome}: {e}', 'error')


def _escrever_script_atualizacao(pasta, origem, pid):
    """Gera o .bat que troca os arquivos com o serviço parado."""
    backup    = os.path.join(pasta, 'backup')
    log_bat   = os.path.join(pasta, 'atualizacao.log')
    excluir   = os.path.join(pasta, 'nao_copiar.txt')
    script    = os.path.join(pasta, 'aplicar_atualizacao.bat')
    resultado = os.path.join(pasta, ARQUIVO_RESULTADO)

    with open(excluir, 'w', encoding='ascii') as f:
        for nome in PRESERVAR_NA_ATUALIZACAO:
            f.write(nome + '\n')

    conteudo = f"""@echo off
setlocal enabledelayedexpansion
set SERVICE={SERVICO_NOME}
set INSTALL={BASE_DIR}
set ORIGEM={origem}
set BACKUP={backup}
set LOG={log_bat}
set EXCLUIR={excluir}
set RESULTADO={resultado}
set ERRO=0
set STATUS=FALHA
set DETALHE=Atualizacao interrompida antes de concluir.

call :L "=== Atualizacao PID={pid} iniciada ==="

:: ---- Para o servico ----
call :L "Parando servico %SERVICE%..."
sc stop %SERVICE% >nul 2>&1
set /a T=0
:aguarda_parada
timeout /t 2 /nobreak >nul
sc query %SERVICE% | findstr /i "STOPPED" >nul
if !errorlevel! neq 0 (
    set /a T+=1
    if !T! lss 15 goto :aguarda_parada
    call :L "[AVISO] Servico nao parou em 30s. Forcando encerramento."
    taskkill /f /im {EXECUTAVEL_AGENTE} >nul 2>&1
    timeout /t 3 /nobreak >nul
)
call :L "[OK] Servico parado."

:: ---- Backup do que sera substituido ----
if not exist "%BACKUP%" mkdir "%BACKUP%"
xcopy "%INSTALL%\\*.exe"  "%BACKUP%\\" /Y /Q >nul 2>&1
xcopy "%INSTALL%\\*.bat"  "%BACKUP%\\" /Y /Q >nul 2>&1
call :L "[OK] Backup gravado em %BACKUP%"

:: ---- Copia a nova versao (agent.properties e preservado) ----
xcopy "%ORIGEM%\\*" "%INSTALL%\\" /E /Y /Q /EXCLUDE:%EXCLUIR% >> "%LOG%" 2>&1
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao copiar os arquivos da nova versao."
    set ERRO=1
    set DETALHE=Falha ao copiar os arquivos da nova versao.
    goto :restaurar
)
call :L "[OK] Arquivos da nova versao copiados."

:: ---- Sobe o servico ----
call :L "Iniciando servico..."
sc start %SERVICE% >nul 2>&1
set /a T=0
:aguarda_inicio
timeout /t 2 /nobreak >nul
sc query %SERVICE% | findstr /i "RUNNING" >nul
if !errorlevel! neq 0 (
    set /a T+=1
    if !T! lss 15 goto :aguarda_inicio
    call :L "[ERRO] Servico nao entrou em execucao apos a atualizacao."
    set ERRO=1
    set DETALHE=Servico nao entrou em execucao com a nova versao.
    goto :restaurar
)
call :L "[OK] Servico em execucao. Atualizacao concluida."
set STATUS=SUCESSO
set DETALHE=Nova versao instalada e servico em execucao.
goto :fim

:restaurar
call :L "Restaurando versao anterior a partir do backup..."
sc stop %SERVICE% >nul 2>&1
timeout /t 5 /nobreak >nul
taskkill /f /im {EXECUTAVEL_AGENTE} >nul 2>&1
xcopy "%BACKUP%\\*" "%INSTALL%\\" /E /Y /Q >> "%LOG%" 2>&1
sc start %SERVICE% >nul 2>&1
timeout /t 5 /nobreak >nul
sc query %SERVICE% | findstr /i "RUNNING" >nul
if !errorlevel! equ 0 (
    call :L "[OK] Versao anterior restaurada e servico em execucao."
    set STATUS=REVERTIDO
    set DETALHE=!DETALHE! Versao anterior restaurada e servico em execucao.
) else (
    call :L "[FALHA] Servico nao subiu nem apos a restauracao. Requer acao manual."
    set STATUS=FALHA_CRITICA
    set DETALHE=!DETALHE! Servico nao subiu nem apos a restauracao; requer acao manual.
)

:fim
call :L "=== Atualizacao PID={pid} finalizada (STATUS=!STATUS!) ==="
:: O agente le este arquivo ao subir e responde por e-mail com o resultado
> "%RESULTADO%" echo STATUS=!STATUS!
>> "%RESULTADO%" echo DETALHE=!DETALHE!
>> "%RESULTADO%" echo DATAHORA=%DATE% %TIME%
schtasks /delete /tn "{TAREFA_ATUALIZACAO}" /f >nul 2>&1
exit /b !ERRO!

:L
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
"""
    with open(script, 'w', encoding='ascii', errors='replace') as f:
        f.write(conteudo)
    return script


def _disparar_script(script):
    """Executa o script fora da árvore de processos do serviço.

    Pelo Agendador de Tarefas: se fosse filho do agente, o NSSM o encerraria
    junto ao parar o serviço — que é justamente o primeiro passo do script.
    """
    import subprocess as _sp
    criar = [
        'schtasks', '/create', '/tn', TAREFA_ATUALIZACAO,
        '/tr', f'"{script}"', '/sc', 'once', '/st', '00:00',
        '/ru', 'SYSTEM', '/rl', 'HIGHEST', '/f',
    ]
    resultado = _sp.run(criar, capture_output=True, text=True, timeout=30)
    if resultado.returncode == 0:
        _sp.run(['schtasks', '/run', '/tn', TAREFA_ATUALIZACAO],
                capture_output=True, text=True, timeout=30)
        log('[Atualizacao] Script agendado e disparado via Agendador de Tarefas.')
        return True

    log(f'[Atualizacao] schtasks falhou ({resultado.stderr.strip()}); '
        f'disparando processo desanexado.', 'warning')
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    _sp.Popen(['cmd', '/c', script],
              creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
              close_fds=True)
    return True


def processar_atualizacao(imap, num, msg, corpo, props, email_user, dados_zip=None):
    """Aplica o pacote de atualização recebido por e-mail ou pela fila do relay.

    Pelo relay, o pacote chega em `dados_zip` (bytes já decodificados) e `msg` é
    None. A autorização, nesse caminho, é o token do relay — só quem tem o token
    consegue publicar na fila do agente —, então a checagem de remetente vale
    apenas para o e-mail.
    """
    import shutil

    pid     = extrair_campo(corpo, 'PID')
    pacote  = extrair_campo(corpo, 'Pacote')
    sha     = extrair_campo(corpo, 'SHA256').lower()
    sufixo  = extrair_campo(corpo, 'SufixoNeutro')
    destino = extrair_campo(corpo, 'Destino')
    usuario = extrair_campo(corpo, 'Usuario')
    tamanho_informado = extrair_campo(corpo, 'TamanhoBytes')

    log(f'[Atualizacao] PID={pid} | Pacote={pacote} | Resultado para: {destino or "(não informado)"}')

    # O e-mail é marcado como lido antes de qualquer coisa: o serviço será
    # reiniciado no meio do processo e não pode reprocessar a mesma mensagem.
    _marcar_lido(imap, num)

    # Tudo dentro do try: uma falha aqui não pode derrubar o ciclo e impedir o
    # processamento das solicitações de log da mesma rodada.
    try:
        if dados_zip is None:
            autorizado, remetente = _remetente_autorizado(msg, props, email_user)
            if not autorizado:
                log(f'[Atualizacao] Remetente não autorizado: {remetente}. Pacote ignorado.', 'error')
                return False
        else:
            log('[Atualizacao] Pacote recebido pelo relay (autorizado pelo token).')

        if pid and _pid_ja_aplicado(pid):
            log(f'[Atualizacao] PID {pid} já aplicado anteriormente. Ignorando.', 'warning')
            return False

        pasta = os.path.join(ATUALIZACAO_DIR, pid or datetime.now().strftime('%Y%m%d%H%M%S'))
        shutil.rmtree(pasta, ignore_errors=True)
        os.makedirs(pasta, exist_ok=True)

        if dados_zip is not None:
            # Pelo relay o pacote vem no payload; grava com o nome informado para
            # que o resto do fluxo (conferência, extração) siga igual ao do e-mail.
            caminho_zip = os.path.join(pasta, os.path.basename(pacote or 'pacote.zip'))
            with open(caminho_zip, 'wb') as f:
                f.write(dados_zip)
            log(f'[Atualizacao] Pacote gravado a partir da fila: {caminho_zip} '
                f'({len(dados_zip)} bytes)')
        else:
            caminho_zip = _extrair_anexo_zip(msg, pasta)
        if not caminho_zip:
            log('[Atualizacao] E-mail sem anexo .zip. Nada a fazer.', 'error')
            return False

        tamanho = os.path.getsize(caminho_zip)
        if tamanho_informado and str(tamanho) != tamanho_informado.strip():
            log(f'[Atualizacao] Tamanho divergente: recebido {tamanho}, '
                f'informado {tamanho_informado}. Pacote descartado.', 'error')
            return False

        if sha:
            calculado = _sha256_arquivo(caminho_zip)
            if calculado != sha:
                log(f'[Atualizacao] SHA256 divergente. Esperado {sha}, '
                    f'calculado {calculado}. Pacote descartado.', 'error')
                return False
            log('[Atualizacao] SHA256 conferido.')
        else:
            log('[Atualizacao] E-mail sem SHA256; seguindo sem verificação de integridade.', 'warning')

        extraido = os.path.join(pasta, 'extraido')
        os.makedirs(extraido, exist_ok=True)
        with zipfile.ZipFile(caminho_zip) as z:
            z.extractall(extraido)

        restaurados = _restaurar_sufixo(extraido, sufixo)
        if restaurados:
            log(f'[Atualizacao] {restaurados} arquivo(s) renomeado(s) de volta (sufixo {sufixo}).')

        if not os.path.exists(os.path.join(extraido, EXECUTAVEL_AGENTE)):
            log(f'[Atualizacao] {EXECUTAVEL_AGENTE} não encontrado no pacote. '
                f'Atualização abortada.', 'error')
            return False

        # Gravado antes de disparar: depois da troca o processo atual não existe
        # mais, e é por este arquivo que a nova versão sabe para quem responder.
        _gravar_contexto(pasta, {
            'PID': pid,
            'Destino': destino,
            'Pacote': pacote,
            'Usuario': usuario,
            'VersaoAnterior': AGENTE_VERSAO,
        })

        script = _escrever_script_atualizacao(pasta, extraido, pid)
        if pid:
            _registrar_pid_aplicado(pid, pacote)

        log('[Atualizacao] Pacote validado. O serviço será parado, atualizado e reiniciado.')
        return _disparar_script(script)
    except Exception as e:
        log(f'[Atualizacao] Falha ao preparar a atualização: {e}', 'error')
        return False


def buscar_emails_processar():
    global _debug_mode
    props      = ler_properties(CONFIG_FILE)
    _debug_mode = props.get('debug', 'false').lower() == 'true'
    if _debug_mode:
        _logger.setLevel(logging.DEBUG)
        log('[DEBUG] Modo debug ativado')
    email_user = props.get('email')
    email_pass = props.get('senha')

    # A cada ciclo, e não só na subida: o script de atualização confirma o serviço
    # no ar e só então grava o resultado, alguns segundos DEPOIS de o agente já ter
    # iniciado. Verificando só no start, esse resultado nunca seria enviado.
    # Fica antes do IMAP para não depender da conexão com a caixa.
    try:
        enviar_resultados_atualizacao(props, email_user, email_pass)
    except Exception as ex:
        log(f'[Atualizacao] Erro ao enviar resultado pendente: {ex}', 'error')

    imap = imaplib.IMAP4_SSL('imap.gmail.com')
    imap.login(email_user, email_pass)
    imap.select('inbox')

    status, mensagens = imap.search(None, 'UNSEEN')
    if status != 'OK':
        log('Erro ao buscar mensagens na caixa de entrada.', 'error')
        return

    ids = mensagens[0].split()
    log(f'Emails não lidos encontrados: {len(ids)}')

    for num in ids:
        status, dados = imap.fetch(num, '(RFC822)')
        if status != 'OK':
            continue
        msg     = email.message_from_bytes(dados[0][1])
        assunto = decodifica_assunto(msg.get('Subject', ''))
        corpo   = extrair_corpo(msg)

        # Usuário do Windows (máquina que usou o BEC) e PID passam a acompanhar
        # cada linha de log gerada durante o tratamento deste e-mail.
        usuario_msg = extrair_campo(corpo, 'Usuario')
        pid_msg     = extrair_campo(corpo, 'PID')
        definir_contexto(usuario_msg, pid_msg)

        try:
            funcionalidade = _funcionalidade_do_assunto(assunto)
            if funcionalidade:
                registrar_acao_usuario(funcionalidade, pid_msg, usuario_msg)

            if '[Solicitação Log]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_solicitacao_log(imap, num, corpo, props, email_user, email_pass)

            elif '[Parametrização PDV]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_parametrizacao(imap, num, corpo, props, email_user, email_pass)

            elif '[Verificar Parametrização]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_verificar_parametrizacao(imap, num, corpo, props, email_user, email_pass)

            elif '[Relatório Parametrização]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_relatorio_parametrizacao(imap, num, corpo, props, email_user, email_pass)

            elif '[Status PDV]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_status_pdv(imap, num, corpo, props, email_user, email_pass)

            elif '[Fechar PDV]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_fechar_pdv(imap, num, corpo, props)

            elif '[Reiniciar PDV]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_reiniciar_pdv(imap, num, corpo, props)

            elif '[PinPad]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_pinpad(imap, num, corpo, props, email_user, email_pass)

            elif '[Registro Execucao]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                processar_registro_execucao(imap, num, corpo)

            elif '[Atualizacao Agente]' in assunto:
                log(f'Tratando e-mail: {assunto}')
                if processar_atualizacao(imap, num, msg, corpo, props, email_user):
                    # O serviço será parado pelo script; encerra o laço para não
                    # processar outros e-mails no meio da troca de arquivos. Eles
                    # continuam não lidos e serão tratados após o reinício.
                    break

            else:
                log(f'Ignorando e-mail: {assunto}')
                imap.store(num, '-FLAGS', '\\Seen')
        finally:
            limpar_contexto()

    imap.logout()


# ---------------------------------------------------------------------------
# Funcionalidade 7: PinPad via Cloudflare Tunnel (polling HTTP)
# ---------------------------------------------------------------------------
def _executar_pinpad_local(comando, porta):
    """Executa comando serial no PinPad local via PowerShell. Retorna (sucesso, mensagem)."""
    import subprocess as _sp
    ps_script = (
        f"$port=New-Object System.IO.Ports.SerialPort '{porta}',115200,None,8,one;"
        f"try{{$port.Open();Start-Sleep -m 500;$port.WriteLine('{comando}');$port.Close();"
        f"echo 'Comando [{comando}] enviado para {porta}'}}catch{{echo \"ERRO: $_\"}}"
    )
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    ps_exe  = 'powershell.exe'
    for sub in (r'SysNative\WindowsPowerShell\v1.0', r'System32\WindowsPowerShell\v1.0'):
        candidato = os.path.join(sysroot, sub, 'powershell.exe')
        if os.path.exists(candidato):
            ps_exe = candidato
            break
    resultado = _sp.run(
        [ps_exe, '-NoProfile', '-NonInteractive', '-Command', ps_script],
        capture_output=True, text=True, timeout=10
    )
    saida   = (resultado.stdout or resultado.stderr or '').strip()
    sucesso = 'ERRO' not in saida.upper() and resultado.returncode == 0
    return sucesso, saida


# Extrações de log são serializadas entre si: cópia por SMB e zip são pesados, e
# duas extrações simultâneas competiriam pela mesma rede e pelo mesmo disco. Não
# bloqueia o polling, que segue em outra thread atendendo PinPad no meio tempo.
_lock_extracao_relay = threading.Lock()

# O relay só descarta o item pendente quando recebe o POST /resultado/<pid> — o
# GET /pendente apenas lê. Como a extração de logs leva minutos, o mesmo pedido
# reaparece em todos os polls até a resposta ser enviada. Sem este controle, cada
# ciclo de 2s dispararia uma nova extração e um novo e-mail para o mesmo PID.
_pids_em_andamento = set()
_lock_pids         = threading.Lock()


def _reservar_pid(pid):
    """Marca o PID como em tratamento. False se já havia sido reservado."""
    with _lock_pids:
        if pid in _pids_em_andamento:
            return False
        _pids_em_andamento.add(pid)
        return True


def _liberar_pid(pid):
    with _lock_pids:
        _pids_em_andamento.discard(pid)


def _corpo_solicitacao_log(dados):
    """Reconstrói o corpo chave/valor a partir do JSON da fila.

    Assim o caminho do relay reaproveita integralmente o
    `processar_solicitacao_log`, que já sabe interpretar esse formato — em vez de
    duplicar a lógica de extração, histórico e montagem do e-mail.
    """
    return '\n'.join([
        f"PID: {dados.get('pid', '')}",
        f"Usuario: {dados.get('usuario', '')}",
        f"Destino: {dados.get('destino', '')}",
        f"Loja: {dados.get('loja', '')}",
        f"PDV: {dados.get('pdv', '')}",
        f"Logs: {dados.get('logs', '')}",
        f"Data: {dados.get('data', '')}",
    ])


# Tipos aceitos na fila do relay. Cada entrada aponta para o mesmo handler que
# atende o e-mail equivalente, o nome da funcionalidade na trilha de ações e se o
# handler recebe as credenciais de e-mail (os que respondem ao solicitante) ou
# não (os que apenas agem no PDV).
#
# O nome da funcionalidade tem de bater com FUNCIONALIDADES_POR_ASSUNTO, senão a
# mesma ação apareceria na trilha com nomes diferentes conforme o canal usado.
#   nome       : como a ação aparece na trilha. None quando o próprio handler a
#                registra — é o caso do registro de execução, cujo nome vem do
#                corpo e não do tipo.
#   assinatura : 'completo'   = (imap, num, corpo, props, email_user, email_pass)
#                'props'      = (imap, num, corpo, props)
#                'simples'    = (imap, num, corpo)
#                'atualizacao'= (imap, num, msg, corpo, props, email_user, dados_zip)
#   serializa  : se concorre pela rede/disco dos PDVs e precisa do lock. O
#                registro de execução só acrescenta uma linha em arquivo, e ficar
#                atrás de uma extração de minutos o atrasaria sem motivo.
#   ack_antes  : responde ao relay ANTES de executar. Só a atualização usa: ela
#                para o serviço e troca o executável, então o processo morre no
#                meio e a resposta nunca sairia. Sem o ack, o item ficaria preso
#                no relay e voltaria a cada poll depois do reinício.
_TIPOS_RELAY = {
    'solicitacao_log':          ('Solicitar Logs',           'completo',    True,  False),
    'parametrizacao_pdv':       ('Parametrização PDV',       'completo',    True,  False),
    'verificar_parametrizacao': ('Verificar Parametrização', 'completo',    True,  False),
    'relatorio_parametrizacao': ('Relatório Parametrização', 'completo',    True,  False),
    'status_pdv':               ('Status PDV',               'completo',    True,  False),
    'fechar_pdv':               ('Fechar PDV',               'props',       True,  False),
    'reiniciar_pdv':            ('Reiniciar PDV',            'props',       True,  False),
    'registro_execucao':        (None,                       'simples',     False, False),
    'atualizacao_agente':       ('Atualizar Agente',         'atualizacao', True,  True),
}


def _handler_do_tipo(tipo):
    """Resolve o handler na hora da chamada, não na definição do dicionário —
    as funções `processar_*` são declaradas depois deste ponto no arquivo."""
    return {
        'solicitacao_log':          processar_solicitacao_log,
        'parametrizacao_pdv':       processar_parametrizacao,
        'verificar_parametrizacao': processar_verificar_parametrizacao,
        'relatorio_parametrizacao': processar_relatorio_parametrizacao,
        'status_pdv':               processar_status_pdv,
        'fechar_pdv':               processar_fechar_pdv,
        'reiniciar_pdv':            processar_reiniciar_pdv,
        'registro_execucao':        processar_registro_execucao,
        'atualizacao_agente':       processar_atualizacao,
    }.get(tipo)


def _tratar_item_relay(tipo, dados, props, email_user, email_pass, responder):
    """Trata um item da fila do relay, em thread própria.

    O corpo chega pronto no payload, no mesmo formato chave/valor do e-mail — o
    handler é literalmente o mesmo dos dois canais, então o resultado (inclusive
    o e-mail de resposta ao solicitante) sai idêntico.
    """
    funcionalidade, assinatura, serializa, ack_antes = _TIPOS_RELAY[tipo]
    handler = _handler_do_tipo(tipo)

    # `corpo` é a forma canônica. O fallback cobre um BEC ainda na v2.41.0, que
    # mandava a solicitação de log em campos separados.
    corpo   = dados.get('corpo') or _corpo_solicitacao_log(dados)
    pid     = dados.get('pid', '') or extrair_campo(corpo, 'PID')
    usuario = dados.get('usuario', '') or extrair_campo(corpo, 'Usuario')

    # Contexto é thread-local: usuário e PID carimbam as linhas de log desta
    # thread, sem vazar para o polling nem para outra solicitação em paralelo.
    # Nome só para as mensagens de log — o registro na trilha vem logo abaixo
    rotulo = funcionalidade or extrair_campo(corpo, 'Funcionalidade') or tipo

    definir_contexto(usuario, pid)
    try:
        # Mesma trilha do caminho por e-mail. No modo tunnel não existe assunto
        # para o agente classificar, então o registro é feito aqui — exceto
        # quando o handler já o faz por conta própria (funcionalidade=None), caso
        # em que registrar aqui gravaria o nome errado e a dedução por PID
        # engoliria o registro correto que viria depois.
        if funcionalidade and registrar_acao_usuario(funcionalidade, pid, usuario):
            log(f'[Relay] Ação registrada na trilha: {funcionalidade} | usuario={usuario}')

        log(f'[Relay] {rotulo} recebida pela fila — PID={pid}')

        if ack_antes:
            # Responde já: a atualização derruba este processo no meio do caminho,
            # e o resultado real chega por e-mail quando a nova versão sobe.
            log('[Relay] Respondendo ao relay antes de executar — o processo será reiniciado.')
            responder(True, f'{rotulo} recebida; o resultado virá por e-mail.')

        def _executar():
            if assinatura == 'completo':
                handler(None, None, corpo, props, email_user, email_pass)
            elif assinatura == 'props':
                handler(None, None, corpo, props)
            elif assinatura == 'atualizacao':
                dados_zip = _base64.b64decode(dados.get('arquivo', ''))
                handler(None, None, None, corpo, props, email_user, dados_zip)
            else:
                handler(None, None, corpo)

        if serializa:
            # Serializa entre si tudo que mexe em PDV por SMB/rede: duas execuções
            # simultâneas competiriam pela mesma rede e pelo mesmo disco.
            with _lock_extracao_relay:
                _executar()
        else:
            _executar()

        if not ack_antes:
            responder(True, f'{rotulo} executada pelo agente')
    except Exception as ex:
        log(f'[Relay] Erro ao tratar {rotulo} PID={pid}: {ex}', 'error')
        if not ack_antes:
            responder(False, str(ex))
    finally:
        # Só libera depois de responder: é a resposta que apaga o item no relay.
        # Liberar antes abriria uma janela para o próximo poll reapresentar o
        # mesmo pedido e disparar uma segunda execução.
        _liberar_pid(pid)
        limpar_contexto()


def _parsear_janela(texto):
    """'08:00-20:00' -> (480, 1200) em minutos desde a meia-noite. Vazio = 24h."""
    texto = (texto or '').strip()
    if not texto:
        return None
    try:
        ini, fim = texto.split('-', 1)
        hi, mi = [int(x) for x in ini.strip().split(':')]
        hf, mf = [int(x) for x in fim.strip().split(':')]
        return hi * 60 + mi, hf * 60 + mf
    except Exception:
        log(f'[Tunnel] polling_janela invalida: "{texto}". Ignorando (24h).', 'warning')
        return None


_DIAS = {'seg': 0, 'ter': 1, 'qua': 2, 'qui': 3, 'sex': 4, 'sab': 5, 'dom': 6}


def _parsear_dias(texto):
    """'seg-sex' ou 'seg,qua,sex' -> set de weekday(). Vazio/'todos' = todos."""
    texto = (texto or '').strip().lower()
    if not texto or texto == 'todos':
        return None
    try:
        if '-' in texto:
            ini, fim = [d.strip() for d in texto.split('-', 1)]
            a, b = _DIAS[ini], _DIAS[fim]
            return set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
        return {_DIAS[d.strip()] for d in texto.split(',') if d.strip()}
    except Exception:
        log(f'[Tunnel] polling_dias invalido: "{texto}". Ignorando (todos os dias).', 'warning')
        return None


def _dentro_da_janela(janela, dias, quando=None):
    """True quando o agente deve buscar trabalho no relay agora.

    Fora da janela o agente nao chama o relay — e o que de fato reduz o consumo
    de KV, porque cada busca custa uma leitura da cota diaria.
    """
    quando = quando or datetime.now()
    if dias is not None and quando.weekday() not in dias:
        return False
    if janela is None:
        return True
    minutos = quando.hour * 60 + quando.minute
    ini, fim = janela
    # Janela que cruza a meia-noite (ex.: 22:00-06:00)
    return (ini <= minutos < fim) if ini <= fim else (minutos >= ini or minutos < fim)


def _tunnel_loop(props):
    """Thread de polling HTTP do relay. Executa indefinidamente.

    O campo `tipo` do item discrimina o tratamento:
      - qualquer chave de `_TIPOS_RELAY` : delega ao mesmo handler do e-mail
        equivalente, em thread própria (resposta ao solicitante segue por e-mail)
      - `pinpad` ou ausente : comando serial no PinPad local, inline por ser
        rápido — a ausência do campo preserva o comportamento legado
    """
    loja       = props.get('loja', '')
    pdv        = props.get('pdv', '')
    url        = props.get('bec_tunnel_url', '').rstrip('/')
    token      = props.get('pinpad_tunnel_token', '')
    porta      = props.get('pinpad_porta', 'COM10')
    email_user = props.get('email', '')
    email_pass = props.get('senha', '')

    # Cada busca no relay custa uma leitura da cota diaria do KV (100.000/dia no
    # plano gratuito). Buscando a cada 2s sao ~43.200 por dia, so deste agente —
    # 43% da cota parada, sem ninguem usando o BEC. Janela de horario e intervalo
    # ocioso existem para cortar isso.
    janela   = _parsear_janela(props.get('polling_janela', ''))
    dias     = _parsear_dias(props.get('polling_dias', ''))
    intervalo_ativo  = max(1, int(props.get('polling_intervalo_seg', 2) or 2))
    intervalo_ocioso = max(1, int(props.get('polling_intervalo_ocioso_seg', 15) or 15))
    ocioso_apos      = max(0, int(props.get('polling_ocioso_apos_seg', 120) or 120))

    if not loja or not pdv or not url:
        log('[Tunnel] loja, pdv ou bec_tunnel_url não configurados — polling desativado.', 'warning')
        return

    desc_janela = props.get('polling_janela', '').strip() or '24h'
    desc_dias   = props.get('polling_dias', '').strip() or 'todos os dias'
    log(f'[Tunnel] Iniciando polling para loja={loja} pdv={pdv} em {url}')
    log(f'[Tunnel] Janela: {desc_janela} ({desc_dias}) | intervalo {intervalo_ativo}s ativo, '
        f'{intervalo_ocioso}s ocioso apos {ocioso_apos}s sem trabalho')

    _HEADERS_BASE = {
        'X-Token': token,
        'User-Agent': 'AgentExtratarLog/1.0',
        'Accept': 'application/json',
    }

    def _get(endpoint):
        req = _urllib_req.Request(f'{url}{endpoint}', headers=_HEADERS_BASE)
        try:
            with _urllib_req.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except _urllib_err.HTTPError as e:
            corpo = b''
            try: corpo = e.read()
            except Exception: pass
            log(f'[Tunnel] HTTP {e.code} em GET {endpoint}: {corpo[:200]}', 'warning')
            return e.code, corpo
        except Exception as e:
            log(f'[Tunnel] Erro de conexão em GET {endpoint}: {e}', 'warning')
            return 0, b''

    def _post(endpoint, payload):
        data = _json.dumps(payload).encode('utf-8')
        h    = {**_HEADERS_BASE, 'Content-Type': 'application/json'}
        req  = _urllib_req.Request(f'{url}{endpoint}', data=data, headers=h, method='POST')
        try:
            with _urllib_req.urlopen(req, timeout=10) as resp:
                return resp.status
        except _urllib_err.HTTPError as e:
            log(f'[Tunnel] HTTP {e.code} em POST {endpoint}', 'warning')
            return e.code
        except Exception as e:
            log(f'[Tunnel] Erro de conexão em POST {endpoint}: {e}', 'warning')
            return 0

    _poll_count   = 0
    _ultimo_item  = 0.0    # quando chegou o ultimo trabalho
    _fora_avisado = False

    while True:
        # Fora da janela nao chega nem a chamar o relay: e assim que o consumo
        # de KV cai de verdade. Reavalia a cada minuto para entrar na hora certa.
        if not _dentro_da_janela(janela, dias):
            if not _fora_avisado:
                log(f'[Tunnel] Fora da janela de atendimento ({desc_janela}, {desc_dias}) — '
                    f'polling suspenso.')
                _fora_avisado = True
            time.sleep(60)
            continue
        if _fora_avisado:
            log('[Tunnel] Dentro da janela de atendimento — polling retomado.')
            _fora_avisado = False

        try:
            status, body = _get(f'/pendente/{loja}/{pdv}')
            _poll_count += 1
            if _poll_count % 30 == 0:
                log(f'[Worker] Polling ativo — {_poll_count} ciclos | último status HTTP: {status}')
            if status == 200 and body:
                _ultimo_item = time.time()
                dados = _json.loads(body)
                pid   = dados.get('pid', '')
                # Itens antigos não têm `tipo`; a ausência significa PinPad, para
                # não quebrar o que já estava em uso.
                tipo  = (dados.get('tipo') or 'pinpad').strip().lower()

                def _responder(sucesso, mensagem, _pid=pid):
                    # É este POST que apaga o item pendente no relay. Se ele
                    # falhar, o pedido fica na fila e voltaria em todo poll, por
                    # isso vale insistir algumas vezes antes de desistir.
                    for tentativa in range(1, 4):
                        status_post = _post(f'/resultado/{_pid}', {'sucesso': sucesso, 'mensagem': mensagem})
                        if status_post in (200, 201):
                            log(f'[Worker] Resultado enviado — HTTP {status_post}')
                            return
                        log(f'[Worker] Falha ao enviar resultado de {_pid} '
                            f'(tentativa {tentativa}/3) — HTTP {status_post}', 'warning')
                        time.sleep(2)
                    log(f'[Worker] Resultado de {_pid} não foi aceito pelo relay. '
                        f'O item segue pendente e será ignorado até o agente reiniciar.', 'error')

                if tipo in _TIPOS_RELAY:
                    if not _reservar_pid(pid):
                        # Item já em tratamento — o relay o mantém pendente até
                        # recebermos o resultado. Silencioso de propósito: com
                        # polling de 2s isso repetiria por toda a execução.
                        logd(f'[Worker] Item PID={pid} já em andamento — ignorando reapresentação.')
                    else:
                        # Em thread separada: extrair logs ou percorrer vários PDVs
                        # leva minutos, e o polling não pode parar esperando.
                        threading.Thread(
                            target=_tratar_item_relay,
                            args=(tipo, dados, props, email_user, email_pass, _responder),
                            daemon=True,
                        ).start()
                        log(f'[Worker] {_TIPOS_RELAY[tipo][0] or tipo} PID={pid} despachada para execução.')
                else:
                    comando   = dados.get('comando', '')
                    porta_cmd = dados.get('porta', porta)
                    usuario   = dados.get('usuario', '')

                    # Mesmo tratamento dado aos demais tipos: contexto para as
                    # linhas de log saírem com usuário/PID, e registro na trilha.
                    # Executa inline por ser rápido (~2s), então o contexto é
                    # limpo logo em seguida.
                    definir_contexto(usuario, pid)
                    try:
                        if registrar_acao_usuario('PinPad', pid, usuario):
                            log(f'[Worker] Ação registrada na trilha: PinPad | usuario={usuario}')
                        log(f'[Worker] Comando recebido: {comando} | PID={pid} | Porta={porta_cmd}')
                        try:
                            sucesso, mensagem = _executar_pinpad_local(comando, porta_cmd)
                        except Exception as e:
                            sucesso, mensagem = False, str(e)
                        log(f'[Worker] Resultado: sucesso={sucesso} | {mensagem}')
                        _responder(sucesso, mensagem)
                    finally:
                        limpar_contexto()
        except Exception as ex:
            log(f'[Worker] Exceção no loop: {ex}', 'error')

        # Rapido logo depois de um trabalho (quem esta testando espera resposta),
        # devagar quando ninguem usa — que e a maior parte do dia.
        recente = (time.time() - _ultimo_item) < ocioso_apos
        time.sleep(intervalo_ativo if recente else intervalo_ocioso)


def main():
    props     = ler_properties(CONFIG_FILE)
    intervalo = int(props.get('intervalo_minutos', 5))
    log(f'Agente iniciado. Versão {AGENTE_VERSAO}. '
        f'Intervalo de verificação: {intervalo} minuto(s).')

    # Se o agente subiu logo após uma atualização, responde o resultado
    try:
        enviar_resultados_atualizacao(props, props.get('email'), props.get('senha'))
    except Exception as ex:
        log(f'[Atualizacao] Erro ao enviar resultado pendente: {ex}', 'error')

    # Um único polling atende todas as funcionalidades em modo tunnel; basta que
    # uma delas esteja configurada assim para a thread subir.
    modos = {
        'PinPad':         props.get('pinpad_modo_comunicacao', 'email').strip().lower(),
        'Solicitar Logs': props.get('logs_modo_comunicacao', 'email').strip().lower(),
        'Manutenção PDV': props.get('pdv_modo_comunicacao', 'email').strip().lower(),
        'Registro de Execução': props.get('registro_modo_comunicacao', 'email').strip().lower(),
        'Atualizar Agente':     props.get('atualizacao_modo_comunicacao', 'email').strip().lower(),
    }
    ativos = [nome for nome, modo in modos.items() if modo == 'tunnel']
    if ativos:
        t = threading.Thread(target=_tunnel_loop, args=(props,), daemon=True)
        t.start()
        log(f'[Tunnel] Thread de polling iniciada. Funcionalidades em modo tunnel: {", ".join(ativos)}')

    while True:
        try:
            buscar_emails_processar()
        except Exception as ex:
            log(f'Erro geral no ciclo: {ex}', 'error')
        time.sleep(intervalo * 60)

if __name__ == '__main__':
    # Autoteste de logging: cria/append no arquivo fixo e sai, sem conectar em
    # e-mail nem tocar nos PDVs. Uso: agent_extrator_log.exe --selftest-log
    if '--selftest-log' in sys.argv:
        log('[SELFTEST] Verificacao de logging OK')
        print(f'LOG_FILE={LOG_FILE}')
        print(f'Existe apos escrever: {os.path.exists(LOG_FILE)}')
        sys.exit(0)
    main()
