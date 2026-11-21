@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-neuron-autostart.ps1"
if errorlevel 1 echo Autostart belum dipasang. Baca pesan di atas, lalu jalankan file ini lagi setelah memperbaiki masalahnya.
pause
