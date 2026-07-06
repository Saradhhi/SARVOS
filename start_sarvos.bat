@echo off
REM Launches SARVOS silently in the background (no console window), from
REM whatever directory this .bat file lives in -- so relative paths
REM (sarvos.db, sarvos_workspace, etc.) resolve correctly regardless of
REM where Windows invokes it from at login.
REM
REM SETUP: put a shortcut to THIS FILE in your Windows Startup folder so
REM SARVOS launches automatically every time you log in. See README.md's
REM "Auto-start at login" section for the exact steps.
cd /d "%~dp0"
start "" pythonw desktop.py
