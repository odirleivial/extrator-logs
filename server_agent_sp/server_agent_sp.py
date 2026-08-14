# ---------------------------------------------------------------------------
# Server Agent SP (agente_sp)
# Versão reduzida do Agent Extrator Log: apenas extração de logs de servidores.
# Responde somente a e-mails com assunto contendo [Solicitação Log SP].
# Executa como serviço Windows via NSSM (ver instalar_servico.bat).
# ---------------------------------------------------------------------------
import imaplib, email, smtplib
import os, time, csv, zipfile, re, logging, fnmatch
import ctypes, ctypes.wintypes
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

AGENT_VERSION = '1.4.0'
ASSUNTO_GATILHO = '[Solicitação Log SP]'

CONFIG_FILE = os.path.join(BASE_DIR, 'agent.properties')
CSV_LOG     = os.path.join(BASE_DIR, 'historico_envio_logs.csv')
LOG_DIR     = os.path.join(BASE_DIR, 'log')

os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging diário (mesmo esquema do agente principal)
# ---------------------------------------------------------------------------
LOG_FILE_BASE = 'server_agent_sp'
LOG_FILE      = os.path.join(LOG_DIR, f'{LOG_FILE_BASE}.log')

_log_date = None
_logger = logging.getLogger('agente_sp')
_logger.setLevel(logging.INFO)

def _arquivar_log_anterior(data_anterior):
    """Renomeia o log fixo para server_agent_sp_<data_anterior>.log.
    Se já existir arquivo para essa data, anexa o conteúdo."""
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

    for h in _logger.handlers[:]:
        h.close()
        _logger.removeHandler(h)

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
    # Sem permissão de escrita no arquivo (ex.: rodando fora do serviço, com
    # usuário sem acesso à pasta), segue apenas com saída no console em vez de abortar.
    try:
        handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        _logger.addHandler(handler)
    except Exception as e:
        print(f'[LOG] Sem gravacao em {LOG_FILE} ({e}). Seguindo somente com log no console.')

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
            writer.writerow(['PID', 'Destino', 'Logs', 'Data', 'ArquivoZip', 'DataHora', 'Status', 'Erro'])
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

def extrair_campo(corpo, campo):
    # Lê o valor na MESMA linha do campo. Com \s* a busca atravessaria a quebra
    # de linha e, num campo vazio, capturaria o conteúdo da linha seguinte.
    m = re.search(rf'(?m)^[ \t]*{re.escape(campo)}[ \t]*:[ \t]*(.*)$', corpo)
    return m.group(1).strip() if m else ''

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
# Autenticação UNC nas shares dos servidores
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

def _share_raiz(caminho_unc):
    """Extrai \\\\servidor\\share de um caminho UNC completo. Retorna '' se não for UNC."""
    m = re.match(r'^(\\\\[^\\]+\\[^\\]+)', caminho_unc)
    return m.group(1) if m else ''

def autenticar_unc(share, windows_user, windows_senha):
    """Autentica na share do servidor usando WNetAddConnection2 no processo atual."""
    if not windows_user or not windows_senha:
        log('Aviso: windows_user/windows_senha não configurados em agent.properties — usando sessão atual', 'warning')
        return

    # Remove conexão anterior para evitar conflito de credenciais (erro 1219)
    ctypes.windll.mpr.WNetCancelConnection2W(share, 0, True)

    nr = _NETRESOURCEW()
    nr.dwType       = 1  # RESOURCETYPE_DISK
    nr.lpRemoteName = share

    resultado = ctypes.windll.mpr.WNetAddConnection2W(
        ctypes.byref(nr),
        windows_senha or None,
        windows_user  or None,
        0
    )

    if resultado == 0:
        log(f'Autenticado em {share} (usuario={windows_user})')
    elif resultado == 1219:
        # Credencial já existe para esse servidor — conexão válida
        log(f'Conexão já estabelecida em {share}')
    else:
        raise PermissionError(f'Falha ao autenticar em {share}: erro WNet={resultado}')

