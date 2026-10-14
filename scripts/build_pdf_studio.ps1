# Build the Windows app bundle of PDF Studio.
#
# From the repo root:
#   .\scripts\build_pdf_studio.ps1                  # build
#   .\scripts\build_pdf_studio.ps1 -Smoke           # build + headless self-test
#   .\scripts\build_pdf_studio.ps1 -Smoke -Install  # ...and install for this user
#   .\scripts\build_pdf_studio.ps1 -Smoke -Zip      # ...and create dist\PDFStudio-win64.zip
#
# Uses a dedicated uv virtual environment (build\venv) containing only PyQt6,
# PyMuPDF and PyInstaller at the versions pinned in
# src\pdf_studio\packaging\requirements-build.txt.
# Output: dist\PDFStudio\PDFStudio.exe

param([switch]$Smoke, [switch]$Install, [switch]$Zip)

# PyInstaller logs to stderr; with "Stop", Windows PowerShell 5.1 would treat
# that as a failure. Exit codes are checked explicitly instead.
$ErrorActionPreference = "Continue"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Assert-Ok($what) {
    if ($LASTEXITCODE -ne 0) { Write-Error "$what failed ($LASTEXITCODE)"; exit 1 }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}

$extra = @()
if ($Smoke) { $extra += "--smoke" }

$venv = Join-Path $Root "build\venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    uv venv $venv --python 3.12;                                    Assert-Ok "uv venv"
}
uv pip install --python $py -r src\pdf_studio\packaging\requirements-build.txt -q
Assert-Ok "uv pip install"
$env:PYTHONPATH = Join-Path $Root "src"
& $py -m pdf_studio.packaging.build_windows --work (Join-Path $Root "build\pyinstaller") @extra 2>&1 | ForEach-Object { "$_" }
Assert-Ok "Build"

$exe = Join-Path $Root "dist\PDFStudio\PDFStudio.exe"
Copy-Item (Join-Path $Root "LICENSE") (Join-Path $Root "dist\PDFStudio\LICENSE.txt")
Write-Host ""
Write-Host "Built: $exe"
if ($Zip) {
    $zipPath = Join-Path $Root "dist\PDFStudio-win64.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path (Join-Path $Root "dist\PDFStudio") -DestinationPath $zipPath
    Write-Host ("Zipped: {0} ({1:N0} MB)" -f $zipPath, ((Get-Item $zipPath).Length / 1MB))
}
if ($Install) {
    # per-user install: Programs folder + Desktop/Start-menu shortcuts + PDF association
    $p = Start-Process -FilePath $exe -ArgumentList "--install" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Write-Error "Install failed ($($p.ExitCode))"; exit 1 }
    Write-Host "Installed to $env:LOCALAPPDATA\Programs\PDF Studio (shortcut on the Desktop)."
} elseif (-not $Zip) {
    Write-Host "Install for this user:  `"$exe`" --install"
}
