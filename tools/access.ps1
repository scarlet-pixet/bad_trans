[CmdletBinding()]
param(
    [ValidateSet('PgAdmin','Database','Application')][string]$Target='Database',
    [switch]$CopyPassword
)
$ErrorActionPreference='Stop'
$projectDirectory=Split-Path $PSScriptRoot -Parent
$settings=@{}
foreach($line in Get-Content -LiteralPath (Join-Path $projectDirectory '.env')){
    if($line -match '^([^#=]+)=(.*)$'){$settings[$matches[1].Trim()]=$matches[2].Trim()}
}
switch($Target){
    PgAdmin {
        $password=$settings['PGADMIN_PASSWORD']
        [pscustomobject]@{Address='http://127.0.0.1:18180';Email='admin@bad-trans.example'}
    }
    Database {
        $password=$settings['DB_PASSWORD']
        foreach($index in 0..3){
            $service=@('auth','transaction','analysis','case')[$index]
            [pscustomobject]@{Host='127.0.0.1';Port=(57741+$index);Database="bank_$service";User="bank_$service";PgAdminHost="$service-db";PgAdminPort=5432}
        }
    }
    Application {
        $password='BadTrans-demo-2026!'
        [pscustomobject]@{Address='http://127.0.0.1:18500';Users='admin, manager, analyst'}
    }
}
if(-not $password){throw 'Missing setting. Run tools/init_env.ps1 to create the initial configuration.'}
if($CopyPassword){Set-Clipboard -Value $password;Write-Host 'Password copied. Paste it into the selected login form.'}
else {Write-Host 'Use -CopyPassword to copy this password without printing it.'}
