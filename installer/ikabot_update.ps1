$ErrorActionPreference = 'Stop'

function Log($msg) {
    Write-Host $msg
    Add-Content -Path 'LOG_FILE' -Value $msg -Encoding UTF8
}

try {
    Log 'Checking GitHub for the latest ikabot release...'
    $headers  = @{ 'User-Agent' = 'ikabot-manager'; 'Accept' = 'application/vnd.github+json' }
    $releases = Invoke-RestMethod -Uri 'https://api.github.com/repos/kurzonmorris/ikabot-modules/releases' -Headers $headers

    $asset = $null
    $ver   = $null
    foreach ($r in $releases) {
        foreach ($a in $r.assets) {
            if ($a.name -match '^ikabot-v[\d.]+-mod-v([\d.]+)\.zip$') {
                $asset = $a
                $ver   = $matches[1]
                break
            }
        }
        if ($asset) { break }
    }

    if (-not $asset) {
        throw 'No ikabot release asset found on GitHub (expected ikabot-v{x.x.x}-mod-v{x.x.x}.zip). Please check that the release has been published and try again.'
    }

    Log ('Latest version available: mod v' + $ver)
    Log 'Downloading ikabot...'
    $tmp = [IO.Path]::GetTempFileName() + '.zip'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -UseBasicParsing
    Log 'Download complete.'

    Log 'Clearing ikabot template folder...'
    $template = 'TEMPLATE_DIR'
    Get-ChildItem $template | Where-Object { $_.Name -notlike 'version*' } | ForEach-Object {
        if ($_.PSIsContainer) { Remove-Item $_.FullName -Recurse -Force }
        else                  { Remove-Item $_.FullName -Force }
    }

    Log 'Extracting new version into template...'
    Expand-Archive -Path $tmp -DestinationPath $template -Force
    Remove-Item $tmp

    $json = '{"version": "' + $ver + '"}'
    Set-Content -Path ($template + '\version.json')    -Value $json
    Set-Content -Path ($template + '\version_' + $ver) -Value ''

    Log 'Updating ikabot instance folders...'
    $ikabotDir = 'IKABOT_DIR'
    $folders = Get-ChildItem $ikabotDir -Directory |
               Where-Object { $_.Name -match '^ikariam \d+$' } |
               Sort-Object   { [int]($_.Name -replace '\D', '') }

    $count = 0
    foreach ($folder in $folders) {
        Log ('  ' + $folder.Name)
        Get-ChildItem $folder.FullName | ForEach-Object {
            if ($_.PSIsContainer) { Remove-Item $_.FullName -Recurse -Force }
            else                  { Remove-Item $_.FullName -Force }
        }
        Copy-Item ($template + '\ikabot.exe') ($folder.FullName + '\ikabot.exe') -Force
        Copy-Item ($template + '\_internal')  ($folder.FullName + '\_internal')  -Recurse -Force
        $count++
    }

    Log ''
    Log ('Update complete. Installed mod v' + $ver + '. ' + $count + ' instance folder(s) refreshed.')

} catch {
    Log ('ERROR: ' + $_.ToString())
    exit 1
}
