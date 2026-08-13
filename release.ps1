<#
.SYNOPSIS
    Релиз «Глаза Ивы» одной командой: версия в трёх местах → сборка → тег →
    GitHub Release → копия в локальный архив со СВЕРКОЙ размера.

.DESCRIPTION
    Скрипт закрывает ручной чек-лист, который уже дважды сбоил (копии 1.3 и
    1.4 попадали в архив с опозданием). Любой невыполненный шаг — throw,
    молча пропустить ничего нельзя.

.EXAMPLE
    .\release.ps1 -Version 1.6.0 -NotesFile release-notes.md
    .\release.ps1 -Version 1.6.0 -DryRun        # всё, кроме публикации
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$NotesFile,
    [string]$Title,

    # Не трогает git/GitHub/архив: только версии и сборка.
    [switch]$DryRun,

    # Переиспользовать уже собранный zip (когда сборка прошла, а публикация — нет).
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$repo = "Iva-ZOV/GlazIvy"
$python = Join-Path $root ".venv\Scripts\python.exe"
$archive = "C:\ivasProjects\Глаз Ивы\GlazIvy-releases"
$zipName = "GlazIvy-portable-$Version-win64.zip"
$zipPath = Join-Path $root "release\$zipName"
$buildInfoPath = Join-Path $root "release\build-info-$Version.json"
$parts = $Version.Split('.')

function Write-Step([string]$text) {
    Write-Host "==> $text" -ForegroundColor Cyan
}

# BOM сохраняем ровно такой, каким он был: version_info.txt с BOM ломает
# PyInstaller, а .ps1 с кириллицей БЕЗ BOM ломает сам PowerShell 5.1
# (читает файл как ANSI). Поэтому не навязываем, а повторяем исходный.
function Test-HasBom([string]$path) {
    $head = New-Object byte[] 3
    $stream = [System.IO.File]::OpenRead($path)
    try { $read = $stream.Read($head, 0, 3) } finally { $stream.Dispose() }
    return ($read -eq 3 -and $head[0] -eq 0xEF -and $head[1] -eq 0xBB -and $head[2] -eq 0xBF)
}

# Отпечаток ИМЕННО кода приложения (не инструментов вроде release.ps1):
# по нему публикация понимает, из того ли дерева собран лежащий zip.
function Get-AppFingerprint() {
    $paths = @(
        "vhodnaya", "packaging", "assets", "scripts",
        "main.py", "GlazIvy.spec", "requirements.txt"
    )
    $tracked = git ls-tree -r HEAD -- $paths
    $uncommitted = git diff HEAD -- $paths
    $material = ($tracked -join "`n") + "`n---`n" + ($uncommitted -join "`n")
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($material)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLower()
    }
    finally {
        $sha.Dispose()
    }
}

function Update-File([string]$path, [string]$pattern, [string]$replacement) {
    $hadBom = Test-HasBom $path
    $original = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    # Шаблон обязан находиться: иначе версия молча осталась бы старой.
    if (-not [regex]::IsMatch($original, $pattern)) {
        throw "Шаблон версии не найден в ${path}: $pattern"
    }
    $updated = [regex]::Replace($original, $pattern, $replacement)
    if ($updated -eq $original) {
        # Повторный прогон (например после -DryRun): уже нужная версия.
        Write-Host "    уже актуально: $([System.IO.Path]::GetFileName($path))"
        return
    }
    [System.IO.File]::WriteAllText(
        $path,
        $updated,
        (New-Object System.Text.UTF8Encoding($hadBom))
    )
    Write-Host "    обновлён $([System.IO.Path]::GetFileName($path))"
}

