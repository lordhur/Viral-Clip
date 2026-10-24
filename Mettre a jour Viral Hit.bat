@echo off
setlocal
cd /d "%~dp0"
title Mise a jour Viral Hit

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo Ce dossier n'est pas une copie Git.
  echo Telechargez la version a jour une seule fois, ou clonez le depot pour utiliser les mises a jour en un clic.
  pause
  exit /b 1
)

echo Recherche des mises a jour de Viral Hit...
git pull --ff-only origin arena/2e402653-viral-clip
if errorlevel 1 (
  echo La mise a jour a echoue. Verifiez votre connexion et reessayez.
  pause
  exit /b 1
)

echo Mise a jour des dependances...
call npm.cmd install
if errorlevel 1 (
  echo Mise a jour des dependances Node.js echouee.
  pause
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Mise a jour des dependances Python echouee.
  pause
  exit /b 1
)

echo Viral Hit est a jour. Lancez "Lancer Viral Hit.bat" pour demarrer.
pause
endlocal
