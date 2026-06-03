$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$updated = 0

$rules = @(
    @('core.systems.integration.channel_manager',     'core.systems.integration.channels.channel_manager'),
    @('core.systems.integration.channel_runtime',     'core.systems.integration.channels.channel_runtime'),
    @('core.systems.integration.wechat_channel',      'core.systems.integration.channels.wechat_channel'),
    @('core.systems.integration.wecom_channel',       'core.systems.integration.channels.wecom_channel'),
    @('core.systems.integration.feishu_channel',      'core.systems.integration.channels.feishu_channel'),
    @('core.systems.integration.dingtalk_channel',    'core.systems.integration.channels.dingtalk_channel'),
    @('core.systems.integration.terminal_channel',    'core.systems.integration.channels.terminal_channel'),
    @('core.systems.integration.wechat_claw_channel', 'core.systems.integration.channels.wechat_claw_channel'),
    @('core.systems.integration.mcp_hub',             'core.systems.integration.mcp.mcp_hub')
)

Get-ChildItem -Path . -Recurse -File -Include *.py | Where-Object {
    $_.FullName -notmatch '\\\.git\\|\\__pycache__\\|\\\.venv\\|\\node_modules\\|\\\.pytest_cache\\'
} | ForEach-Object {
    $path = $_.FullName
    $content = [System.IO.File]::ReadAllText($path)
    $orig = $content
    foreach ($rule in $rules) {
        $content = $content -replace [regex]::Escape($rule[0]), $rule[1]
    }
    if ($content -ne $orig) {
        [System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
        $updated++
        Write-Host "  updated: $($_.FullName.Replace($PWD.Path+'\',''))"
    }
}

Write-Host "Total files updated: $updated"
