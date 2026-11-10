param(
    [switch]$NoLaunch,
    [switch]$NoShortcuts,
    [string]$InstallDirectory = (
        Join-Path $env:LOCALAPPDATA "Programs\ChessHelper")
)

$ErrorActionPreference = "Stop"
$archiveName = "ChessHelper-Windows-x64.zip"
$bundleDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$archivePath = Join-Path $bundleDirectory $archiveName
$manifestPath = Join-Path $bundleDirectory "SHA256SUMS.txt"
$installParent = Split-Path -Parent $installDirectory
$stagingDirectory = Join-Path $env:TEMP (
    "ChessHelper-Install-" + [guid]::NewGuid().ToString("N"))
$backupDirectory = $null

function Test-BundledChecksum {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "SHA256SUMS.txt is missing from the installer bundle."
    }
    $manifestLine = Get-Content -LiteralPath $manifestPath | Where-Object {
        $_ -match "^([0-9a-fA-F]{64})\s+\*?$([regex]::Escape($archiveName))$"
    } | Select-Object -First 1
    if (-not $manifestLine) {
        throw "No checksum was provided for $archiveName."
    }
    $expected = ([regex]::Match(
        $manifestLine, "^[0-9a-fA-F]{64}")).Value.ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "The bundled Windows package failed checksum verification."
    }
}

function New-ChessHelperShortcut {
    param([string]$ShortcutPath, [string]$ExecutablePath)

    $parent = Split-Path -Parent $ShortcutPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $ExecutablePath
    $shortcut.WorkingDirectory = Split-Path -Parent $ExecutablePath
    $shortcut.Description = "ChessHelper"
    $shortcut.Save()
}

try {
    Write-Host "Installing ChessHelper for Windows..." -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw "$archiveName is missing. Extract the complete all-platform ZIP first."
    }
    Test-BundledChecksum

    New-Item -ItemType Directory -Force -Path $stagingDirectory | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingDirectory
    $stagedApplication = Join-Path $stagingDirectory "ChessHelper"
    $stagedExecutable = Join-Path $stagedApplication "ChessHelper.exe"
    if (-not (Test-Path -LiteralPath $stagedExecutable -PathType Leaf)) {
        throw "The bundled Windows application is incomplete."
    }

    Get-Process -Name "ChessHelper" -ErrorAction SilentlyContinue |
        Where-Object {
            try { $_.Path -like "$installDirectory\*" } catch { $false }
        } | Stop-Process -Force

    New-Item -ItemType Directory -Force -Path $installParent | Out-Null
    if (Test-Path -LiteralPath $installDirectory) {
        $backupDirectory = "$installDirectory.backup-$([guid]::NewGuid().ToString('N'))"
        Move-Item -LiteralPath $installDirectory -Destination $backupDirectory
    }

    try {
        Move-Item -LiteralPath $stagedApplication -Destination $installDirectory
    } catch {
        if ($backupDirectory -and (Test-Path -LiteralPath $backupDirectory)) {
            Move-Item -LiteralPath $backupDirectory -Destination $installDirectory
            $backupDirectory = $null
        }
        throw
    }

    $executable = Join-Path $installDirectory "ChessHelper.exe"
    if (-not $NoShortcuts) {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $programs = [Environment]::GetFolderPath("Programs")
        New-ChessHelperShortcut (
            Join-Path $desktop "ChessHelper.lnk") $executable
        New-ChessHelperShortcut (
            Join-Path $programs "ChessHelper\ChessHelper.lnk") $executable
    }

    if ($backupDirectory -and (Test-Path -LiteralPath $backupDirectory)) {
        Remove-Item -LiteralPath $backupDirectory -Recurse -Force
        $backupDirectory = $null
    }

    Write-Host "ChessHelper is installed." -ForegroundColor Green
    Write-Host "Automatic moves can be enabled with Ctrl+Alt+M."
    if (-not $NoLaunch) {
        Start-Process -FilePath $executable -WorkingDirectory $installDirectory
    }
} catch {
    Write-Error $_
    exit 1
} finally {
    if (Test-Path -LiteralPath $stagingDirectory) {
        Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
    }
}
