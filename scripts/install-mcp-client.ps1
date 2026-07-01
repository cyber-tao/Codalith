param(
    [string]$Url = $env:UE_CONTEXT_MCP_URL,
    [string]$Token = $env:UE_CONTEXT_MCP_TOKEN,
    [string]$Name = $env:UE_CONTEXT_MCP_NAME,
    [string]$Scope = $env:UE_CONTEXT_MCP_SCOPE
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Name)) {
    $Name = "ue-context"
}

if ([string]::IsNullOrWhiteSpace($Scope)) {
    $Scope = "user"
}

if ([string]::IsNullOrWhiteSpace($Url)) {
    Write-Error "Set UE_CONTEXT_MCP_URL or pass -Url, for example https://mcp.example.com/mcp."
}

if ($Url -notmatch '^https?://') {
    Write-Error "UE_CONTEXT_MCP_URL must start with http:// or https://: $Url"
}

if (@("local", "project", "user") -notcontains $Scope) {
    Write-Error "UE_CONTEXT_MCP_SCOPE must be local, project, or user: $Scope"
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Error "Claude Code CLI is required. Install and authenticate Claude Code, then rerun this script."
}

& claude mcp remove $Name --scope $Scope *> $null

$addArgs = @("mcp", "add", "--scope", $Scope, "--transport", "http", $Name, $Url)
if (-not [string]::IsNullOrWhiteSpace($Token)) {
    $addArgs += @("--header", "Authorization: Bearer $Token")
}

& claude @addArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "claude mcp add failed with exit code $LASTEXITCODE."
}

& claude mcp list
