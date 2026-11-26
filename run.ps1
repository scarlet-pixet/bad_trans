[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Status','Logs','Test')][string]$Action='Start',
    [switch]$Build, [switch]$KeepTestStand
)
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    if($Action -ne 'Test'){& "$PSScriptRoot/tools/init_env.ps1"}
    function Invoke-Compose([string[]]$ComposeArguments) {
        & docker compose --project-name bad_trans @ComposeArguments
        if($LASTEXITCODE -ne 0){throw 'Docker Compose failed. Read the error above.'}
    }
    switch($Action){
        Start {
            $applicationNames=@('auth-service','transaction-service','analysis-service','case-service','gateway','frontend')
            $missingImage=$false
            foreach($applicationName in $applicationNames){
                $imageId=& docker image ls --quiet "bad_trans-${applicationName}:local"
                if(-not $imageId){$missingImage=$true}
            }
            if($Build -or $missingImage){Invoke-Compose @('build')}
            $arguments=@('up','-d','--wait','--wait-timeout','240')
            Invoke-Compose $arguments
            Write-Host 'Frontend http://127.0.0.1:18500 | GraphiQL http://127.0.0.1:18100/graphql'
        }
        Stop {Invoke-Compose @('stop')}
        Status {Invoke-Compose @('ps','--all')}
        Logs {Invoke-Compose @('logs','--tail','100')}
        Test {
            $pythonExecutable=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
            if(-not(Test-Path -LiteralPath $pythonExecutable)){throw 'Run tools/setup_python.ps1 first.'}
            $testArguments=@()
            if($KeepTestStand){$testArguments+= '--keep'}
            & $pythonExecutable tools/run_isolated_tests.py @testArguments
            if($LASTEXITCODE -ne 0){throw 'Isolated acceptance tests failed.'}
        }
    }
} finally {Pop-Location}
