@echo off
echo ============================================================
echo  Painel Interativo Telbra-Ex - Iniciando Sistema
echo ============================================================
echo.

cd /d "%~dp0"

if not exist "main.py" (
    echo [ERRO] main.py nao encontrado.
    echo        Execute este .bat dentro da pasta do projeto.
    pause
    exit /b 1
)

if not exist "Videos\loop\" (
    mkdir "Videos\loop" 2>nul
)

echo Pressione Ctrl+C para encerrar.
echo.

python main.py

echo.
echo Sistema encerrado.
pause
