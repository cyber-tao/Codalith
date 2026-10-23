$ErrorActionPreference = 'Stop'
$codalithInstaller = Join-Path -Path $PSScriptRoot -ChildPath 'scripts/install_agents.py'
$codalithUv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue

if ($null -ne $codalithUv) {
    & $codalithUv.Source run --no-project --python '>=3.11' -- python -X utf8 $codalithInstaller @args
    exit $LASTEXITCODE
}

foreach ($codalithPythonName in @('python', 'python3', 'py')) {
    $codalithPython = Get-Command $codalithPythonName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $codalithPython) {
        continue
    }
    $codalithPrefix = @()
    if ($codalithPythonName -eq 'py') {
        $codalithPrefix = @('-3')
    }
    & $codalithPython.Source @codalithPrefix -c 'import sys; sys.exit(sys.version_info < (3, 11))' *> $null
    if ($LASTEXITCODE -eq 0) {
        & $codalithPython.Source @codalithPrefix -X utf8 $codalithInstaller @args
        exit $LASTEXITCODE
    }
}

[Console]::Error.WriteLine('Codalith setup requires uv or Python 3.11+. Install uv: https://docs.astral.sh/uv/getting-started/installation/')
exit 2
