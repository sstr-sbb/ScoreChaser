# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# Find selenium-manager binaries to bundle them
_sm_binaries = []
try:
    import selenium.webdriver.common.selenium_manager as sm
    sm_dir = os.path.dirname(sm.__file__)
    for platform_dir in ['linux', 'windows', 'macos']:
        pdir = os.path.join(sm_dir, platform_dir)
        if os.path.isdir(pdir):
            for f in os.listdir(pdir):
                src = os.path.join(pdir, f)
                dest = os.path.join('selenium', 'webdriver', 'common', platform_dir)
                _sm_binaries.append((src, dest))
except ImportError:
    pass

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=_sm_binaries,
    datas=[('fonts/*.ttf', 'fonts'), ('icon.png', '.')],
    hiddenimports=['PIL._tkinter_finder', 'selenium', 'selenium.webdriver',
                   'selenium.webdriver.chrome', 'selenium.webdriver.chrome.options',
                   'selenium.webdriver.chrome.service',
                   'selenium.webdriver.common.selenium_manager'],
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
