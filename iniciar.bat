@echo off
rem Inicia Code Capture sin ventana de consola (queda en la bandeja del sistema)
cd /d "%~dp0"
start "" pythonw code_capture.py
