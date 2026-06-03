$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$rootDirs = @('core', 'tests', 'web', 'paper', 'plan.md', 'agent.py', 'api_server.py', 'scripts')
$updated = 0

Get-ChildItem -Path . -Recurse -File -Include *.py,*.md,*.tex,*.json,*.yaml,*.yml,*.toml | Where-Object {
    $_.FullName -notmatch '\\\.git\\|\\__pycache__\\|\\\.venv\\|\\node_modules\\|\\\.pytest_cache\\|\\logs\\'
} | ForEach-Object {
    $path = $_.FullName
    $content = [System.IO.File]::ReadAllText($path)
    $orig = $content
    $content = $content -replace 'core\.systems\.bus', 'core.systems.capability'
    $content = $content -replace 'core/systems/bus', 'core/systems/capability'
    $content = $content -replace 'core\\systems\\bus', 'core\systems\capability'
    if ($content -ne $orig) {
        [System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
        $updated++
        Write-Host "  updated: $($_.FullName.Replace($PWD.Path+'\',''))"
    }
}

Write-Host "Total files updated: $updated"