Push-Location $root
try {
    if (-not (Test-Path -LiteralPath $python)) { throw "Нет venv: $python" }
    if (-not (Test-Path -LiteralPath $gh)) { throw "Нет gh: $gh" }
    if (-not (Test-Path -LiteralPath $archive)) { throw "Нет архива релизов: $archive" }

    # --- 0. Рабочее дерево обязано быть чистым: релиз собирается из того,
    #        что уже в main, иначе тег будет врать.
    if (-not $DryRun) {
        # Три файла версии меняет сам скрипт (в том числе на прошлом прогоне
        # с -DryRun), поэтому их правки ожидаемы и чистоту дерева не портят.
        $versionFiles = @(
            "vhodnaya/constants.py",
            "packaging/version_info.txt",
            "build.ps1"
        )
        $dirty = git status --porcelain | Where-Object {
            $_ -and ($versionFiles -notcontains $_.Substring(3).Trim())
        }
        if ($dirty) {
            throw "Рабочее дерево не чистое — закоммить или спрячь правки:`n$($dirty -join "`n")"
        }
        $existing = git tag --list "v$Version"
        if ($existing) { throw "Тег v$Version уже существует." }
    }

    # --- 1. Версия в ТРЁХ местах ---
    Write-Step "Версия $Version в трёх местах"
    Update-File (Join-Path $root "vhodnaya\constants.py") `
        '(?m)^APP_VERSION = "[^"]*"' "APP_VERSION = `"$Version`""

    $versionInfo = Join-Path $root "packaging\version_info.txt"
    Update-File $versionInfo 'filevers=\(\d+, \d+, \d+, \d+\)' `
        "filevers=($($parts[0]), $($parts[1]), $($parts[2]), 0)"
    Update-File $versionInfo 'prodvers=\(\d+, \d+, \d+, \d+\)' `
        "prodvers=($($parts[0]), $($parts[1]), $($parts[2]), 0)"
    Update-File $versionInfo "StringStruct\(u'FileVersion', u'[^']*'\)" `
        "StringStruct(u'FileVersion', u'$Version')"
    Update-File $versionInfo "StringStruct\(u'ProductVersion', u'[^']*'\)" `
        "StringStruct(u'ProductVersion', u'$Version')"

    Update-File (Join-Path $root "build.ps1") `
        'GlazIvy-portable-[\d.]+-win64\.zip' $zipName

    # --- 2. Сборка ---
    if (-not $SkipBuild) {
        Write-Step "Сборка portable"
        # Запущенный GlazIvy.exe держит cv2.pyd и рвёт очистку dist.
        Get-Process -Name "GlazIvy" -ErrorAction SilentlyContinue |
            Stop-Process -Force -ErrorAction SilentlyContinue

        # ВАЖНО: PyInstaller НЕ гоняем под ErrorActionPreference='Stop' —
        # в PS 5.1 его обычный вывод в stderr считается ошибкой.
        $ErrorActionPreference = "Continue"
        & $python (Join-Path $root "scripts\generate_icon.py") --output (Join-Path $root "assets\app_icon.ico")
        if ($LASTEXITCODE -ne 0) { throw "Не собралась иконка app_icon.ico." }

        & $python -m PyInstaller --noconfirm --clean (Join-Path $root "GlazIvy.spec")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой." }

        $dist = Join-Path $root "dist\GlazIvy"
        if (-not (Test-Path -LiteralPath $dist)) { throw "Нет папки сборки: $dist" }
        Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $dist -Force

        New-Item -ItemType Directory -Path (Join-Path $root "release") -Force | Out-Null
        if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
        Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zipPath -CompressionLevel Optimal

        # Запоминаем, ИЗ ЧЕГО собран этот zip: при публикации сверим.
        @{
            version = $Version
            head = (git rev-parse HEAD)
            app = (Get-AppFingerprint)
        } | ConvertTo-Json | Set-Content -LiteralPath $buildInfoPath -Encoding UTF8
    }

    $localSize = 0
    if (Test-Path -LiteralPath $zipPath) {
        $localSize = (Get-Item -LiteralPath $zipPath).Length
        Write-Host "    zip: $zipPath ($([math]::Round($localSize / 1MB, 1)) МБ)"
    } elseif ($DryRun) {
        Write-Host "    zip не собран (SkipBuild) — проверялась только подстановка версий"
    } else {
        throw "Нет архива сборки: $zipPath"
    }

    # --- 2а. Zip обязан быть собран из ТЕКУЩЕГО кода приложения ---
    # Иначе с -SkipBuild можно опубликовать вчерашнюю сборку под сегодняшним
    # тегом, и никто этого не заметит.
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $buildInfoPath)) {
            throw "Нет $([System.IO.Path]::GetFileName($buildInfoPath)) — zip собран неизвестно из чего. Пересобери без -SkipBuild."
        }
        $buildInfo = Get-Content -LiteralPath $buildInfoPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $currentApp = Get-AppFingerprint
        if ($buildInfo.app -ne $currentApp) {
            throw @"
Код приложения изменился после сборки zip — публиковать его нельзя.
Собран из: $($buildInfo.head) ($($buildInfo.app.Substring(0,12))…)
Сейчас:    $(git rev-parse HEAD) ($($currentApp.Substring(0,12))…)
Пересобери релиз без -SkipBuild.
"@
        }
        Write-Host "    сборка соответствует текущему коду приложения"
    }

    if ($DryRun) {
        Write-Step "DryRun: git/GitHub/архив пропущены"
        Write-Host "Осталось вручную: коммит версии, тег, релиз, копия в архив." -ForegroundColor Yellow
        return
    }

    # --- 3. Коммит версии и тег ---
    Write-Step "Коммит версии и тег v$Version"
    git add vhodnaya/constants.py packaging/version_info.txt build.ps1
    if ($LASTEXITCODE -ne 0) { throw "git add не прошёл." }
    git commit -m "Версия $Version"
    if ($LASTEXITCODE -ne 0) { throw "git commit не прошёл." }
    git tag "v$Version"
    if ($LASTEXITCODE -ne 0) { throw "git tag не прошёл." }
    git push --follow-tags
    if ($LASTEXITCODE -ne 0) { throw "git push не прошёл." }

    # --- 4. GitHub Release ---
    Write-Step "GitHub Release v$Version (заливка ~1,5 минуты)"
    if (-not $Title) { $Title = "Глаз Ивы $Version" }
    $ghArgs = @("release", "create", "v$Version", "--repo", $repo, "--title", $Title)
    if ($NotesFile) {
        $ghArgs += @("--notes-file", $NotesFile)
    } else {
        $ghArgs += @("--generate-notes")
    }
    $ghArgs += $zipPath
    & $gh @ghArgs
    if ($LASTEXITCODE -ne 0) { throw "gh release create не прошёл." }

    # --- 5. Сверка с ассетом и копия в локальный архив ---
    Write-Step "Сверка размера и копия в GlazIvy-releases"
    $remoteSize = & $gh release view "v$Version" --repo $repo --json assets --jq ".assets[0].size"
    if ($LASTEXITCODE -ne 0) { throw "Не удалось прочитать размер ассета на GitHub." }
    if ([int64]$remoteSize -ne [int64]$localSize) {
        throw "Размер ассета ($remoteSize) не совпал с локальным zip ($localSize) — заливка оборвалась."
    }

    $archived = Join-Path $archive $zipName
    Copy-Item -LiteralPath $zipPath -Destination $archived -Force
    $archivedSize = (Get-Item -LiteralPath $archived).Length
    if ([int64]$archivedSize -ne [int64]$localSize) {
        throw "Копия в архиве ($archivedSize) не совпала с оригиналом ($localSize)."
    }
    Write-Host "    архив: $archived — размер сверен с GitHub" -ForegroundColor Green

    Write-Step "Релиз $Version готов"
    Write-Host "Не забудь: CHANGELOG и ROADMAP в GlazIvy-docs, карточка на доске в Done, память Claude." -ForegroundColor Yellow
}
finally {
    Pop-Location
}
