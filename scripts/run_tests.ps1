[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot

try {
    $VenvWin = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $VenvPosix = Join-Path $RepoRoot ".venv/bin/python"

    if (Test-Path $VenvWin) {
        $PythonBin = $VenvWin
    }
    elseif (Test-Path $VenvPosix) {
        $PythonBin = $VenvPosix
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonBin = (Get-Command python).Source
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonBin = (Get-Command py).Source
    }
    else {
        throw "Could not find a Python interpreter (.venv, python, or py)."
    }

    Write-Host "Running pytest with: $PythonBin" -ForegroundColor Cyan
    & $PythonBin -m pytest -v -s @PytestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
