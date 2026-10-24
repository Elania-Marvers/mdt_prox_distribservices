@echo off
rem ============================================================================
rem  startnet.cmd : lance automatiquement par Windows PE au demarrage
rem  (scripts/build-winpe.sh le place dans  X:\Windows\System32\startnet.cmd ;
rem   l'ancien winpeshl.ini, qui lancait l'installeur Windows, est supprime).
rem
rem  Ce fichier est volontairement ECRIT SANS ACCENTS (ASCII) : cmd.exe lit
rem  mal l'UTF-8 tant que "chcp 65001" n'a pas ete execute.
rem
rem  Deroulement :
rem    1. wpeinit                    : demarre le reseau et les peripheriques
rem    2. drvload (optionnel)        : pilotes reseau supplementaires dans
rem                                    X:\deploy\drivers\ (dossier facultatif)
rem    3. console en UTF-8, grande   : chcp 65001 + mode con
rem    4. python.exe deploy_client.py : le programme de deploiement
rem    5. si le programme s'arrete avec une erreur : invite de commandes pour
rem       depanner. AUCUN redemarrage automatique ici (pas de boucle).
rem ============================================================================

title MDTPROX - Deploiement Windows

echo.
echo Initialisation de Windows PE (reseau et peripheriques)...
wpeinit

rem --- 2. Pilotes supplementaires (dossier facultatif) -----------------------
if exist X:\deploy\drivers\ echo Chargement des pilotes supplementaires...
if exist X:\deploy\drivers\ for /r X:\deploy\drivers %%i in (*.inf) do drvload "%%i"

rem --- 3. Console : UTF-8 et plus grande (sans importance si ca echoue) --------
chcp 65001 >nul
mode con cols=120 lines=40 >nul 2>&1

rem --- 4. Programme de deploiement ----------------------------------------------
set "PATH=X:\python;%PATH%"

if not exist X:\python\python.exe goto :pas_de_python
if not exist X:\deploy\client\deploy_client.py goto :pas_de_client

cd /d X:\deploy\client
python.exe deploy_client.py
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto :fin_normale

rem --- 5. Echec : on laisse la main pour depanner -----------------------------
echo.
echo ============================================================================
echo  Le programme de deploiement s'est arrete avec une erreur (code %CODE%).
echo  Un invite de commandes est ouvert pour depanner. Pistes :
echo    ipconfig /all                       le PC a-t-il une adresse IP ?
echo    type X:\deploy\client\client.json   quelle adresse de serveur ?
echo    X:\python\python.exe deploy_client.py   relancer le programme
echo    wpeutil reboot                      redemarrer le PC
echo    wpeutil shutdown                    eteindre le PC
echo ============================================================================
echo.
cmd.exe
goto :fin

:pas_de_python
echo.
echo ERREUR : X:\python\python.exe est introuvable dans ce mini-systeme.
echo Le WIM doit etre refabrique avec scripts/build-winpe.sh.
echo.
cmd.exe
goto :fin

:pas_de_client
echo.
echo ERREUR : X:\deploy\client\deploy_client.py est introuvable dans ce mini-systeme.
echo Le WIM doit etre refabrique avec scripts/build-winpe.sh.
echo.
cmd.exe
goto :fin

:fin_normale
echo.
echo Le programme de deploiement est termine.
echo Pour redemarrer le PC : wpeutil reboot      Pour l'eteindre : wpeutil shutdown
echo.

:fin
rem Fin du script : WinPE laisse l'invite de commandes ouvert (cmd /k startnet.cmd).
