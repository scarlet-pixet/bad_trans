[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Status','Logs','Test')][string]$Action='Start',
    [switch]$Build
)
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    & "$PSScriptRoot/tools/init_env.ps1"
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
            $previousFaultSetting=$env:ENABLE_TEST_FAULTS
            $previousSummaryDsn=$env:SUMMARY_TEST_DSN
            $previousCaseDsn=$env:CASE_EVENT_TEST_DSN
            $previousProductSettings=@{}
            foreach($settingName in @('PRODUCT_API','PRODUCT_COMPOSE','PRODUCT_TRANSACTION_DB_PORT','PROFILE_REFRESH_SECONDS','PROFILE_POLL_SECONDS')) {
                $previousProductSettings[$settingName]=[Environment]::GetEnvironmentVariable($settingName,'Process')
            }
            try {
                $testSettings=ConvertFrom-StringData (Get-Content -LiteralPath '.env' -Raw)
                $testDbPassword=[uri]::EscapeDataString($testSettings.DB_PASSWORD)
                $env:SUMMARY_TEST_DSN="postgresql://bank_transaction:${testDbPassword}@127.0.0.1:57742/bank_transaction"
                $env:CASE_EVENT_TEST_DSN="postgresql://bank_case:${testDbPassword}@127.0.0.1:57744/bank_case"
                $env:ENABLE_TEST_FAULTS='1'
                $env:PRODUCT_API='http://127.0.0.1:18100/graphql'
                $env:PRODUCT_COMPOSE=Join-Path $PSScriptRoot 'compose.yaml'
                $env:PRODUCT_TRANSACTION_DB_PORT='57742'
                $env:PROFILE_REFRESH_SECONDS='10'
                $env:PROFILE_POLL_SECONDS='1'
                Invoke-Compose @('up','-d','--wait','--wait-timeout','240')
                & $pythonExecutable tools/prove_flow.py
                if($LASTEXITCODE -ne 0){throw 'End-to-end proof failed.'}
                & $pythonExecutable -m pytest -q --junitxml=artifacts/verification/pytest.xml
                if($LASTEXITCODE -ne 0){throw 'Tests failed; see artifacts/verification/pytest.xml.'}
            } finally {
                foreach($settingName in $previousProductSettings.Keys) {
                    [Environment]::SetEnvironmentVariable($settingName,$previousProductSettings[$settingName],'Process')
                }
                if($null -eq $previousSummaryDsn){Remove-Item Env:SUMMARY_TEST_DSN -ErrorAction SilentlyContinue}
                else {$env:SUMMARY_TEST_DSN=$previousSummaryDsn}
                if($null -eq $previousCaseDsn){Remove-Item Env:CASE_EVENT_TEST_DSN -ErrorAction SilentlyContinue}
                else {$env:CASE_EVENT_TEST_DSN=$previousCaseDsn}
                if($null -eq $previousFaultSetting){Remove-Item Env:ENABLE_TEST_FAULTS -ErrorAction SilentlyContinue}
                else {$env:ENABLE_TEST_FAULTS=$previousFaultSetting}
                Invoke-Compose @('up','-d','--wait','--wait-timeout','240')
            }
            & $pythonExecutable tools/verify.py
            if($LASTEXITCODE -ne 0){throw 'Verification failed.'}
        }
    }
} finally {Pop-Location}
