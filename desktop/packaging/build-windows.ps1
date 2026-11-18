param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string] $Version = '0.4.0'
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$Build = Join-Path $Repo 'build\windows'
$Dist = Join-Path $Repo 'dist\windows'
$Installer = Join-Path $Dist "KeybowCodex-$Version-x64-Setup.exe"
$Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$ISCC = if ($Compiler) { $Compiler.Source } else {
    Join-Path ([Environment]::GetEnvironmentVariable('ProgramFiles(x86)')) 'Inno Setup 6\ISCC.exe'
}
if (-not (Test-Path -LiteralPath $ISCC)) {
    throw 'Install Inno Setup 6 before building the installer.'
}

Push-Location $Repo
try {
    $PyInstallerArgs = @(
        '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'KeybowCodex',
        '--workpath', $Build, '--specpath', $Build, '--distpath', $Dist, 'desktop\__main__.py'
    )
    python -m PyInstaller @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

    & $ISCC "/DVersion=$Version" "/DAppFiles=$Dist\KeybowCodex" "/O$Dist" "$PSScriptRoot\keybow.iss"
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }

    $Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $(Split-Path -Leaf $Installer)" | Set-Content -Encoding ascii "$Installer.sha256"
} finally {
    Pop-Location
}
