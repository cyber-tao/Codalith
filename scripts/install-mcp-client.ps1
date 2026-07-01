param(
    [string]$Client = $env:CODALITH_MCP_CLIENT,
    [string]$Url = $env:CODALITH_MCP_URL,
    [string]$Token = $env:CODALITH_MCP_TOKEN,
    [string]$Name = $env:CODALITH_MCP_NAME,
    [string]$Scope = $env:CODALITH_MCP_SCOPE,
    [string]$ConfigPath = $env:CODALITH_MCP_CONFIG_PATH
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Client)) {
    $Client = "all"
}
if ([string]::IsNullOrWhiteSpace($Name)) {
    $Name = "codalith"
}
if ([string]::IsNullOrWhiteSpace($Scope)) {
    $Scope = "user"
}
if ([string]::IsNullOrWhiteSpace($Url)) {
    Write-Error "Set CODALITH_MCP_URL or pass -Url, for example https://mcp.example.com/mcp."
}
if ($Url -notmatch '^https?://') {
    Write-Error "CODALITH_MCP_URL must start with http:// or https://: $Url"
}
if ($Name -notmatch '^[A-Za-z0-9_-]+$') {
    Write-Error "CODALITH_MCP_NAME may contain only letters, numbers, underscores, and hyphens."
}

function Normalize-Client([string]$Value) {
    switch ($Value) {
        "claude-code" { "claude" }
        "vs-code" { "vscode" }
        "copilot" { "vscode" }
        "github-copilot" { "vscode" }
        default { $Value }
    }
}

function Get-ServerConfig([switch]$ForVsCodeCli) {
    $server = [ordered]@{
        type = "http"
        url = $Url
    }
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $server.headers = [ordered]@{
            Authorization = "Bearer $Token"
        }
    }
    if ($ForVsCodeCli) {
        $server = [ordered]@{
            name = $Name
            type = "http"
            url = $Url
        }
        if (-not [string]::IsNullOrWhiteSpace($Token)) {
            $server.headers = [ordered]@{
                Authorization = "Bearer $Token"
            }
        }
    }
    return $server
}

function ConvertTo-Hashtable($Value) {
    if ($null -eq $Value) {
        return @{}
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $table = @{}
        foreach ($key in $Value.Keys) {
            $table[$key] = ConvertTo-Hashtable $Value[$key]
        }
        return $table
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = @()
        foreach ($item in $Value) {
            $items += ConvertTo-Hashtable $item
        }
        return $items
    }
    if ($Value -is [pscustomobject]) {
        $table = @{}
        foreach ($property in $Value.PSObject.Properties) {
            $table[$property.Name] = ConvertTo-Hashtable $property.Value
        }
        return $table
    }
    return $Value
}

function Write-JsonMcpConfig([string]$Path, [string]$RootKey) {
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $raw = ""
    if (Test-Path -LiteralPath $Path) {
        $raw = Get-Content -Raw -LiteralPath $Path
    }
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $data = ConvertTo-Hashtable ($raw | ConvertFrom-Json)
    } else {
        $data = @{}
    }
    if (-not $data.ContainsKey($RootKey) -or $null -eq $data[$RootKey]) {
        $data[$RootKey] = @{}
    }
    $data[$RootKey][$Name] = Get-ServerConfig
    $data | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Install-Claude {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        throw "Claude Code CLI is not installed."
    }
    $targetScope = $Scope
    if ($targetScope -eq "workspace") {
        $targetScope = "local"
    }
    if (@("user", "local", "project") -notcontains $targetScope) {
        throw "Claude Code scope must be user, local, or project."
    }
    & claude mcp remove $Name --scope $targetScope *> $null
    $addArgs = @("mcp", "add", "--scope", $targetScope, "--transport", "http", $Name, $Url)
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $addArgs += @("--header", "Authorization: Bearer $Token")
    }
    & claude @addArgs
    if ($LASTEXITCODE -ne 0) {
        throw "claude mcp add failed with exit code $LASTEXITCODE."
    }
}

function Install-Codex {
    $target = $ConfigPath
    switch ($Scope) {
        "user" {
            if ([string]::IsNullOrWhiteSpace($target)) {
                $target = Join-Path $HOME ".codex/config.toml"
            }
        }
        { $_ -in @("local", "project", "workspace") } {
            if ([string]::IsNullOrWhiteSpace($target)) {
                $target = ".codex/config.toml"
            }
        }
        default { throw "Codex scope must be user or project/workspace/local." }
    }
    $parent = Split-Path -Parent $target
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    if (Test-Path -LiteralPath $target) {
        $lines = Get-Content -LiteralPath $target
    } else {
        $lines = @()
    }
    $section = "[mcp_servers.$Name]"
    $filtered = New-Object System.Collections.Generic.List[string]
    $skip = $false
    foreach ($line in $lines) {
        if ($line -eq $section) {
            $skip = $true
            continue
        }
        if ($skip -and $line.StartsWith("[")) {
            $skip = $false
        }
        if (-not $skip) {
            $filtered.Add($line)
        }
    }
    $filtered.Add("")
    $filtered.Add($section)
    $filtered.Add("url = `"$Url`"")
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $filtered.Add("http_headers = { `"Authorization`" = `"Bearer $Token`" }")
    }
    $filtered | Set-Content -LiteralPath $target -Encoding utf8
}

function Install-VSCode {
    if ($Scope -in @("local", "project", "workspace")) {
        Write-JsonMcpConfig ".vscode/mcp.json" "servers"
        return
    }
    if (-not (Get-Command code -ErrorAction SilentlyContinue)) {
        throw "VS Code CLI 'code' is not installed."
    }
    $json = Get-ServerConfig -ForVsCodeCli | ConvertTo-Json -Compress -Depth 20
    & code --add-mcp $json
    if ($LASTEXITCODE -ne 0) {
        throw "code --add-mcp failed with exit code $LASTEXITCODE."
    }
}

function Install-Cursor {
    $target = $ConfigPath
    switch ($Scope) {
        "user" {
            if ([string]::IsNullOrWhiteSpace($target)) {
                $target = Join-Path $HOME ".cursor/mcp.json"
            }
        }
        { $_ -in @("local", "project", "workspace") } {
            if ([string]::IsNullOrWhiteSpace($target)) {
                $target = ".cursor/mcp.json"
            }
        }
        default { throw "Cursor scope must be user or project/workspace/local." }
    }
    Write-JsonMcpConfig $target "mcpServers"
}

function Invoke-Install([string]$TargetClient, [scriptblock]$Action, [string]$Label) {
    if ($Client -eq "all") {
        try {
            & $Action
            Write-Host "Configured $Label."
        } catch {
            Write-Warning "Skipped $Label. $($_.Exception.Message)"
        }
        return
    }
    & $Action
    Write-Host "Configured $Label."
}

$Client = Normalize-Client $Client

switch ($Client) {
    "claude" { Invoke-Install $Client { Install-Claude } "Claude Code MCP" }
    "codex" { Invoke-Install $Client { Install-Codex } "Codex MCP" }
    "vscode" { Invoke-Install $Client { Install-VSCode } "VS Code/Copilot MCP" }
    "cursor" { Invoke-Install $Client { Install-Cursor } "Cursor MCP" }
    "all" {
        Invoke-Install "claude" { Install-Claude } "Claude Code MCP"
        Invoke-Install "codex" { Install-Codex } "Codex MCP"
        Invoke-Install "vscode" { Install-VSCode } "VS Code/Copilot MCP"
        Invoke-Install "cursor" { Install-Cursor } "Cursor MCP"
    }
    default { throw "Unsupported client: $Client" }
}
