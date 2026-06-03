# PyBoty Offline Deployment Configuration

This spec configures PyInstaller to package PyBoty into a standalone executable.

```python
# pyboty.spec
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Collect all dynamic imports that PyInstaller might miss
hidden_imports = []
hidden_imports += collect_submodules('core.assets.tools')
hidden_imports += collect_submodules('core.assets.agents')
hidden_imports += collect_submodules('core.systems.agents')
hidden_imports += collect_submodules('core.systems.apps')
hidden_imports += collect_submodules('core.assets.workflows')
hidden_imports += collect_submodules('core.systems.integration')
hidden_imports += collect_submodules('web.routers')
hidden_imports += collect_submodules('langchain_openai')
hidden_imports += collect_submodules('langgraph')

# Collect static files and templates
datas = []
datas += collect_data_files('web.static')
datas += collect_data_files('core.assets.workflows')

a = Analysis(
    ['service_mode.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='pyboty',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pyboty',
)
```

## Build Instructions
1. Install PyInstaller: `pip install pyinstaller`
2. Run build: `pyinstaller pyboty.spec`
3. Output will be in `dist/pyboty/`
