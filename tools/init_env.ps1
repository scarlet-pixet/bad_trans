[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$projectDirectory=Split-Path $PSScriptRoot -Parent
$environmentPath=Join-Path $projectDirectory '.env'
function New-LocalSecret {
    $secretBytes=New-Object byte[] 32
    $generator=[Security.Cryptography.RandomNumberGenerator]::Create()
    try {$generator.GetBytes($secretBytes)} finally {$generator.Dispose()}
    return ([BitConverter]::ToString($secretBytes)).Replace('-','').ToLowerInvariant()
}
$settings=@()
if(Test-Path -LiteralPath $environmentPath){
    $settings=@([IO.File]::ReadAllLines($environmentPath))
} else {
    $settings=@('# Local development credentials. Never commit this file.')
}
$changed=$false
foreach($settingName in @('DB_PASSWORD','REDIS_PASSWORD','JWT_SECRET','SERVICE_TOKEN','PGADMIN_PASSWORD','REPORT_SIGNING_KEY')){
    $matching=@($settings | Where-Object {$_ -match ('^'+[regex]::Escape($settingName)+'=')})
    $currentValue=if($matching.Count){($matching[-1] -split '=',2)[1].Trim().Trim('"',"'")}else{''}
    if(-not $currentValue){
        $settings=@($settings | Where-Object {$_ -notmatch ('^'+[regex]::Escape($settingName)+'=')})
        $settings+=$settingName+'='+(New-LocalSecret)
        $changed=$true
    }
}
if($changed){[IO.File]::WriteAllLines($environmentPath,$settings,(New-Object Text.UTF8Encoding($false)))}
Write-Host 'Local bad_trans configuration ready; existing non-empty keys retained.'
