@echo off
setlocal
cd /d "%~dp0"
title Viral Hit

echo ========================================
echo   VIRAL HIT - Demarrage de l'application
echo ========================================
echo.

where node >nul 2>nul
if errorlevel 1 (
  echo ERREUR : Node.js est introuvable. Installez Node.js puis relancez ce fichier.
  pause
  exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
  echo ERREUR : Python est introuvable dans le PATH.
  echo Si Python est installe dans C:\Python315, ajoutez-le au PATH Windows.
  pause
  exit /b 1
)

python -m uvicorn --version >nul 2>nul
if errorlevel 1 (
  echo Installation des dependances Python, une seule fois...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERREUR : Installation Python echouee. Verifiez requirements.txt et votre connexion.
    pause
    exit /b 1
  )
)

if not exist "%LOCALAPPDATA%\ViralHit\vision_install_checked.flag" (
  python -c "import cv2" >nul 2>nul
  if errorlevel 1 (
    echo Installation optionnelle de la detection de webcam...
    python -m pip install --user -r requirements-vision.txt
    if errorlevel 1 echo OpenCV non disponible pour cette version de Python; les clips seront recadres sans detection webcam.
  )
  if not exist "%LOCALAPPDATA%\ViralHit" mkdir "%LOCALAPPDATA%\ViralHit"
  echo checked>"%LOCALAPPDATA%\ViralHit\vision_install_checked.flag"
)

if not exist "node_modules\electron\dist\electron.exe" (
  echo Installation des dependances de l'application, une seule fois...
  call npm.cmd install
  if errorlevel 1 (
    echo ERREUR : Installation Node.js echouee.
    pause
    exit /b 1
  )
)

echo Lancement de Viral Hit...
call npm.cmd run electron:dev
if errorlevel 1 (
  echo.
  echo Viral Hit s'est arrete avec une erreur. Le detail est affiche ci-dessus.
  pause
)
endlocal
