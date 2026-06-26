import imaplib, email, smtplib
import os, time, csv, zipfile, re, logging, ctypes, ctypes.wintypes
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
# Logging diário
# ---------------------------------------------------------------------------
_log_date = None
_logger = logging.getLogger('agente')
_logger.setLevel(logging.INFO)

def _atualizar_handler():
    global _log_date
    hoje = datetime.now().strftime('%Y-%m-%d')
    if _log_date == hoje:
        return
    _log_date = hoje
    for h in _logger.handlers[:]:
        h.close()
        _logger.removeHandler(h)
    handler = logging.FileHandler(
        os.path.join(LOG_DIR, f'operacao_{hoje}.log'),
        encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    _logger.addHandler(handler)

def log(msg, level='info'):
    _atualizar_handler()
    print(msg)
    getattr(_logger, level)(msg)

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

def enviar_email_com_anexo(remetente, senha, destino, assunto, corpo, arquivo_anexo):
    msg = MIMEMultipart()
    msg['From'] = remetente
    msg['To'] = destino
    msg['Subject'] = assunto
    msg.attach(MIMEText(corpo, 'plain', 'utf-8'))
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

# ---------------------------------------------------------------------------
# Funcionalidade 1: Solicitação de Logs
# ---------------------------------------------------------------------------
def processar_solicitacao_log(imap, num, corpo, props, email_user, email_pass):
    pid     = extrair_campo(corpo, 'PID')
    destino = extrair_campo(corpo, 'Destino')
    loja    = extrair_campo(corpo, 'Loja')
    pdv     = extrair_campo(corpo, 'PDV')
    logs    = extrair_campo(corpo, 'Logs')

    log(f'[SolicitacaoLog] PID={pid} | Loja={loja} | PDV={pdv} | Logs={logs}')

    data_atual    = datetime.now().strftime('%d%m%Y%H%M%S')
    nome_zip      = f'LOG-{loja}-{pdv}-{data_atual}.zip'
    nome_zip_path = os.path.join(BASE_DIR, nome_zip)
    status_envio  = 'Sucesso'
    erro_msg      = ''

    try:
        base_pdv      = props.get(f'PDV_{pdv}', '')
        windows_user  = props.get('windows_user', '')
        windows_senha = props.get('windows_senha', '')

        if not base_pdv:
            log(f'Aviso: IP não configurado para PDV {pdv}', 'warning')
        else:
            autenticar_unc(base_pdv, windows_user, windows_senha)

        lista_logs = [l.strip() for l in logs.split(',') if l.strip()]
        caminhos_arquivos = []

        for log_item in lista_logs:
            caminho_relativo = props.get(log_item)
            if not caminho_relativo:
                log(f'Aviso: caminho do log "{log_item}" não encontrado no properties.', 'warning')
                continue

            if log_item.strip().upper() == 'MFDE':
                loja_fmt = loja.zfill(4)
                pdv_fmt  = pdv.zfill(3)
                nome_mfde = f"MFDE{loja_fmt}{pdv_fmt}{datetime.now().strftime('%Y%m%d')}"
                base_mfde = props.get('MFDE', 'Logs')
                caminho_absoluto = os.path.join(f'\\\\{base_pdv}\\C$', base_mfde, nome_mfde) if base_pdv else os.path.join(base_mfde, nome_mfde)
            else:
                caminho_absoluto = os.path.join(f'\\\\{base_pdv}\\C$', caminho_relativo.strip(':\\')) if base_pdv else caminho_relativo

            log(f'Arquivo {log_item} -> {caminho_absoluto}')
            if os.path.exists(caminho_absoluto):
                caminhos_arquivos.append(caminho_absoluto)
            else:
                log(f'Aviso: arquivo não encontrado: {caminho_absoluto}', 'warning')

        if not caminhos_arquivos:
            raise FileNotFoundError('Nenhum arquivo válido para compactar.')

        with zipfile.ZipFile(nome_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            for arquivo in caminhos_arquivos:
                zipf.write(arquivo, arcname=os.path.basename(arquivo))

        enviar_email_com_anexo(email_user, email_pass, destino,
                               f'[Logs][{loja}][{pdv}][{pid}]',
                               f'Envio dos logs solicitados.\nLoja: {loja}\nPDV: {pdv}\nPID: {pid}',
                               nome_zip_path)
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
    """Localiza a linha com 'constante=...' e substitui o valor."""
    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(f'Arquivo não encontrado: {caminho_arquivo}')

    with open(caminho_arquivo, 'r', encoding='utf-8', errors='replace') as f:
        linhas = f.readlines()

    padrao = re.compile(rf'^(\s*{re.escape(constante)}\s*=\s*)(.*)$')
    alterado = False
    novas_linhas = []
    for linha in linhas:
        m = padrao.match(linha)
        if m:
            novas_linhas.append(f'{m.group(1)}{novo_valor}\n')
            alterado = True
        else:
            novas_linhas.append(linha)

    if not alterado:
        raise KeyError(f'Constante "{constante}" não encontrada em {caminho_arquivo}')

    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        f.writelines(novas_linhas)

def sobrescrever_arquivo(caminho_arquivo, novo_valor):
    """Remove todo o conteúdo do arquivo e escreve apenas o novo valor."""
    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(f'Arquivo não encontrado: {caminho_arquivo}')
    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        f.write(novo_valor + '\n')

def processar_parametrizacao(imap, num, corpo, props, email_user, email_pass):
    pid        = extrair_campo(corpo, 'PID')
    destino    = extrair_campo(corpo, 'Destino')
    loja       = extrair_campo(corpo, 'Loja')
    pdv        = extrair_campo(corpo, 'PDV')
    parametros = extrair_campo(corpo, 'Parametros')

    log(f'[Parametrizacao] PID={pid} | Loja={loja} | PDV={pdv} | Parametros={parametros}')

    base_pdv      = props.get(f'PDV_{pdv}', '')
    windows_user  = props.get('windows_user', '')
    windows_senha = props.get('windows_senha', '')

    if not base_pdv:
        log(f'Aviso: IP não configurado para PDV {pdv}', 'warning')
    else:
        try:
            autenticar_unc(base_pdv, windows_user, windows_senha)
        except PermissionError as e:
            log(str(e), 'error')

    lista_params = [p.strip() for p in parametros.split(',') if p.strip()]
    resultados = []

    for param in lista_params:
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
                resultados.append(f'[OK] {param}: sobrescrito com valor = {novo_valor}')
            else:
                log(f'Editando: {caminho_absoluto} | {constante}={novo_valor}')
                alterar_constante_properties(caminho_absoluto, constante, novo_valor)
                log(f'[OK] {param}: {constante} alterado para "{novo_valor}"')
                resultados.append(f'[OK] {param}: {constante} = {novo_valor}')

        except Exception as e:
            status_param = 'Erro'
            erro_param   = str(e)
            log(f'Erro no parametro {param}: {erro_param} — continuando para o proximo', 'error')
            resultados.append(f'[ERRO] {param}: {erro_param}')

        gravar_csv_param([pid, loja, pdv, param, caminho_relativo or '', constante or '',
                          novo_valor or '', datetime.now().isoformat(), status_param, erro_param])

    # Responde por e-mail com resumo
    if destino:
        corpo_resposta = (
            f'Resultado da parametrização.\n'
            f'PID: {pid}\nLoja: {loja}\nPDV: {pdv}\n\n'
            + '\n'.join(resultados)
        )
        try:
            enviar_email_texto(email_user, email_pass, destino,
                               f'[Parametrização][{loja}][{pdv}][{pid}]',
                               corpo_resposta)
            log(f'Resposta enviada para {destino}')
        except Exception as e:
            log(f'Erro ao enviar resposta de parametrização: {e}', 'error')

    imap.store(num, '+FLAGS', '\\Seen')

# ---------------------------------------------------------------------------
# Loop principal de leitura de e-mails
# ---------------------------------------------------------------------------
def buscar_emails_processar():
    props      = ler_properties(CONFIG_FILE)
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

        else:
            log(f'Ignorando e-mail: {assunto}')
            imap.store(num, '-FLAGS', '\\Seen')

    imap.logout()


def main():
    props     = ler_properties(CONFIG_FILE)
    intervalo = int(props.get('intervalo_minutos', 5))
    log(f'Agente iniciado. Intervalo de verificação: {intervalo} minuto(s).')
    while True:
        try:
            buscar_emails_processar()
        except Exception as ex:
            log(f'Erro geral no ciclo: {ex}', 'error')
        time.sleep(intervalo * 60)

if __name__ == '__main__':
    main()
