@echo off
setlocal
echo ============================================================
echo  Painel Interativo Telbra-Ex - Empacotar para Feira
echo ============================================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    echo        Instale Python 3.9 ou superior para gerar o .exe.
    echo.
    pause
    exit /b 1
)

echo [1/4] Instalando dependencias do projeto...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias.
    echo.
    pause
    exit /b 1
)

echo.
echo [2/4] Instalando PyInstaller...
python -m pip install pyinstaller
if errorlevel 1 (
    echo [ERRO] Falha ao instalar PyInstaller.
    echo.
    pause
    exit /b 1
)

echo.
echo [3/4] Gerando executavel...
python -m PyInstaller --noconfirm --clean --onedir --name PainelInterativo --collect-all mediapipe --collect-all cv2 main.py
if errorlevel 1 (
    echo [ERRO] Falha ao gerar o executavel.
    echo.
    pause
    exit /b 1
)

echo.
echo [4/4] Copiando videos e inicializador da feira...
if not exist "dist\PainelInterativo\Videos" mkdir "dist\PainelInterativo\Videos"
xcopy /E /I /Y "Videos" "dist\PainelInterativo\Videos\" >nul
copy /Y "iniciar_feira.bat" "dist\PainelInterativo\iniciar_feira.bat" >nul

echo.
echo ============================================================
echo  Pacote pronto!
echo  Pasta final: dist\PainelInterativo
echo  No PC da feira, execute: iniciar_feira.bat
echo ============================================================
echo.
pause
