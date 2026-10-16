$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localGcloud = Join-Path $projectRoot 'tmp/gcloud/google-cloud-sdk/bin/gcloud.cmd'
$gcloudCommand = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $localGcloud) {
    $gcloudPath = $localGcloud
} elseif ($gcloudCommand) {
    $gcloudPath = $gcloudCommand.Source
} else {
    throw 'Install the official Google Cloud CLI before running this script.'
}
$env:CLOUDSDK_CONFIG = Join-Path $projectRoot 'tmp/gcloud-config'
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $pythonPath) { $env:CLOUDSDK_PYTHON = $pythonPath }
& $gcloudPath auth application-default login --project=campusgdg-511817
if ($LASTEXITCODE -ne 0) { throw 'Google Cloud authorization did not complete.' }
$env:GOOGLE_APPLICATION_CREDENTIALS = Join-Path $env:CLOUDSDK_CONFIG 'application_default_credentials.json'
& $gcloudPath auth application-default set-quota-project campusgdg-511817
if ($LASTEXITCODE -ne 0) { throw 'The signed-in account cannot use this project as quota project.' }
Write-Output 'Application Default Credentials are ready in this PowerShell session. No key file was exported.'
