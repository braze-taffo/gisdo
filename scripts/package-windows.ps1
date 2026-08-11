[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $repoRoot 'src-tauri\tauri.conf.json'
$config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
$version = [string]$config.version
$package = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repoRoot 'package.json') | ConvertFrom-Json
if ([string]$package.version -ne $version) {
    throw "package.json version $($package.version) does not match Tauri version $version."
}
$cargoManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repoRoot 'Cargo.toml')
$versionPattern = '(?m)^version\s*=\s*"' + [regex]::Escape($version) + '"\s*$'
if ($cargoManifest -notmatch $versionPattern) {
    throw "Cargo workspace version does not match Tauri version $version."
}
$releaseRoot = Join-Path $repoRoot 'target\release'
$distributionRoot = Join-Path $releaseRoot 'distribution'
$portableName = "GISdo $version Windows x64"
$portableRoot = Join-Path $distributionRoot $portableName

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

if (-not $SkipBuild) {
    if (Get-Process -Name 'gisdo' -ErrorAction SilentlyContinue) {
        throw 'GISdo is running. Close the application before creating a release package.'
    }
    Push-Location $repoRoot
    try {
        & npm run tauri build
        if ($LASTEXITCODE -ne 0) {
            throw "Tauri build failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

$setupSource = Join-Path $releaseRoot "bundle\nsis\GISdo_${version}_x64-setup.exe"
$msiSource = Join-Path $releaseRoot "bundle\msi\GISdo_${version}_x64_zh-CN.msi"
$requiredFiles = @(
    (Join-Path $releaseRoot 'gisdo.exe'),
    $setupSource,
    $msiSource,
    (Join-Path $repoRoot 'workers\arcmap\worker_server.py'),
    (Join-Path $repoRoot 'workers\common\worker_core.py'),
    (Join-Path $repoRoot 'workers\legacy\legacy_runner.py'),
    (Join-Path $repoRoot 'workers\pro\worker_server.py'),
    (Join-Path $repoRoot 'fixtures\arcgis_tool_inventory_510.json'),
    (Join-Path $repoRoot 'packaging\windows\README.txt'),
    (Join-Path $repoRoot 'LICENSE')
)

foreach ($path in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required packaging input is missing: $path"
    }
}

$resolvedDistributionRoot = [System.IO.Path]::GetFullPath($distributionRoot)
$expectedDistributionRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'target\release\distribution'))
if ($resolvedDistributionRoot -ne $expectedDistributionRoot) {
    throw "Refusing to clean unexpected distribution path: $resolvedDistributionRoot"
}

if (Test-Path -LiteralPath $distributionRoot) {
    Remove-Item -LiteralPath $distributionRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $portableRoot | Out-Null

Copy-Item -LiteralPath (Join-Path $releaseRoot 'gisdo.exe') -Destination $portableRoot
Copy-Item -LiteralPath (Join-Path $repoRoot 'LICENSE') -Destination $portableRoot
$readmeTemplate = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repoRoot 'packaging\windows\README.txt')
$readmeTemplate.Replace('{{VERSION}}', $version) | Set-Content -LiteralPath (Join-Path $portableRoot 'README.txt') -Encoding utf8

$resourceFiles = @(
    'workers\arcmap\worker_server.py',
    'workers\common\worker_core.py',
    'workers\legacy\legacy_runner.py',
    'workers\pro\worker_server.py',
    'fixtures\arcgis_tool_inventory_510.json',
    'skills\document-intake\SKILL.md',
    'skills\document-intake\agents\openai.yaml',
    'skills\document-intake\references\planning-contract.md'
)

foreach ($relativePath in $resourceFiles) {
    $source = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required application resource is missing: $source"
    }
    $destination = Join-Path $portableRoot $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

$portableZip = Join-Path $distributionRoot "GISdo-$version-Windows-x64-Portable.zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $portableRoot,
    $portableZip,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true
)

$setupDestination = Join-Path $distributionRoot "GISdo-$version-Windows-x64-Setup.exe"
$msiDestination = Join-Path $distributionRoot "GISdo-$version-Windows-x64-zh-CN.msi"
Copy-Item -LiteralPath $setupSource -Destination $setupDestination
Copy-Item -LiteralPath $msiSource -Destination $msiDestination

$deliverables = @($setupDestination, $msiDestination, $portableZip)
$checksumLines = foreach ($deliverable in $deliverables) {
    "{0}  {1}" -f (Get-Sha256Hex -Path $deliverable), (Split-Path -Leaf $deliverable)
}
$checksumPath = Join-Path $distributionRoot 'SHA256SUMS.txt'
$checksumLines | Set-Content -LiteralPath $checksumPath -Encoding utf8

$manifest = [ordered]@{
    product = [string]$config.productName
    version = $version
    platform = 'windows-x64'
    architecture = 'main executable plus external workers, skills, and fixtures'
    requires = @('Windows 10/11 x64', 'ArcGIS Pro/GeoScene Pro or ArcMap 10.x', 'Microsoft WebView2 Runtime')
    files = foreach ($deliverable in $deliverables) {
        $item = Get-Item -LiteralPath $deliverable
        [ordered]@{
            name = $item.Name
            bytes = $item.Length
            sha256 = (Get-Sha256Hex -Path $item.FullName)
        }
    }
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $distributionRoot 'release-manifest.json') -Encoding utf8

Write-Output "Windows distribution created at: $distributionRoot"
Get-ChildItem -LiteralPath $distributionRoot -File | Select-Object Name, Length
