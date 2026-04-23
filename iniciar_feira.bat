@echo off
setlocal
echo ============================================================
echo  Painel Interativo Telbra-Ex - Modo Feira
echo ============================================================
echo.

cd /d "%~dp0"

if not exist "PainelInterativo.exe" (
    echo [ERRO] PainelInterativo.exe nao encontrado nesta pasta.
    echo        Execute este arquivo dentro da pasta final gerada em dist\PainelInterativo.
    echo.
    pause
    exit /b 1
)

echo Iniciando sistema...
echo Pressione Ctrl+C para encerrar.
echo.

start "" /wait "PainelInterativo.exe"

echo.
echo Sistema encerrado.
pause
