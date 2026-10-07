# Build + upload the station firmware (EVCS-01, ESP32-S3) over USB, then open the serial monitor.
#
#   powershell -ExecutionPolicy Bypass -File .\flash.ps1                build + upload + monitor
#   powershell -ExecutionPolicy Bypass -File .\flash.ps1 -Port COM7     pick the port yourself
#   powershell -ExecutionPolicy Bypass -File .\flash.ps1 -MonitorOnly   firmware already flashed: watch the log
param(
    [string] $Port = "",
    [switch] $MonitorOnly
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Project environment: conda env "iad" (arduino-cli, python with pyserial). A new
# `powershell -File` process re-runs the profile, which activates "base".
if ($env:CONDA_DEFAULT_ENV -ne "iad") {
    $iad = $null
    try {
        $iad = (conda env list --json | ConvertFrom-Json).envs | Where-Object { (Split-Path $_ -Leaf) -eq "iad" } | Select-Object -First 1
    } catch { }
    if (-not $iad) { Write-Host "conda env 'iad' not found (conda env list)." -ForegroundColor Red; exit 1 }
    $env:Path = "$iad;$iad\Scripts;$iad\Library\bin;$env:Path"
}
if (-not (Get-Command arduino-cli -ErrorAction SilentlyContinue)) {
    Write-Host "arduino-cli not found in the iad env: conda install -n iad -c conda-forge arduino-cli" -ForegroundColor Red; exit 1
}

# First USB serial port; Bluetooth ports have no USB VID. VID 303A = ESP32-S3 native USB port.
$found = (python -c @"
from serial.tools import list_ports
usb = [p for p in list_ports.comports() if p.vid is not None]
print(f'{usb[0].device} {usb[0].vid:04X}' if usb else '')
"@).Trim()
$foundPort, $foundVid = if ($found) { $found -split " " } else { "", "" }
if (-not $Port) { $Port = $foundPort }
if (-not $Port) {
    Write-Host "No ESP32 found on USB. Plug it in (data cable), or pass -Port COM7." -ForegroundColor Red; exit 1
}

if (-not $MonitorOnly) {
    if (-not (Test-Path config.h)) {
        Write-Host "config.h missing: copy config.example.h to config.h, then set WiFi." -ForegroundColor Red; exit 1
    }
    if (Select-String -Path config.h -SimpleMatch '"your-wifi"' -Quiet) {
        Write-Host "config.h still has the example WiFi: edit WIFI_SSID / WIFI_PASSWORD first." -ForegroundColor Red; exit 1
    }

    $libs = arduino-cli lib list | Out-String
    foreach ($lib in "PubSubClient", "ArduinoJson", "MFRC522", "Adafruit GFX Library", "Adafruit ILI9341",
                     "Adafruit INA219", "OneWire", "DallasTemperature", "QRCodeGenerator") {
        if ($libs -notmatch "(?m)^$([regex]::Escape($lib)) ") { arduino-cli lib install $lib }
    }

    # The native USB port needs CDC on, the UART port (CP210x / CH34x chip) CDC off.
    $fqbn = if ($Port -eq $foundPort -and $foundVid -eq "303A") { "esp32:esp32:esp32s3:CDCOnBoot=cdc" } else { "esp32:esp32:esp32s3:CDCOnBoot=default" }
    Write-Host "Station firmware, board $fqbn on $Port" -ForegroundColor Cyan

    arduino-cli compile --fqbn $fqbn --output-dir build .
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    arduino-cli upload --fqbn $fqbn --port $Port --input-dir build .
    if ($LASTEXITCODE) { Write-Host "Upload failed: hold BOOT while it starts, and close any other Serial Monitor." -ForegroundColor Red; exit $LASTEXITCODE }
}

Write-Host "Serial monitor on $Port (Ctrl+C to quit). Type a hex UID + Enter to fake a card tap." -ForegroundColor Cyan
# DTR/RTS off: on the UART port they drive EN / IO0 and would hold the chip in reset.
arduino-cli monitor --port $Port --config baudrate=115200,dtr=off,rts=off
