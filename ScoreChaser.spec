# -*- mode: python ; coding: utf-8 -*-
import customtkinter
import os

_ctk_datas = [
    (os.path.join(os.path.dirname(customtkinter.__file__), 'assets'),
     'customtkinter/assets'),
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('fonts/*.ttf', 'fonts'), ('icon.png', '.')] + _ctk_datas,
    hiddenimports=['PIL._tkinter_finder', 'customtkinter', 'darkdetect',
                   'selenium', 'selenium.webdriver',
                   'selenium.webdriver.chrome', 'selenium.webdriver.chrome.webdriver',
                   'selenium.webdriver.chrome.options', 'selenium.webdriver.chrome.service',
                   'selenium.webdriver.common', 'selenium.webdriver.common.options',
                   'selenium.webdriver.common.service', 'selenium.webdriver.common.driver_finder',
                   'selenium.webdriver.common.selenium_manager',
                   'selenium.webdriver.remote', 'selenium.webdriver.remote.webdriver',
                   'selenium.webdriver.remote.remote_connection',
                   'selenium.webdriver.remote.command', 'selenium.webdriver.remote.errorhandler',
                   'webdriver_manager', 'webdriver_manager.chrome',
                   'webdriver_manager.core', 'webdriver_manager.core.driver_cache',
                   'webdriver_manager.core.manager', 'webdriver_manager.core.download_manager',
                   'webdriver_manager.core.os_manager'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ScoreChaser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon='icon.ico',
)
