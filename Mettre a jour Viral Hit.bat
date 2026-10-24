@echo off
setlocal
cd /d "%~dp0"
title Mise a jour Viral Hit

git rev-parse --is-inside-work-tree >nul 2>nul
if not errorlevel 1 goto git_update

if not exist "update.py" call :bootstrap
if errorlevel 1 (
  echo La mise a jour initiale a echoue.
  pause
  exit /b 1
)
python update.py
if errorlevel 1 (
  echo La mise a jour a echoue. Verifiez votre connexion puis reessayez.
  pause
  exit /b 1
)
goto install_deps

:git_update
echo Mise a jour Git de Viral Hit...
git pull --ff-only origin arena/2e402653-viral-clip
if errorlevel 1 (
  echo La mise a jour Git a echoue.
  pause
  exit /b 1
)
goto install_deps

:bootstrap
echo Ce dossier est une archive ZIP. Initialisation automatique unique...
where curl.exe >nul 2>nul || (echo curl.exe est requis sur Windows 10/11.& exit /b 1)
set "WORK=%TEMP%\ViralHitBootstrap_%RANDOM%"
set "ZIP=%WORK%\source.zip"
set "UNPACK=%WORK%\unpacked"
echo Dossier local : %CD%
mkdir "%WORK%" >nul 2>nul
mkdir "%UNPACK%" >nul 2>nul
curl.exe -fL --retry 3 --output "%ZIP%" "https://codeload.github.com/lordhur/Viral-Clip/zip/refs/heads/arena/2e402653-viral-clip"
if errorlevel 1 (echo ECHEC: telechargement GitHub. Verifiez votre connexion.& exit /b 1)
python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "%ZIP%" "%UNPACK%"
if errorlevel 1 (echo ECHEC: extraction Python de l'archive ZIP.& exit /b 1)
for /d %%D in ("%UNPACK%\Viral-Clip-*") do call :copy_source "%%~fD"
if errorlevel 1 (echo ECHEC: copie des fichiers. Fermez Viral Hit et relancez ce script.& exit /b 1)
if not exist "update.py" (echo ECHEC: update.py absent apres extraction.& exit /b 1)
python update.py --record-current
if errorlevel 1 (echo ECHEC: impossible d'enregistrer la version GitHub courante.& exit /b 1)
rmdir /s /q "%WORK%" >nul 2>nul
echo Initialisation terminee. Les prochaines mises a jour ne telechargeront que les fichiers modifies.
exit /b 0

:copy_source
robocopy "%~1" "%~dp0" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD .git node_modules dist build coverage .venv __pycache__ output temp /XF "Mettre a jour Viral Hit.bat"
if errorlevel 8 exit /b 1
exit /b 0

:install_deps
echo Mise a jour des dependances...
call npm.cmd install
if errorlevel 1 (echo Echec de la mise a jour Node.js.& pause& exit /b 1)
python -m pip install -r requirements.txt
if errorlevel 1 (echo Echec de la mise a jour Python.& pause& exit /b 1)
echo Viral Hit est a jour. Lancez "Lancer Viral Hit.bat".
pause
endlocal
