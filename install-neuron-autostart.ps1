$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $projectRoot "server.py"
$taskName = "Neuron AI Server"

if (-not (Test-Path $serverPath)) {
    throw "File server.py tidak ditemukan di folder aplikasi."
}

$pythonPath = $null
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    $resolvedPath = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $resolvedPath) {
        $pythonPath = ($resolvedPath | Select-Object -Last 1).Trim()
    }
}

if (-not $pythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike "*\WindowsApps\*") {
        $resolvedPath = & $pythonCommand.Source -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolvedPath) {
            $pythonPath = ($resolvedPath | Select-Object -Last 1).Trim()
        }
    }
}

if (-not $pythonPath) {
    $installations = Get-ChildItem "HKCU:\Software\Python\PythonCore" -ErrorAction SilentlyContinue |
        Sort-Object { [version]$_.PSChildName } -Descending
    foreach ($installation in $installations) {
        $installPathKey = Join-Path $installation.PSPath "InstallPath"
        $installInfo = Get-ItemProperty -Path $installPathKey -ErrorAction SilentlyContinue
        $candidate = $installInfo.ExecutablePath
        if ($candidate -and (Test-Path $candidate)) {
            $pythonPath = $candidate
            break
        }
    }
}

if (-not $pythonPath -or -not (Test-Path $pythonPath)) {
    throw "Python 3 belum terdeteksi. Pastikan Python sudah terpasang, lalu jalankan lagi skrip ini."
}

$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) "pythonw.exe"
if (-not (Test-Path $pythonwPath)) {
    throw "pythonw.exe tidak ditemukan di folder instalasi Python. Pasang Python dari python.org dengan Python Launcher, lalu coba lagi."
}

$envPath = Join-Path $projectRoot ".env"
$exampleEnvPath = Join-Path $projectRoot ".env.example"
if (-not (Test-Path $envPath) -and (Test-Path $exampleEnvPath)) {
    Copy-Item $exampleEnvPath $envPath
}

$action = New-ScheduledTaskAction `
    -Execute $pythonwPath `
    -Argument ('"' + $serverPath + '"') `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Menjalankan server AI Neuron saat pengguna masuk Windows." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
$health = $null
$lastHealthError = $null
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
        if ($health.ok) {
            break
        }
    } catch {
        $lastHealthError = $_
    }
    Start-Sleep -Seconds 1
}

if ($health -and $health.ok) {
    Write-Output "Server Neuron berjalan dan akan otomatis aktif setiap login Windows."
    Write-Output "Alamat aplikasi: http://127.0.0.1:8000"
    if (-not $health.configured) {
        Write-Warning "Server aktif, tetapi model AI belum dikonfigurasi. Isi OPENROUTER_API_KEY di file $envPath, lalu mulai ulang tugas Neuron AI Server."
    }
} else {
    $healthErrorMessage = if ($lastHealthError) { $lastHealthError.Exception.Message } else { "health endpoint belum merespons." }
    Write-Warning "Tugas otomatis sudah dibuat, tetapi server belum merespons: $healthErrorMessage"
}
