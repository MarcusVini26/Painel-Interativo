@echo off
echo ============================================================
echo  Painel Interativo Telbra-Ex - Instalacao de Dependencias
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    echo        Instale Python 3.9 ou superior:
    echo        https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] Python encontrado:
python --version
echo.

echo Instalando dependencias Python...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias.
    echo Verifique sua conexao de internet e tente novamente.
    pause
    exit /b 1
)
echo.

mpv --version >nul 2>&1
if errorlevel 1 (
    echo [AVISO] mpv nao encontrado no PATH.
    echo.
    echo Para instalar o mpv:
    echo   1. Acesse: https://mpv.io/installation/
    echo   2. Baixe a versao para Windows
    echo   3. Extraia em C:\mpv\
    echo   4. Adicione C:\mpv\ ao PATH do Windows
    echo.
) else (
    echo [OK] mpv encontrado.
)

echo ============================================================
echo  Instalacao concluida!
echo  Execute iniciar.bat para iniciar o sistema.
echo ============================================================

echo.
pause
