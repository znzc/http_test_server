@echo off
REM ============================================================
REM  (OPTIONAL) Rebuild HttpFileServer.exe from source.
REM
REM  To RUN the server, just double-click:
REM      dist\HttpFileServer.exe
REM  You do NOT need to run this script at all.
REM
REM  This script only rebuilds the exe from server.py.
REM  ASCII-only on purpose so cmd.exe never garbles it.
REM ============================================================
cd /d "%~dp0"

set MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple

echo [1/3] Fixing setuptools (old PyInstaller needs pkg_resources)...
python3 -m pip install --quiet --disable-pip-version-check "setuptools<70" %MIRROR%
if errorlevel 1 (
    echo   pip install failed. Check your network, then retry.
)

echo [2/3] Building with PyInstaller (one-file, console)...
python -m PyInstaller --onefile --console --name HttpFileServer --clean --noconfirm server.py
if errorlevel 1 (
    echo.
    echo *** BUILD FAILED ***
    echo If it says "WinError 5" or "Access denied":
    echo     Close the running HttpFileServer.exe first, then retry.
    echo If it says "No module named pkg_resources":
    echo     python -m pip install "setuptools<70" %MIRROR%
    echo.
    pause
    exit /b 1
)

echo.
echo [3/3] Done.
echo ============================================================
echo  BUILD OK. Output:
echo    %~dp0dist\HttpFileServer.exe
echo  Double-click that exe to start the server on port 8080.
echo ============================================================
pause
