import imaplib, email, smtplib
import os, time, csv, zipfile, re, logging, ctypes, ctypes.wintypes, threading
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
        '%(asctime)s [%(levelname)s] %(message)s',
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
        imap.store(num, '+FLAGS', '\\Seen')

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

    COR = {'OK': '#1a7f4b', 'ERRO': '#b91c1c'}
    BG  = {'OK': '#dcfce7', 'ERRO': '#fee2e2'}
    ICO = {'OK': '✔',       'ERRO': '✖'}

    linhas_html = ''
    linhas_txt  = ''
    for r in resultados:
        st      = r['status']
        bg_row  = '' if st == 'OK' else "background:#fff5f5;"
        badge   = (f"<span style='background:{BG[st]};color:{COR[st]};font-size:11px;"
                   f"font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>"
                   f"{ICO[st]} {st}</span>")
        label_c = (f"<br><span style='color:#6b7280;font-size:11px'>{r['constante']}</span>"
                   if r['constante'] else '')
        esp_s, esp_f = truncar(r['esperado'])
        if r['erro']:
            atu_s, atu_f = truncar(r['erro'])
            atu_cor = '#b91c1c'
        else:
            atu_s, atu_f = truncar(r['atual'])
            atu_cor = '#374151'

        cel = "padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px"
        linhas_html += f"""
            <tr style='{bg_row}'>
              <td style='{cel};max-width:160px'>
                <span style='font-family:monospace;font-size:12px;color:#1e293b'>{r['param']}</span>
                {label_c}
              </td>
              <td style='{cel};color:#374151;max-width:140px' title='{esp_f}'>
                <span style='display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{esp_s}</span>
              </td>
              <td style='{cel};color:{atu_cor};max-width:140px' title='{atu_f}'>
                <span style='display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{atu_s}</span>
              </td>
              <td style='{cel};text-align:center;white-space:nowrap'>{badge}</td>
            </tr>"""
        linhas_txt += f"\n[{st}] {r['param']} | {r['esperado'] or r['erro']}"

    corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
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
          <th style='padding:9px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Parâmetro</th>
          <th style='padding:9px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Esperado</th>
          <th style='padding:9px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Atual</th>
          <th style='padding:9px 14px;text-align:center;color:#6b7280;font-size:11px;
                     text-transform:uppercase;border-bottom:1px solid #e5e7eb;width:90px'>Status</th>
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

    imap.store(num, '+FLAGS', '\\Seen')

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
        imap.store(num, '+FLAGS', '\\Seen')
        return

    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')
    agora         = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    grupos = _parsear_selecao(selecao, props)

    COR = {'OK': '#1a7f4b', 'DIVERGENTE': '#b45309', 'ERRO': '#b91c1c'}
    BG  = {'OK': '#dcfce7', 'DIVERGENTE': '#fef3c7', 'ERRO': '#fee2e2'}
    ICO = {'OK': '✔', 'DIVERGENTE': '⚠', 'ERRO': '✖'}

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
        <tr><td colspan='4' style='padding:14px 28px 6px;background:#f8fafc;
            border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb'>
          <span style='font-size:12px;font-weight:bold;color:#1e3a5f;text-transform:uppercase;
                       letter-spacing:.5px'>Loja {loja}</span>
        </td></tr>"""
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
                f"<span style='background:#dcfce7;color:#1a7f4b;font-size:10px;font-weight:bold;"
                f"padding:2px 8px;border-radius:10px;margin-right:4px'>{n_ok} OK</span>"
                f"<span style='background:#fef3c7;color:#b45309;font-size:10px;font-weight:bold;"
                f"padding:2px 8px;border-radius:10px;margin-right:4px'>{n_div} DIV</span>"
                f"<span style='background:#fee2e2;color:#b91c1c;font-size:10px;font-weight:bold;"
                f"padding:2px 8px;border-radius:10px'>{n_err} ERRO</span>"
            )

            secoes_html += f"""
        <tr><td colspan='4' style='padding:8px 28px 4px'>
          <span style='font-size:11px;font-weight:bold;color:#374151'>PDV {pdv}</span>
          &nbsp;&nbsp;{resumo_badges}
        </td></tr>"""
            secoes_txt += f'\n  --- PDV {pdv} ---'

            linhas_html = ''
            for r in resultados:
                st  = r['status']
                cor = COR[st]; bg = BG[st]; ico = ICO[st]
                nome_exibido = r['constante'] if r.get('constante') else r['param']
                esp_curto, esp_full = truncar(r.get('esperado', ''))
                if r.get('erro') and not r.get('atual'):
                    atu_curto, atu_full = truncar(r['erro'])
                    atu_cor = '#b91c1c'
                else:
                    atu_curto, atu_full = truncar(r.get('atual', ''))
                    atu_cor = '#374151'
                cel = "padding:8px 14px;border-bottom:1px solid #e5e7eb;font-size:11px;max-width:150px"
                linhas_html += f"""
            <tr>
              <td style='{cel}'><span style='font-family:monospace;color:#1e293b'>{nome_exibido}</span></td>
              <td style='{cel};color:#374151' title='{esp_full}'>
                <span style='white-space:nowrap;overflow:hidden;display:block;max-width:140px;text-overflow:ellipsis'>{esp_curto}</span>
              </td>
              <td style='{cel};color:{atu_cor}' title='{atu_full}'>
                <span style='white-space:nowrap;overflow:hidden;display:block;max-width:140px;text-overflow:ellipsis'>{atu_curto}</span>
              </td>
              <td style='padding:8px 14px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap'>
                <span style='background:{bg};color:{cor};font-weight:bold;font-size:10px;
                             padding:2px 8px;border-radius:10px;display:inline-block'>{ico} {st}</span>
              </td>
            </tr>"""
                secoes_txt += (
                    f"\n  [{st}] {nome_exibido}"
                    f"\n    Esperado: {r.get('esperado', '')}"
                    f"\n    Atual   : {r.get('atual', '') or r.get('erro', '')}\n"
                )

            secoes_html += f"""
        <tr><td colspan='4' style='padding:0 28px 12px'>
          <table width='100%' cellpadding='0' cellspacing='0'
                 style='border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;font-size:12px'>
            <thead><tr style='background:#f8fafc'>
              <th style='padding:8px 14px;text-align:left;color:#6b7280;font-size:10px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Parâmetro</th>
              <th style='padding:8px 14px;text-align:left;color:#6b7280;font-size:10px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Esperado</th>
              <th style='padding:8px 14px;text-align:left;color:#6b7280;font-size:10px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Atual</th>
              <th style='padding:8px 14px;text-align:center;color:#6b7280;font-size:10px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Status</th>
            </tr></thead>
            <tbody>{linhas_html}</tbody>
          </table>
        </td></tr>"""

    if destino:
        corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
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

    imap.store(num, '+FLAGS', '\\Seen')

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
        imap.store(num, '+FLAGS', '\\Seen')
        return

    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')
    agora         = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    # Parseia seleção filtrando lojas ignoradas
    grupos = _parsear_selecao(selecao, props)

    COR = {'OK': '#1a7f4b', 'DIVERGENTE': '#b45309', 'ERRO': '#b91c1c'}
    BG  = {'OK': '#dcfce7', 'DIVERGENTE': '#fef3c7', 'ERRO': '#fee2e2'}
    ICO = {'OK': '✔',       'DIVERGENTE': '⚠',       'ERRO': '✖'}

    total_ok = total_div = total_erro = 0
    secoes_html = ''
    secoes_txt  = ''

    for loja, pdvs in grupos.items():
        secoes_html += f"""
        <tr><td colspan='2' style='padding:14px 28px 6px;background:#f8fafc;
            border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb'>
          <span style='font-size:12px;font-weight:bold;color:#1e3a5f;text-transform:uppercase;
                       letter-spacing:.5px'>Loja {loja}</span>
        </td></tr>"""
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
                f"<span style='background:#dcfce7;color:#1a7f4b;font-size:10px;font-weight:bold;"
                f"padding:2px 8px;border-radius:10px;margin-right:4px'>{n_ok} OK</span>"
                f"<span style='background:#fef3c7;color:#b45309;font-size:10px;font-weight:bold;"
                f"padding:2px 8px;border-radius:10px;margin-right:4px'>{n_div} DIV</span>"
                f"<span style='background:#fee2e2;color:#b91c1c;font-size:10px;font-weight:bold;"
                f"padding:2px 8px;border-radius:10px'>{n_err} ERR</span>"
            )

            linhas_params = ''
            for r in resultados:
                st = r['status']
                descricao = r['erro'] if r['erro'] else ''
                linhas_params += f"""
                <tr>
                  <td style='padding:7px 14px;border-bottom:1px solid #f0f0f0;
                             font-family:monospace;font-size:11px;color:#374151'>
                    {r['param']}
                  </td>
                  <td style='padding:7px 14px;border-bottom:1px solid #f0f0f0;
                             text-align:right;white-space:nowrap'>
                    <span style='background:{BG[st]};color:{COR[st]};font-size:10px;font-weight:bold;
                                 padding:2px 8px;border-radius:10px'>{ICO[st]} {st}</span>
                    {'<br><span style="font-size:10px;color:#b91c1c">' + descricao[:50] + '…</span>' if descricao else ''}
                  </td>
                </tr>"""

            secoes_html += f"""
        <tr><td colspan='2' style='padding:10px 28px'>
          <table width='100%' cellpadding='0' cellspacing='0'
                 style='border:1px solid #e5e7eb;border-radius:6px;overflow:hidden'>
            <thead>
              <tr style='background:#f8fafc'>
                <th style='padding:8px 14px;text-align:left;font-size:12px;color:#1e3a5f;font-weight:bold'>
                  PDV {pdv}
                  <span style='font-size:11px;font-weight:normal;color:#6b7280;margin-left:4px'>
                    ({base_pdv or 'IP não configurado'})
                  </span>
                  <span style='font-size:11px;font-weight:normal;color:#6b7280;margin-left:4px'>
                    — Versão {versao_pdv}
                  </span>
                </th>
                <th style='padding:8px 14px;text-align:right'>{resumo_badges}</th>
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
<html><head><meta charset='utf-8'></head>
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

    imap.store(num, '+FLAGS', '\\Seen')

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
        imap.store(num, '+FLAGS', '\\Seen')
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

    imap.store(num, '+FLAGS', '\\Seen')


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
        imap.store(num, '+FLAGS', '\\Seen')
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

    imap.store(num, '+FLAGS', '\\Seen')

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

    imap.store(num, '+FLAGS', '\\Seen')

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

    imap.store(num, '+FLAGS', '\\Seen')

