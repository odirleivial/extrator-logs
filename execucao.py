# execucao.py
# Registro de execução das funcionalidades do BEC.
#
# Funcionalidades que já enviam e-mail ao Agent Extrator de Log (Solicitar Logs,
# Manutenção PDV, PinPad em modo e-mail) geram seu próprio registro a partir
# desse e-mail. As demais (Exportar Oracle, Requisição API, MDM, PinPad em modo
# direto/túnel) não passavam por nenhum agente — este módulo envia, para elas,
# um e-mail de registro contendo funcionalidade, PID e usuário do Windows.
#
# O envio é feito em thread separada: registrar a execução nunca deve atrasar
# nem derrubar a operação que o usuário pediu.
import random
import smtplib
import string
import threading
from datetime import datetime
from email.mime.text import MIMEText

from logger import logger, MAQUINA, USUARIO_WINDOWS

ASSUNTO_PREFIXO = '[Registro Execucao]'


def gerar_pid(tamanho=10):
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choice(caracteres) for _ in range(tamanho))


def _enviar_smtp(remetente, senha, destinatario, assunto, corpo):
    msg = MIMEText(corpo, 'plain', 'utf-8')
    msg['Subject'] = assunto
    msg['From'] = remetente
    msg['To'] = destinatario

    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(remetente, senha)
        server.sendmail(remetente, destinatario, msg.as_string())


def montar_corpo(funcionalidade, pid, detalhes=None):
    """Corpo do e-mail de registro — formato chave/valor, uma por linha."""
    linhas = [
        f"Funcionalidade: {funcionalidade}",
        f"PID: {pid}",
        f"Usuario: {USUARIO_WINDOWS}",
        f"Maquina: {MAQUINA}",
        f"DataHora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
    ]
    if detalhes:
        linhas.append("")
        for chave, valor in detalhes.items():
            linhas.append(f"{chave}: {valor}")
    return '\n'.join(linhas)


def registrar_execucao(props, funcionalidade, pid=None, detalhes=None):
    """Envia o e-mail de registro de execução da funcionalidade.

    Devolve o PID usado (gerado aqui quando não informado), para que a tela possa
    exibi-lo. Desligável com registro_execucao=false no config.properties.
    """
    pid = pid or gerar_pid()

    if str(props.get('registro_execucao', 'true')).strip().lower() != 'true':
        return pid

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')
    if not remetente or not senha:
        logger.warning(f"Registro de execução de '{funcionalidade}' não enviado: e-mail de envio não configurado")
        return pid

    assunto = f"{ASSUNTO_PREFIXO} - [{funcionalidade}] - [{pid}]"
    corpo = montar_corpo(funcionalidade, pid, detalhes)

    def _enviar():
        try:
            _enviar_smtp(remetente, senha, remetente, assunto, corpo)
            logger.info(f"Registro de execução enviado: {funcionalidade} PID={pid}")
        except Exception as e:
            # Falha no registro não invalida a operação já realizada pelo usuário
            logger.warning(f"Falha ao enviar registro de execução de '{funcionalidade}': {str(e)}")

    threading.Thread(target=_enviar, daemon=True).start()
    return pid
