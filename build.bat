@echo off
setlocal
cd /d "%~dp0"
py -3.13 -m pip install --upgrade pyinstaller tkinterdnd2
if errorlevel 1 goto fail
py -3.13 -m PyInstaller --noconfirm --clean --onefile --noconsole --collect-all tkinterdnd2 --name VideoSplitTool --distpath dist --workpath build --specpath . split_tool.py
if errorlevel 1 goto fail
echo.
echo Built: dist\VideoSplitTool.exe
pause
exit /b 0
:fail
echo.
echo Build failed.
pause
exit /b 1
