import logging
import os
import sys

def _base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def configurar_logger(nome_arquivo='extrator_logs.log'):
    base = _base_dir()
    log_dir = os.path.join(base, 'log')
    os.makedirs(log_dir, exist_ok=True)
    caminho_log = os.path.join(log_dir, nome_arquivo)

    logger = logging.getLogger('ExtratrorLogs')
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        logger.handlers.clear()

    formato = logging.Formatter(
        '[%(asctime)s] - %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    handler_arquivo = logging.FileHandler(caminho_log, encoding='utf-8')
    handler_arquivo.setLevel(logging.DEBUG)
    handler_arquivo.setFormatter(formato)
    logger.addHandler(handler_arquivo)

    # Console só quando não estiver empacotado
    if not getattr(sys, 'frozen', False):
        handler_console = logging.StreamHandler()
        handler_console.setLevel(logging.INFO)
        handler_console.setFormatter(formato)
        logger.addHandler(handler_console)

    return logger, caminho_log

logger, _LOG_PATH = configurar_logger()