# ---------------------------------------------------------------------------
# Resolução do formato de nome do arquivo de log
# Sintaxe do formato (em agent.properties, chave log.<nome>.formato):
#   {texto}  -> parte fixa do nome (literal)
#   [tokens] -> parte de data, tokens: yyyy=ano 4 díg., yy=ano 2 díg.,
#               mm=mês, dd=dia (maiúsculas/minúsculas indiferentes)
#   Texto fora de chaves/colchetes (ex.: extensão .log) é mantido como está.
# Exemplos: {integrador}.log            -> integrador.log
#           {lgComandosSQL_}[YYYYmmdd].txt -> lgComandosSQL_20260803.txt
#           [ddmmyyyy].log              -> 03082026.log
# ---------------------------------------------------------------------------
def _tokens_data(tokens, data):
    """Converte a sequência de tokens de data em string usando a data informada."""
    resultado = ''
    i = 0
    while i < len(tokens):
        trecho4 = tokens[i:i+4].lower()
        trecho2 = tokens[i:i+2].lower()
        if trecho4 == 'yyyy':
            resultado += data.strftime('%Y'); i += 4
        elif trecho2 == 'yy':
            resultado += data.strftime('%y'); i += 2
        elif trecho2 == 'mm':
            resultado += data.strftime('%m'); i += 2
        elif trecho2 == 'dd':
            resultado += data.strftime('%d'); i += 2
        else:
            resultado += tokens[i]; i += 1
    return resultado

# resolver_formato foi unificado com o dos históricos: ver resolver_formato_log,
# que vale para as duas configurações.

def parsear_data(texto):
    """Interpreta o campo opcional Data do e-mail (dd/mm/yyyy, dd-mm-yyyy ou yyyy-mm-dd)."""
    texto = texto.strip()
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y'):
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None

# ---------------------------------------------------------------------------
# Logs históricos (dias anteriores)
#
# Configuração no agent.properties:
#   historico.<log>.caminho = pasta base onde o arquivo/pasta do dia fica
#   historico.<log>.formato = nomenclatura do arquivo ou da pasta
#   historico.<log>.tipo    = arquivo | pasta
#
# Tokens: [..] e {..} viram data quando o conteúdo só tem marcadores de data,
# senão são texto fixo; (xxx) é curinga; (LOJA)/(PDV) são as variáveis da
# solicitação. Texto fora dos delimitadores é literal.
# ---------------------------------------------------------------------------
_RE_TOKEN_FORMATO = re.compile(r'\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\}')
_RE_TOKEN_SO_DATA = re.compile(r'^[ymdhs\-_/.: ]+$')

def _token_eh_data(conteudo):
    """Classifica o token pelo conteúdo e não pelo delimitador, para tolerar
    [..] e {..} trocados na configuração."""
    c = conteudo.lower()
    return bool(c) and bool(_RE_TOKEN_SO_DATA.match(c)) and bool(re.search(r'[ymd]', c))

def resolver_formato_log(formato, data=None, loja='', pdv=''):
    """Resolve o formato no nome (ou padrão com curinga) do dia informado.
    Vale para os logs do dia e para os históricos.
    Ex.: '{linx-webservices_}[yyyy-mm-dd](xxx).zip'
         -> 'linx-webservices_2026-08-03*.zip'"""
    data = data or datetime.now()
    def _sub(m):
        conteudo = next(g for g in m.groups() if g is not None)
        chave = conteudo.strip().upper()
        if chave == 'LOJA':
            return loja
        if chave == 'PDV':
            return pdv
        if chave == 'XXX':
            return '*'
        if _token_eh_data(conteudo):
            return _tokens_data(conteudo, data)
        return conteudo
    return _RE_TOKEN_FORMATO.sub(_sub, formato)

def localizar_itens(base, padrao, tipo):
    """Arquivos (ou pastas) que casam com o padrão dentro da base. Com curinga
    pode retornar mais de um — é o caso dos logs rotacionados no mesmo dia.
    Retorna (itens, motivo) — motivo preenchido só quando a lista vem vazia."""
    eh_pasta = (tipo == 'pasta')
    try:
        if '*' in padrao:
            candidatos = fnmatch.filter(os.listdir(base), padrao)
            itens = [os.path.join(base, n) for n in sorted(candidatos)]
            itens = [c for c in itens if os.path.isdir(c) == eh_pasta]
        else:
            completo = os.path.join(base, padrao)
            existe = os.path.isdir(completo) if eh_pasta else os.path.isfile(completo)
            itens = [completo] if existe else []
    except OSError as e:
        return [], str(e)

    if not itens:
        return [], 'pasta não encontrada' if eh_pasta else 'arquivo não encontrado'
    return itens, ''

