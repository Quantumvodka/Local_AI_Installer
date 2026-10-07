@echo off
title Local AI Installer
echo Starting Local AI Installer...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.ps1 | iex"
echo.
pause
