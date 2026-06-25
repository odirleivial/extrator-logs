import imaplib, email, smtplib
import os, time, csv, zipfile, re
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime


CONFIG_FILE = 'agent.properties'
CSV_LOG = 'historico_envio_logs.csv'

def ler_properties(arquivo):
    props = {}
    with open(arquivo, 'r') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                props[key.strip()] = value.strip()
    return props

def gravar_csv(dados):
    existe = os.path.exists(CSV_LOG)
    with open(CSV_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(['PID', 'Destino', 'Loja', 'PDV', 'Logs', 'Arquivo Zip', 'DataHora', 'Status', 'MensagemErro'])
        writer.writerow(dados)

def decodifica_assunto(assunto_header):
    assunto, charset = decode_header(assunto_header)[0]
    if isinstance(assunto, bytes):
        assunto = assunto.decode(charset or 'utf-8', errors='replace')
    return assunto

def extrair_info_corpo(corpo):
    pid = destino = loja = pdv = logs = ''
    pid_m = re.search(r'PID:\s*([A-Za-z0-9]+)', corpo)
    destino_m = re.search(r'Destino:\s*(.+)', corpo)
    loja_m = re.search(r'Loja:\s*(.+)', corpo)
    pdv_m = re.search(r'PDV:\s*(.+)', corpo)
    logs_m = re.search(r'Logs:\s*(.+)', corpo)

    if pid_m: pid = pid_m.group(1).strip()
    if destino_m: destino = destino_m.group(1).strip()
    if loja_m: loja = loja_m.group(1).strip()
    if pdv_m: pdv = pdv_m.group(1).strip()
    if logs_m: logs = logs_m.group(1).strip()
    return pid, destino, loja, pdv, logs

def compactar_log(caminho_completo, nome_zip):
    if not os.path.exists(caminho_completo):
        raise FileNotFoundError(f"Arquivo de log não encontrado: {caminho_completo}")
    with zipfile.ZipFile(nome_zip, 'w') as zipf:
        zipf.write(caminho_completo, arcname=os.path.basename(caminho_completo))
    return os.path.exists(nome_zip)

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

def buscar_emails_processar():
    props = ler_properties(CONFIG_FILE)
    email_user = props.get('email')
    email_pass = props.get('senha')
    intervalo = int(props.get('intervalo_minutos', '5'))

    imap = imaplib.IMAP4_SSL('imap.gmail.com')
    imap.login(email_user, email_pass)
    imap.select('inbox')
    
    status, mensagens = imap.search(None, 'UNSEEN')
    if status != 'OK':
        print("Erro ao buscar mensagens")
        return

    for num in mensagens[0].split():
        status, dados = imap.fetch(num, '(RFC822)')
        if status != 'OK':
            continue
        msg = email.message_from_bytes(dados[0][1])

        assunto_header = msg.get('Subject', '')
        assunto = decodifica_assunto(assunto_header)

        if '[Solicitação linx-webservices]' not in assunto:
            imap.store(num, '-FLAGS', '\\Seen')
            print(' - - - - - - - - - - - - - - - - - - - - - - -')
            print('Igniroando e-mail:', assunto)
            continue

        print(' - - - - - - - - - - - - - - - - - - - - - - -')
        print('Tratando e-mail:', assunto)
        # Extrair corpo texto
        corpo = ''
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    charset = part.get_content_charset() or 'utf-8'
                    corpo = part.get_payload(decode=True).decode(charset, errors='replace')
                    break
        else:
            charset = msg.get_content_charset() or 'utf-8'
            corpo = msg.get_payload(decode=True).decode(charset, errors='replace')

        pid, destino, loja, pdv, logs = extrair_info_corpo(corpo)
        print(f"Processando PID={pid} Destino={destino} Loja={loja} PDV={pdv} Logs={logs}")
        
        data_atual = datetime.now().strftime('%d%m%Y%H%M%S')
        
        nome_zip = f'LOG-{loja}-{pdv}-{data_atual}.zip'
        status_envio = 'Sucesso'
        erro_msg = ''

        try:
            # Busca o caminho base (IP ou diretório) do servidor referente ao PDV
            base_pdv = props.get(f'PDV_{pdv}', '')
            
            if not base_pdv:
                print(f"Aviso: base (IP/diretório) não encontrado para PDV {pdv}")

            lista_logs = [log.strip() for log in logs.split(',') if log.strip()]
            caminhos_arquivos = []

            for log_item in lista_logs:

                caminho_relativo = props.get(log_item)
                if not caminho_relativo:
                    print(f"Aviso: caminho relativo do log '{log_item}' não encontrado.")
                    continue
                

                if log_item.strip().upper() == 'MFDE':
                    # Formata loja com 4 dígitos (ex: 0045)
                    loja_formatada = loja.zfill(4)
                    # Formata PDV com 3 dígitos (ex: 458)
                    pdv_formatado = pdv.zfill(3)
                    # Obtém data atual no formato YYYYMMDD
                    data_atual = datetime.now().strftime('%Y%m%d')
                    # Monta o nome do arquivo MFDE sem extensão
                    nome_arquivo_mfde = f"MFDE{loja_formatada}{pdv_formatado}{data_atual}"
                    
                    # Busca o caminho base do MFDE no properties (ou usa caminho padrão)
                    caminho_base_mfde = props.get('MFDE', 'Logs')  # ajuste conforme seu ambiente
                    
                    # Monta caminho completo
                    if base_pdv:
                        caminho_absoluto = os.path.join(f"\\\\{base_pdv}\\C$", caminho_base_mfde, nome_arquivo_mfde)
                    else:
                        caminho_absoluto = os.path.join(caminho_base_mfde, nome_arquivo_mfde)
                elif log_item.strip().upper() == 'linx-webservices':
                    caminho_absoluto = props.get('linx-webservices')
                else:
                                    # Cria caminho absoluto concatenando IP do PDV e caminho do arquivo
                    if base_pdv:
                        # Ajuste a concatenação conforme seu ambiente, ex. UNC path para rede Windows
                        caminho_absoluto = os.path.join(f"\\\\{base_pdv}\\C$", caminho_relativo.strip(':\\'))
                    else:
                        caminho_absoluto = caminho_relativo  # fallback local sem base PDV


                if os.path.exists(caminho_absoluto):
                    caminhos_arquivos.append(caminho_absoluto)
                else:
                    print(f"Aviso: arquivo não encontrado: {caminho_absoluto}")
                
                print('Arquivo', log_item,'-',caminho_absoluto) 

            if not caminhos_arquivos:
                raise FileNotFoundError("Nenhum arquivo válido para compactar.")

            with zipfile.ZipFile(nome_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
                for arquivo in caminhos_arquivos:
                    zipf.write(arquivo, arcname=os.path.basename(arquivo))

            corpo_email = f"Envio dos logs solicitados.\nLoja: {loja}\nPDV: {pdv}\nPID: {pid} "
            assunto_email = f"[Logs][{loja}][{pdv}][{pid}]"
            enviar_email_com_anexo(email_user, email_pass, destino, assunto_email, corpo_email, nome_zip)
            print(f"Email enviado para {destino} com os logs {nome_zip}")
            
            imap.store(num, '+FLAGS', '\\Seen')

        except Exception as e:
            status_envio = 'Erro'
            erro_msg = str(e)
            print(f"Erro no envio: {erro_msg}")

        gravar_csv([pid, destino, loja, pdv, logs, nome_zip, datetime.now().isoformat(), status_envio, erro_msg])
        # imap.store(num, '+FLAGS', '\\Seen')

    imap.logout()

    # props = ler_properties(CONFIG_FILE)
    # email_user = props.get('email')
    # email_pass = props.get('senha')
    # intervalo = int(props.get('intervalo_minutos', '5'))
    # caminho_arquivo = props.get('caminho_arquivo')
    # nome_arquivo = props.get('nome_arquivo')

    # imap = imaplib.IMAP4_SSL('imap.gmail.com')
    # imap.login(email_user, email_pass)
    # imap.select('inbox')

    # status, mensagens = imap.search(None, 'UNSEEN')
    # if status != 'OK':
    #     print("Erro ao buscar mensagens")
    #     return

    # for num in mensagens[0].split():
    #     status, dados = imap.fetch(num, '(RFC822)')
    #     if status != 'OK':
    #         continue
    #     msg = email.message_from_bytes(dados[0][1])

    #     assunto_header = msg.get('Subject', '')
    #     assunto = decodifica_assunto(assunto_header)
    #     print('ASSUNTO DO EMAIL:',assunto)

    #     if '[Solicitação Log]' not in assunto:
    #         imap.store(num, '+FLAGS', '\\Seen')
    #         continue

    #     # Extrair corpo texto
    #     corpo = ''
    #     if msg.is_multipart():
    #         for part in msg.walk():
    #             if part.get_content_type() == 'text/plain':
    #                 charset = part.get_content_charset() or 'utf-8'
    #                 corpo = part.get_payload(decode=True).decode(charset, errors='replace')
    #                 break
    #     else:
    #         charset = msg.get_content_charset() or 'utf-8'
    #         corpo = msg.get_payload(decode=True).decode(charset, errors='replace')

    #     pid, destino, loja, pdv, logs = extrair_info_corpo(corpo)
    #     print(f"Processando PID={pid} Destino={destino} Loja={loja} PDV={pdv} Logs={logs}")

    #     nome_zip = f'{pid}.zip'
    #     status_envio = 'Sucesso'
    #     erro_msg = ''

    #     try:
    #         caminho_completo = os.path.join(caminho_arquivo, nome_arquivo)
    #         compactar_log(caminho_completo, nome_zip)
    #         corpo_email = f"Envio dos logs solicitados. PID: {pid}"
    #         assunto_email = f"[Logs][{pid}]"
    #         enviar_email_com_anexo(email_user, email_pass, destino, assunto_email, corpo_email, nome_zip)
    #         print(f"Email enviado para {destino} com os logs {nome_zip}")
    #     except Exception as e:
    #         status_envio = 'Erro'
    #         erro_msg = str(e)
    #         print(f"Erro no envio: {erro_msg}")

    #     # Grava resultado no CSV
    #     gravar_csv([pid, destino, loja, pdv, logs, nome_zip, datetime.now().isoformat(), status_envio, erro_msg])

    #     imap.store(num, '+FLAGS', '\\Seen')

    # imap.logout()

def main():
    props = ler_properties(CONFIG_FILE)
    intervalo = int(props.get('intervalo_minutos', 5))
    while True:
        try:
            buscar_emails_processar()
        except Exception as ex:
            print(f"Erro geral: {ex}")
        time.sleep(intervalo * 60)

if __name__ == '__main__':
    main()
