#!/bin/sh
# Double-click to run (first time: right-click -> Open, to get past Gatekeeper).
clear
echo "Starting Local AI Installer..."
curl -fsSL https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.sh | sh
echo
printf "Press Enter to close..."; read _
