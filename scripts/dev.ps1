param(
    [switch]$SkipFrontend,
    [switch]$NoAuth
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required: https://docs.astral.sh/uv/'
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw 'ffmpeg must be installed and available on PATH'
}

uv sync --extra dev
if (-not $SkipFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'Node.js 20 and npm are required to build the frontend'
    }
    npm ci
    npm run build
}

$ServerArgs = @('run', 'biliup', 'server', '--reload')
if ($NoAuth) {
    $ServerArgs += '--no-auth'
}
uv @ServerArgs
