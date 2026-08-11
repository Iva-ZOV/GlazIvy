[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipInstall,
    [switch]$SkipPortableZip
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$requirements = Join-Path $projectRoot "requirements.txt"
$iconScript = Join-Path $projectRoot "scripts\generate_icon.py"
$iconFile = Join-Path $projectRoot "assets\app_icon.ico"
$specFile = Join-Path $projectRoot "GlazIvy.spec"
$distFolder = Join-Path $projectRoot "dist\GlazIvy"
$releaseFolder = Join-Path $projectRoot "release"
$portableZip = Join-Path $releaseFolder "GlazIvy-portable-1.0.0-win64.zip"

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python не найден: $Python"
}

Push-Location $projectRoot
try {
    if (-not $SkipInstall) {
        & $Python -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "Не удалось обновить pip." }
        & $Python -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости." }
    }

    & $Python $iconScript --output $iconFile
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать app_icon.ico." }

    & $Python -m PyInstaller --noconfirm --clean $specFile
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой." }

    Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination $distFolder -Force

    if (-not $SkipPortableZip) {
        New-Item -ItemType Directory -Path $releaseFolder -Force | Out-Null
        if (Test-Path -LiteralPath $portableZip) {
            Remove-Item -LiteralPath $portableZip -Force
        }
        Compress-Archive -Path (Join-Path $distFolder "*") -DestinationPath $portableZip -CompressionLevel Optimal
    }

    Write-Host "Готово: $distFolder" -ForegroundColor Green
    if (-not $SkipPortableZip) {
        Write-Host "Portable ZIP: $portableZip" -ForegroundColor Green
    }
}
finally {
    Pop-Location
}
