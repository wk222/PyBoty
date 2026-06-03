$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$updated = 0

$rules = @(
    @('core.systems.runtime.projected_runtime_view',  'core.systems.context.projected_runtime_view'),
    @('core.systems.runtime._projected_view_merge',   'core.systems.context._projected_view_merge'),
    @('core.systems.runtime._projected_view_render',  'core.systems.context._projected_view_render'),
    @('core.systems.runtime.context_budget',          'core.systems.context.context_budget'),
    @('core.systems.runtime.context_hygiene_runtime', 'core.systems.context.context_hygiene_runtime'),
    @('core.systems.runtime.instruction_assembly',    'core.systems.context.instruction_assembly'),
    @('core.systems.runtime.prompts',                 'core.systems.context.prompts'),
    @('core.systems.runtime.private_state',           'core.systems.context.private_state'),
    @('core/systems/runtime/projected_runtime_view',  'core/systems/context/projected_runtime_view'),
    @('core/systems/runtime/prompts',                 'core/systems/context/prompts'),
    @('core/systems/runtime/private_state',           'core/systems/context/private_state'),
    @('core/systems/runtime/context_budget',          'core/systems/context/context_budget'),
    @('core/systems/runtime/context_hygiene_runtime', 'core/systems/context/context_hygiene_runtime'),
    @('core/systems/runtime/instruction_assembly',    'core/systems/context/instruction_assembly')
)

Get-ChildItem -Path . -Recurse -File -Include *.py,*.md,*.tex,*.json,*.yaml,*.yml,*.toml | Where-Object {
    $_.FullName -notmatch '\\\.git\\|\\__pycache__\\|\\\.venv\\|\\node_modules\\|\\\.pytest_cache\\|\\logs\\'
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
