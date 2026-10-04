# Shared runtime for the console and windowed executables.
from pathlib import Path

root = Path(SPECPATH).parent
assets = root / "src/analog_fpv_compressor/gui/assets"
gui = root / "src/analog_fpv_compressor/gui"
a = Analysis([str(root / "packaging/entrypoint.py"), str(root / "packaging/gui_entrypoint.py")],
             pathex=[str(root / "src")],
             datas=[(str(assets), "analog_fpv_compressor/gui/assets"),
                    (str(gui / "translations"), "analog_fpv_compressor/gui/translations")],
             hiddenimports=[], excludes=["PySide6.QtTest"], noarchive=False)
# Qt hooks discover optional plugins installed in the development wheel.
# Ship only the desktop/image plugins we use and their LGPL library modules.
qt_modules = {"Core", "Gui", "Widgets", "Network", "OpenGL", "Svg"}
qt_plugins = {"qwindows.dll", "qoffscreen.dll", "qmodernwindowsstyle.dll",
              "qgif.dll", "qico.dll", "qjpeg.dll", "qsvg.dll", "qsvgicon.dll"}
def include_binary(entry):
    destination = entry[0].replace("\\", "/")
    name = Path(destination).name
    if "PySide6/plugins/" in destination:
        return name in qt_plugins
    if name.startswith("Qt6") and name.endswith(".dll"):
        return name[3:-4] in qt_modules
    if name.startswith(("QtQml", "QtQuick", "QtPdf", "QtVirtualKeyboard")) and name.endswith(".pyd"):
        return False
    return True
a.binaries = [entry for entry in a.binaries if include_binary(entry)]
pyz = PYZ(a.pure)
cli_scripts = [entry for entry in a.scripts if entry[0] != "gui_entrypoint"]
gui_scripts = [entry for entry in a.scripts if entry[0] != "entrypoint"]
cli = EXE(pyz, cli_scripts, [], exclude_binaries=True, name="fpv-compress",
          console=True, upx=False, icon=str(assets / "icon-big.ico"))
desktop = EXE(pyz, gui_scripts, [], exclude_binaries=True, name="fpv-compress-gui",
              console=False, upx=False, icon=str(assets / "icon-big.ico"))
bundle = COLLECT(cli, desktop, a.binaries, a.datas, name="fpv-compress", upx=False)
