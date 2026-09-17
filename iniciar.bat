@echo off
title Iniciando Agenda...

:: Garante que o pywebview e o flask estejam instalados
echo Verificando dependencias...
pip install flask pywebview >nul 2>&1

:: Inicia o aplicativo sem deixar a tela do terminal (CMD) aberta no fundo
echo Abrindo o aplicativo...
start "" pythonw app.py

exit