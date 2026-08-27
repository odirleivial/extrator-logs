# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# cryptography é usado pelo oracledb (thin mode) para autenticação.
# Sem a coleta explícita de todos os submódulos, o PyInstaller pode deixar
# de fora módulos como cryptography.hazmat.primitives.kdf, causando o erro
# DPY-3016 ao rodar o .exe.
cryptography_imports = collect_submodules('cryptography')

a = Analysis(
    ['extrator_logs.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('templates',              'templates'),
        ('static',                 'static'),
        ('properties/agent.properties', 'properties'),
    ],
    hiddenimports=[
        # Flask e dependências
        'flask',
        'flask.json',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.routing',
        'werkzeug.exceptions',
        'werkzeug.utils',
        'werkzeug.middleware.proxy_fix',
        'click',
        'itsdangerous',
        # Oracle
        'oracledb',
        'oracledb.driver_mode',
        # cryptography (necessário pelo oracledb em thin mode) + dependências
        'cffi',
        '_cffi_backend',
    ] + cryptography_imports + [
        # Excel
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        # Pillow (renderização do Explain Plan em PNG/PDF)
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        # Requisições HTTP
        'requests',
        'requests.adapters',
        'requests.auth',
        'urllib3',
        'urllib3.util',
        'urllib3.util.retry',
        'certifi',
        'charset_normalizer',
        'idna',
        # E-mail
        'smtplib',
        'email',
        'email.mime.text',
        'email.mime.multipart',
        # Stdlib extras
        'csv',
        'zipfile',
        'logging',
        'logging.handlers',
        # pywebview (janela nativa Windows com Edge WebView2)
        'webview',
        'webview.platforms',
        'webview.platforms.winforms',
        'webview.event',
        'clr',
        'clr_loader',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'test',
        'xmlrpc',
        'pydoc',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ExtratordeLogs',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # execução em background sem janela de console
    icon='icon.ico',
)

# UPX corrompe a extensão binária Rust da cryptography (_rust*.pyd) e as
# DLLs do OpenSSL, causando o erro DPY-3016 no oracledb (thin mode). Pelo
# mesmo motivo, exclui as extensões binárias do Pillow (usadas para gerar a
# imagem/PDF do Explain Plan).
UPX_EXCLUDE = [
    '_rust.pyd',
    '_cffi_backend*.pyd',
    'libcrypto*.dll',
    'libssl*.dll',
    '_imaging.pyd',
    '_imagingft.pyd',
    '_imagingcms.pyd',
]

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=UPX_EXCLUDE,
    name='ExtratordeLogs',
)
