@echo off
setlocal
cd /d "%~dp0"

set "PYTHON="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
  python --version >nul 2>&1
  if not errorlevel 1 set "PYTHON=python"
)

if not defined PYTHON (
  for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -Command "$installs=Get-ChildItem 'HKCU:\Software\Python\PythonCore' -ErrorAction SilentlyContinue | Sort-Object { [version]$_.PSChildName } -Descending; foreach($install in $installs){$entry=Get-ItemProperty ($install.PSPath+'\InstallPath') -ErrorAction SilentlyContinue; if($entry.ExecutablePath -and (Test-Path $entry.ExecutablePath)){$entry.ExecutablePath; break}}"`) do set "PYTHON=%%P"
)

if not defined PYTHON (
  echo Python 3 belum terdeteksi. Instal Python 3, lalu jalankan file ini lagi.
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Isi .env dengan kunci OpenRouter BARU yang sudah dirotasi.
  echo Model chat dan soal sudah diatur di .env.example.
  start "" notepad ".env"
  pause
)

echo Membuka Neuron di http://127.0.0.1:8000
echo Biarkan jendela ini tetap terbuka selama menggunakan aplikasi.
"%PYTHON%" server.py
pause
