import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_NIVEIS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}

def _base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _ler_config_log(base):
    """Lê apenas as chaves log.* de config.properties.

    Leitura própria (e não a de extrator_logs.py) porque o logger é
    inicializado no import, antes de qualquer outro módulo carregar config.
    """
    padrao = {'nivel': 'INFO', 'max_mb': 5, 'backups': 3, 'console': 'INFO'}
    caminho = os.path.join(base, 'properties', 'config.properties')
    if not os.path.exists(caminho):
        return padrao
    try:
        with open(caminho, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith('#') or '=' not in linha:
                    continue
                chave, valor = linha.split('=', 1)
                chave, valor = chave.strip(), valor.strip()
                if chave == 'log.level':
                    padrao['nivel'] = valor.upper()
                elif chave == 'log.max_size_mb':
                    padrao['max_mb'] = int(valor)
                elif chave == 'log.backup_count':
                    padrao['backups'] = int(valor)
                elif chave == 'log.console_level':
                    padrao['console'] = valor.upper()
    except Exception:
        pass
    return padrao

def configurar_logger(nome_arquivo='extrator_logs.log'):
    base = _base_dir()
    cfg = _ler_config_log(base)
    nivel = _NIVEIS.get(cfg['nivel'], logging.INFO)

    log_dir = os.path.join(base, 'log')
    os.makedirs(log_dir, exist_ok=True)
    caminho_log = os.path.join(log_dir, nome_arquivo)

    logger = logging.getLogger('ExtratrorLogs')
    logger.setLevel(nivel)

    if logger.handlers:
        logger.handlers.clear()

    formato = logging.Formatter(
        '[%(asctime)s] - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    handler_arquivo = RotatingFileHandler(
        caminho_log,
        maxBytes=cfg['max_mb'] * 1024 * 1024,
        backupCount=cfg['backups'],
        encoding='utf-8'
    )
    handler_arquivo.setLevel(nivel)
    handler_arquivo.setFormatter(formato)
    logger.addHandler(handler_arquivo)

    # Console só quando não estiver empacotado
    if not getattr(sys, 'frozen', False):
        handler_console = logging.StreamHandler()
        handler_console.setLevel(_NIVEIS.get(cfg['console'], logging.INFO))
        handler_console.setFormatter(formato)
        logger.addHandler(handler_console)

    return logger, caminho_log

logger, _LOG_PATH = configurar_logger()