def resolver_log_historico(log_item, props, data):
    """Lê historico.<log>.* e devolve (base, padrao, tipo).
    Retorna None quando o log não tem configuração de histórico."""
    base    = props.get(f'historico.{log_item}.caminho', '')
    formato = props.get(f'historico.{log_item}.formato', '')
    if not base or not formato:
        return None
    tipo = props.get(f'historico.{log_item}.tipo', 'arquivo').strip().lower()
    # O caminho também passa pelo resolvedor: há logs cuja data está na PASTA e
    # não no nome do arquivo (ex.: ...\logsTesouraria\[yyyymmdd]).
    base = resolver_formato_log(base, data).rstrip('\\/')
    return base, resolver_formato_log(formato, data), tipo

def descrever_itens(itens, base):
    """Texto do que foi coletado, para a coluna "Arquivo" do e-mail."""
    if len(itens) == 1:
        return itens[0]
    nomes = ', '.join(os.path.basename(i) for i in itens)
    return f'{base}\\ ({len(itens)} arquivos: {nomes})'

# ---------------------------------------------------------------------------
# Solicitação de Logs SP
# Corpo esperado do e-mail:
#   PID: <identificador>
#   Destino: <e-mail de resposta>
#   Logs: <nome1,nome2,...>   (nomes definidos em log.<nome>.caminho no properties)
#   Data: <dd/mm/yyyy>        (opcional — data usada nos formatos [.,.]; padrão hoje)
# ---------------------------------------------------------------------------
def processar_solicitacao_log_sp(imap, num, corpo, props, email_user, email_pass):
    pid      = extrair_campo(corpo, 'PID')
    destino  = extrair_campo(corpo, 'Destino')
    logs     = extrair_campo(corpo, 'Logs')
    data_txt = extrair_campo(corpo, 'Data')

    # Data de referência: quando anterior à data atual, os logs vêm dos arquivos
    # históricos (configuração historico.<log>.*); caso contrário, do dia corrente.
    hoje     = datetime.now().date()
    data_ref = parsear_data(data_txt) if data_txt else None
    if data_txt and not data_ref:
        log(f'Aviso: campo Data "{data_txt}" inválido — usando data atual', 'warning')
    if data_ref and data_ref.date() > hoje:
        log(f'Data solicitada ({data_ref:%d/%m/%Y}) é futura — usando os logs do dia atual.', 'warning')
        data_ref = None
    historico = data_ref is not None and data_ref.date() < hoje
    data_ref  = data_ref or datetime.now()

    log(f'[SolicitacaoLogSP] PID={pid} | Logs={logs} | '
        f'Data={data_ref:%d/%m/%Y} ({"histórico" if historico else "dia atual"})')

    agora         = datetime.now()
    agora_fmt     = agora.strftime('%d/%m/%Y %H:%M:%S')
    data_ref_fmt  = data_ref.strftime('%d/%m/%Y')
    if historico:
        nome_zip = f'LOG-SP-HIST{data_ref:%Y%m%d}-{agora.strftime("%d%m%Y%H%M%S")}.zip'
    else:
        nome_zip = f'LOG-SP-{agora.strftime("%d%m%Y%H%M%S")}.zip'
    nome_zip_path = os.path.join(BASE_DIR, nome_zip)
    status_envio  = 'Sucesso'
    erro_msg      = ''

    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')

    # Rastreia status de cada arquivo:
    # {'nome', 'caminho', 'status': 'ok'|'nao_encontrado'|'sem_config'}
    arquivos_status = []
    shares_autenticadas = set()

    try:
        lista_logs = [l.strip() for l in logs.split(',') if l.strip()]
        if not lista_logs:
            raise ValueError('Campo Logs vazio no corpo do e-mail.')

        caminhos_arquivos = []  # (caminho_absoluto, arcname)
        # Pastas a compactar, deduplicadas: quando vários logs apontam para a
        # mesma pasta, ela entra no zip uma única vez.
        pastas_a_compactar = {}

        for log_item in lista_logs:
            if historico:
                cfg = resolver_log_historico(log_item, props, data_ref)
                if not cfg:
                    log(f'Aviso: log "{log_item}" sem configuração de histórico '
                        f'(historico.{log_item}.caminho/.formato).', 'warning')
                    arquivos_status.append({'nome': log_item, 'caminho': '—', 'status': 'sem_config'})
                    continue
                caminho_base, padrao, tipo = cfg
            else:
                caminho_base = props.get(f'log.{log_item}.caminho')
                formato      = props.get(f'log.{log_item}.formato')
                if not caminho_base or not formato:
                    log(f'Aviso: log "{log_item}" sem configuração (log.{log_item}.caminho/.formato).', 'warning')
                    arquivos_status.append({'nome': log_item, 'caminho': '—', 'status': 'sem_config'})
                    continue
                caminho_base = resolver_formato_log(caminho_base, data_ref).rstrip('\\/')
                padrao       = resolver_formato_log(formato, data_ref)
                tipo         = props.get(f'log.{log_item}.tipo', 'arquivo').strip().lower()

            share = _share_raiz(caminho_base)
            if share and share.lower() not in shares_autenticadas:
                try:
                    autenticar_unc(share, windows_user, windows_senha)
                except PermissionError as e:
                    log(str(e), 'error')
                shares_autenticadas.add(share.lower())

            prefixo = '[Histórico] ' if historico else ''
            rotulo  = 'Pasta' if tipo == 'pasta' else 'Arquivo'
            log(f'{prefixo}{rotulo} {log_item} -> {os.path.join(caminho_base, padrao)}')

            itens, motivo = localizar_itens(caminho_base, padrao, tipo)
            if not itens:
                log(f'Aviso: {rotulo.lower()} indisponível ({motivo}): '
                    f'{os.path.join(caminho_base, padrao)}', 'warning')
                arquivos_status.append({'nome': log_item,
                                        'caminho': os.path.join(caminho_base, padrao),
                                        'status': 'nao_encontrado'})
                continue

            if tipo == 'pasta':
                for pasta in itens:
                    chave = os.path.normcase(os.path.normpath(pasta))
                    if chave in pastas_a_compactar:
                        log(f'Pasta já incluída por outro log — não será compactada de novo: {pasta}')
                    else:
                        # Guarda o log de origem: a pasta entra no zip sob o nome
                        # dele, senão o anexo traria só pastas com a data.
                        pastas_a_compactar[chave] = (pasta, log_item)
            else:
                # Pasta por log dentro do zip evita colisão de nomes iguais
                # (ex.: dois logs com formato [ddmmyyyy].log)
                for arq in itens:
                    caminhos_arquivos.append((arq, f'{log_item}/{os.path.basename(arq)}'))

            arquivos_status.append({'nome': log_item,
                                    'caminho': descrever_itens(itens, caminho_base),
                                    'status': 'ok'})

        if not caminhos_arquivos and not pastas_a_compactar:
            raise FileNotFoundError('Nenhum arquivo válido para compactar.')

        with zipfile.ZipFile(nome_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            for arquivo, arcname in caminhos_arquivos:
                zipf.write(arquivo, arcname=arcname)
            raizes_usadas = set()
            for caminho_pasta, log_origem in pastas_a_compactar.values():
                # Desempate defensivo: duas pastas de origem homônimas dentro do
                # mesmo log se sobrescreveriam sem isto.
                nome_pasta = os.path.basename(caminho_pasta.rstrip('\\/')) or 'pasta'
                raiz = f'{log_origem}/{nome_pasta}'
                base_raiz, n = raiz, 2
                while raiz in raizes_usadas:
                    raiz = f'{base_raiz}_{n}'
                    n += 1
                raizes_usadas.add(raiz)
                total = 0
                for dirpath, _, nomes in os.walk(caminho_pasta):
                    for nome_arq in nomes:
                        completo = os.path.join(dirpath, nome_arq)
                        rel      = os.path.relpath(completo, caminho_pasta)
                        zipf.write(completo, arcname=f'{raiz}/' + rel.replace('\\', '/'))
                        total += 1
                log(f'Pasta incluída no anexo: {caminho_pasta} -> {raiz}/ ({total} arquivo(s))')

        n_ok   = sum(1 for a in arquivos_status if a['status'] == 'ok')
        n_erro = len(arquivos_status) - n_ok

        # ---- HTML do e-mail ----
        # O card da data fica em âmbar quando os logs são de um dia anterior
        if historico:
            bg_data, cor_data, rotulo_data = '#fef3c7', '#92400e', 'Data (histórico)'
        else:
            bg_data, cor_data, rotulo_data = '#f8fafc', '#1e3a5f', 'Data dos Logs'

        linhas_html = ''
        linhas_txt  = ''
        for a in arquivos_status:
            if a['status'] == 'ok':
                bg_row = ''
                badge  = "<span style='background:#dcfce7;color:#1a7f4b;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10004; Incluído</span>"
            elif a['status'] == 'nao_encontrado':
                bg_row = "background:#fff5f5;"
                badge  = "<span style='background:#fee2e2;color:#b91c1c;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10006; Não encontrado</span>"
            else:
                bg_row = "background:#fff5f5;"
                badge  = "<span style='background:#fee2e2;color:#b91c1c;font-size:11px;font-weight:bold;padding:3px 10px;border-radius:12px;display:inline-block'>&#10006; Sem configuração</span>"

            linhas_html += f"""
            <tr style='{bg_row}'>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:12px;color:#1e293b'>{a['nome']}</td>
              <td style='padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#6b7280;word-break:break-all'>{a['caminho']}</td>
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
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Solicitação de Logs SP</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{pid}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:{bg_data};border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>{rotulo_data}</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:{cor_data}'>{data_ref_fmt}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:32%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Gerado em</p>
          <p style='margin:4px 0 0;font-size:14px;font-weight:bold;color:#1e3a5f'>{agora_fmt}</p>
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
          <th style='padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Log</th>
          <th style='padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;text-transform:uppercase;border-bottom:1px solid #e5e7eb'>Arquivo</th>
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
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora_fmt} &nbsp;|&nbsp; Server Agent SP v{AGENT_VERSION}</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

        corpo_txt = (
            f"Solicitação de Logs SP\n"
            f"PID: {pid} | Data dos logs: {data_ref_fmt}\n"
            f"Resumo: {n_ok} incluído(s) | {n_erro} não encontrado(s)\n"
            f"Anexo: {nome_zip}\n"
            f"{'=' * 60}"
            + linhas_txt
        )

        enviar_email_com_anexo(email_user, email_pass, destino,
                               f'[Logs SP][{pid}]',
                               corpo_txt, nome_zip_path, corpo_html)
        log(f'Email enviado para {destino} | Arquivo: {nome_zip}')
        imap.store(num, '+FLAGS', '\\Seen')

    except Exception as e:
        status_envio = 'Erro'
        erro_msg = str(e)
        log(f'Erro ao processar log SP PID={pid}: {erro_msg}', 'error')

        # Notifica o solicitante sobre a falha, se houver destino
        if destino:
            try:
                detalhes = ''.join(
                    f"<li style='margin:4px 0;font-size:12px;color:#374151'>"
                    f"<span style='font-family:monospace'>{a['nome']}</span> — "
                    f"{'não encontrado' if a['status'] == 'nao_encontrado' else 'sem configuração'}</li>"
                    for a in arquivos_status if a['status'] != 'ok'
                )
                corpo_html_erro = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='560' cellpadding='0' cellspacing='0' style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>
  <tr><td style='background:#7f1d1d;padding:24px 28px'>
    <p style='margin:0;color:#fecaca;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Falha na Solicitação de Logs SP</h1>
  </td></tr>
  <tr><td style='padding:20px 28px'>
    <p style='margin:0 0 8px;font-size:13px;color:#374151'><strong>PID:</strong> <span style='font-family:monospace'>{pid}</span></p>
    <p style='margin:0 0 8px;font-size:13px;color:#374151'><strong>Data dos logs:</strong> {data_ref_fmt}</p>
    <p style='margin:0 0 12px;font-size:13px;color:#b91c1c'><strong>Erro:</strong> {erro_msg}</p>
    {f"<ul style='margin:0;padding-left:18px'>{detalhes}</ul>" if detalhes else ''}
  </td></tr>
  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora_fmt} &nbsp;|&nbsp; Server Agent SP v{AGENT_VERSION}</p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""
                enviar_email_html(email_user, email_pass, destino,
                                  f'[Logs SP][{pid}] - ERRO',
                                  corpo_html_erro,
                                  f'Falha na Solicitação de Logs SP\nPID: {pid}\nErro: {erro_msg}')
                log(f'E-mail de erro enviado para {destino}')
            except Exception as e2:
                log(f'Erro ao enviar e-mail de falha: {e2}', 'error')
        imap.store(num, '+FLAGS', '\\Seen')

    gravar_csv_log([pid, destino, logs, data_ref.strftime('%d/%m/%Y'), nome_zip,
                    datetime.now().isoformat(), status_envio, erro_msg])

# ---------------------------------------------------------------------------
# Loop principal de leitura de e-mails
# ---------------------------------------------------------------------------
def buscar_emails_processar():
    global _debug_mode
    props       = ler_properties(CONFIG_FILE)
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

        if ASSUNTO_GATILHO in assunto:
            log(f'Tratando e-mail: {assunto}')
            corpo = extrair_corpo(msg)
            processar_solicitacao_log_sp(imap, num, corpo, props, email_user, email_pass)
        else:
            # Devolve como não lido — pode ser de outro agente que usa a mesma caixa
            log(f'Ignorando e-mail: {assunto}')
            imap.store(num, '-FLAGS', '\\Seen')

    imap.logout()

def selftest_conexao():
    """Diagnostica conectividade de saída deste executável (TCP, IMAP e SMTP).
    Compare com o resultado do mesmo teste feito via powershell.exe: se o
    PowerShell conecta e este executável não, o bloqueio é por processo
    (antivírus/EDR), não pela rede."""
    import socket
    print(f'Server Agent SP v{AGENT_VERSION} — teste de conexao')
    print(f'Executavel: {sys.executable}')
    print('-' * 60)

    alvos = [('imap.gmail.com', 993), ('smtp.gmail.com', 587), ('www.google.com', 443)]
    for host, porta in alvos:
        try:
            ip = socket.gethostbyname(host)
        except Exception as e:
            print(f'[FALHA] DNS  {host} -> {type(e).__name__}: {e}')
            continue
        try:
            s = socket.create_connection((host, porta), timeout=10)
            s.close()
            print(f'[OK]    TCP  {host}:{porta} ({ip})')
        except Exception as e:
            print(f'[FALHA] TCP  {host}:{porta} ({ip}) -> {type(e).__name__}: {e}')

    if not os.path.exists(CONFIG_FILE):
        print(f'[AVISO] {CONFIG_FILE} nao encontrado — testes de login ignorados.')
        return
    props = ler_properties(CONFIG_FILE)
    usuario, senha = props.get('email', ''), props.get('senha', '')
    if not usuario or not senha:
        print('[AVISO] email/senha nao configurados — testes de login ignorados.')
        return

    try:
        imap = imaplib.IMAP4_SSL('imap.gmail.com')
        imap.login(usuario, senha)
        imap.select('inbox')
        status, mensagens = imap.search(None, 'UNSEEN')
        n = len(mensagens[0].split()) if status == 'OK' else 0
        imap.logout()
        print(f'[OK]    IMAP login {usuario} — {n} e-mail(s) nao lido(s)')
    except Exception as e:
        print(f'[FALHA] IMAP login -> {type(e).__name__}: {e}')

    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(usuario, senha)
        print(f'[OK]    SMTP login {usuario}')
    except Exception as e:
        print(f'[FALHA] SMTP login -> {type(e).__name__}: {e}')

def main():
    props     = ler_properties(CONFIG_FILE)
    intervalo = int(props.get('intervalo_minutos', 5))
    log(f'Server Agent SP v{AGENT_VERSION} iniciado. Intervalo de verificação: {intervalo} minuto(s).')

    while True:
        try:
            buscar_emails_processar()
        except Exception as ex:
            log(f'Erro geral no ciclo: {ex}', 'error')
        time.sleep(intervalo * 60)

if __name__ == '__main__':
    # Autoteste de logging: cria/append no arquivo fixo e sai, sem conectar em
    # e-mail nem tocar nos servidores. Uso: server_agent_sp.exe --selftest-log
    if '--selftest-log' in sys.argv:
        log('[SELFTEST] Verificacao de logging OK')
        print(f'LOG_FILE={LOG_FILE}')
        print(f'Existe apos escrever: {os.path.exists(LOG_FILE)}')
        sys.exit(0)
    # Diagnóstico de rede: server_agent_sp.exe --selftest-conexao
    if '--selftest-conexao' in sys.argv:
        selftest_conexao()
        sys.exit(0)
    # Teste do parser de formatos: server_agent_sp.exe --selftest-formato
    if '--selftest-formato' in sys.argv:
        exemplos = ['{integrador}.log', '{linx-webservices}.log', '{CSIDebugFile}.txt',
                    '{lgComandosSQL_}[YYYYmmdd].txt', '[ddmmyyyy].log', '[yyyymmdd]',
                    '{linx-webservices_}[yyyy-mm-dd](xxx).zip',
                    '{logsTesouraria_}[yyyymmdd].zip']
        for f in exemplos:
            print(f'{f}  ->  {resolver_formato_log(f)}')
        sys.exit(0)
    main()