# ---------------------------------------------------------------------------
# Loop principal de leitura de e-mails
# ---------------------------------------------------------------------------
def buscar_emails_processar():
    global _debug_mode
    props      = ler_properties(CONFIG_FILE)
    _debug_mode = props.get('debug', 'false').lower() == 'true'
    if _debug_mode:
        _logger.setLevel(logging.DEBUG)
        log('[DEBUG] Modo debug ativado')
    email_user = props.get('email')
    email_pass = props.get('senha')

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

        else:
            log(f'Ignorando e-mail: {assunto}')
            imap.store(num, '-FLAGS', '\\Seen')

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


def _tunnel_pinpad_loop(props):
    """Thread de polling HTTP para modo Cloudflare Tunnel. Executa indefinidamente."""
    loja  = props.get('loja', '')
    pdv   = props.get('pdv', '')
    url   = props.get('bec_tunnel_url', '').rstrip('/')
    token = props.get('pinpad_tunnel_token', '')
    porta = props.get('pinpad_porta', 'COM10')

    if not loja or not pdv or not url:
        log('[Tunnel] loja, pdv ou bec_tunnel_url não configurados — polling desativado.', 'warning')
        return

    log(f'[Tunnel] Iniciando polling PinPad para loja={loja} pdv={pdv} em {url}')

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

    _poll_count = 0
    while True:
        try:
            status, body = _get(f'/pendente/{loja}/{pdv}')
            _poll_count += 1
            if _poll_count % 30 == 0:
                log(f'[Worker] Polling ativo — {_poll_count} ciclos | último status HTTP: {status}')
            if status == 200 and body:
                dados     = _json.loads(body)
                pid       = dados.get('pid', '')
                comando   = dados.get('comando', '')
                porta_cmd = dados.get('porta', porta)
                log(f'[Worker] Comando recebido: {comando} | PID={pid} | Porta={porta_cmd}')
                try:
                    sucesso, mensagem = _executar_pinpad_local(comando, porta_cmd)
                except Exception as e:
                    sucesso, mensagem = False, str(e)
                log(f'[Worker] Resultado: sucesso={sucesso} | {mensagem}')
                status_post = _post(f'/resultado/{pid}', {'sucesso': sucesso, 'mensagem': mensagem})
                log(f'[Worker] Resultado enviado — HTTP {status_post}')
        except Exception as ex:
            log(f'[Worker] Exceção no loop: {ex}', 'error')
        time.sleep(2)


def main():
    props     = ler_properties(CONFIG_FILE)
    intervalo = int(props.get('intervalo_minutos', 5))
    log(f'Agente iniciado. Intervalo de verificação: {intervalo} minuto(s).')

    if props.get('pinpad_modo_comunicacao', 'email') == 'tunnel':
        t = threading.Thread(target=_tunnel_pinpad_loop, args=(props,), daemon=True)
        t.start()
        log('[Tunnel] Thread de polling PinPad iniciada.')

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
