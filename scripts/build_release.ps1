param(
    [string]$Version = "0.1.1"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run: py -3.12 -m venv .venv"
}

Push-Location $projectRoot
try {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

    $existingBundle = Join-Path $projectRoot "dist\PDFSlim"
    if (Test-Path -LiteralPath $existingBundle) {
        Remove-Item -LiteralPath $existingBundle -Recurse -Force
    }
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --contents-directory . `
        --noupx `
        --windowed `
        --name PDFSlim `
        --paths src `
        --runtime-hook scripts\pyi_rth_qt_dll_path.py `
        scripts\pyinstaller_entry.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $packageName = "PDFSlim-v$Version-windows-x64"
    $packageDir = Join-Path $projectRoot "dist\$packageName"
    if (Test-Path -LiteralPath $packageDir) {
        Remove-Item -LiteralPath $packageDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $packageDir | Out-Null
    Copy-Item -Path "dist\PDFSlim\*" -Destination $packageDir -Recurse
    Copy-Item -LiteralPath "LICENSE" -Destination $packageDir
    Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination $packageDir
    Copy-Item -LiteralPath "README.md" -Destination $packageDir

    $sitePackages = Join-Path $projectRoot ".venv\Lib\site-packages"
    $thirdPartyDir = Join-Path $packageDir "THIRD_PARTY_LICENSES"
    New-Item -ItemType Directory -Path $thirdPartyDir | Out-Null
    Copy-Item -LiteralPath "licenses\AGPL-3.0.txt" -Destination $thirdPartyDir
    Copy-Item -LiteralPath "licenses\LGPL-3.0.txt" -Destination $thirdPartyDir
    $licensePatterns = @{
        "PySide6" = "pyside6-*.dist-info\licenses"
        "PySide6-Addons" = "pyside6_addons-*.dist-info\licenses"
        "PySide6-Essentials" = "pyside6_essentials-*.dist-info\licenses"
        "shiboken6" = "shiboken6-*.dist-info\licenses"
        "PyMuPDF" = "pymupdf-*.dist-info\COPYING"
        "pikepdf" = "pikepdf-*.dist-info\licenses"
        "Pillow" = "pillow-*.dist-info\licenses"
        "lxml" = "lxml-*.dist-info\licenses"
        "packaging" = "packaging-*.dist-info\licenses"
        "PyInstaller" = "pyinstaller-*.dist-info\licenses"
        "PyInstaller-hooks" = "pyinstaller_hooks_contrib-*.dist-info\licenses"
    }
    foreach ($entry in $licensePatterns.GetEnumerator()) {
        $source = Get-ChildItem -Path (Join-Path $sitePackages $entry.Value) -ErrorAction Stop | Select-Object -First 1
        $destination = Join-Path $thirdPartyDir $entry.Key
        New-Item -ItemType Directory -Path $destination | Out-Null
        if ($source.PSIsContainer) {
            Copy-Item -Path "$($source.FullName)\*" -Destination $destination -Recurse
        } else {
            Copy-Item -LiteralPath $source.FullName -Destination $destination
        }
    }

    $archive = Join-Path $projectRoot "dist\$packageName.zip"
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    Compress-Archive -Path "$packageDir\*" -DestinationPath $archive -CompressionLevel Optimal

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    $checksum = Join-Path $projectRoot "dist\$packageName.zip.sha256"
    Set-Content -LiteralPath $checksum -Encoding ascii -NoNewline -Value "$hash  $packageName.zip"

    Write-Output $archive
    Write-Output $checksum
} finally {
    Pop-Location
}
