<#
.SYNOPSIS
    Crea l'eseguibile di MP3-to-LRC con PyInstaller.

.DESCRIPTION
    Usa il venv di build (default %USERPROFILE%\venvs\mp3-to-lrc), esegue i
    test e produce <DistDir>\MP3-to-LRC\MP3-to-LRC.exe. Sia la cartella di
    build intermedia sia quella finale stanno fuori dal progetto, per non
    sincronizzare oltre 1 GB di file.

.PARAMETER Venv
    Percorso del venv con le dipendenze (vedi requirements-build.txt).

.PARAMETER DistDir
    Dove creare la cartella MP3-to-LRC con l'eseguibile.

.PARAMETER Console
    Build con finestra console, utile per vedere errori e log.

.PARAMETER SkipTests
    Salta l'esecuzione dei test prima della build.

.PARAMETER SelfTest
    File audio con cui verificare l'eseguibile appena creato
    (Demucs + trascrizione locale con modello "tiny").

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Console -SelfTest C:\musica\canzone.mp3
#>
param(
    [string]$Venv = "$env:USERPROFILE\venvs\mp3-to-lrc",
    [string]$DistDir = "$env:USERPROFILE\builds",
    [switch]$Console,
    [switch]$SkipTests,
    [string]$SelfTest
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $Venv "Scripts\python.exe"
$workDir = Join-Path $env:LOCALAPPDATA "mp3-to-lrc-build"

if (-not (Test-Path $python)) {
    throw "Venv non trovato: $Venv (vedi requirements-build.txt per crearlo)"
}

Push-Location $root
try {
    if (-not $SkipTests) {
        Write-Host "==> Test" -ForegroundColor Cyan
        & $python -m pytest tests -q -p no:cacheprovider
        if ($LASTEXITCODE -ne 0) { throw "Test falliti: build annullata." }
    }

    Write-Host "==> PyInstaller (puo' richiedere diversi minuti)" -ForegroundColor Cyan
    $env:MP3LRC_CONSOLE = if ($Console) { "1" } else { "0" }
    & $python -m PyInstaller mp3_to_lrc.spec --noconfirm --clean `
        --workpath $workDir --distpath $DistDir
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallito." }

    $exe = Join-Path $DistDir "MP3-to-LRC\MP3-to-LRC.exe"
    $sizeMb = [math]::Round(((Get-ChildItem (Split-Path $exe) -Recurse | Measure-Object Length -Sum).Sum / 1MB))
    Write-Host "==> Creato $exe ($sizeMb MB in totale)" -ForegroundColor Green

    if ($SelfTest) {
        Write-Host "==> Autotest su $SelfTest" -ForegroundColor Cyan
        $proc = Start-Process -FilePath $exe -ArgumentList @("--selftest", "`"$SelfTest`"") -Wait -PassThru
        Get-Content "$SelfTest.selftest.log"
        if ($proc.ExitCode -ne 0) { throw "Autotest fallito (vedi log sopra)." }
        Write-Host "==> Autotest OK" -ForegroundColor Green
    }
}
finally {
    Pop-Location
}
