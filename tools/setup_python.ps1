$ErrorActionPreference='Stop'
$projectDirectory=Split-Path $PSScriptRoot -Parent
Push-Location $projectDirectory
try {
    if(-not(Test-Path -LiteralPath '.venv/Scripts/python.exe')){
        & py -3.13 -m venv .venv
        if($LASTEXITCODE -ne 0){throw 'Install Python 3.13 with the Windows py launcher.'}
    }
    & ./.venv/Scripts/python.exe -m pip install -r requirements.txt
    if($LASTEXITCODE -ne 0){throw 'Dependency installation failed.'}
} finally {Pop-Location}
