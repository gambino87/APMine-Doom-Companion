param(
    [Parameter(Mandatory = $true)][string] $SourceDir,
    [Parameter(Mandatory = $true)][string] $LibraryPrefix,
    [string] $BuildDir = "",
    [switch] $GameplayDiagnostics,
    [string] $BuildToolsRoot = "C:\BuildTools"
)

$ErrorActionPreference = "Stop"
$SourceDir = (Resolve-Path -LiteralPath $SourceDir).ProviderPath
$LibraryPrefix = (Resolve-Path -LiteralPath $LibraryPrefix).ProviderPath
if (-not $BuildDir) {
    $BuildDir = Join-Path $SourceDir "build-local"
}
$BuildDir = [System.IO.Path]::GetFullPath($BuildDir)
if (-not (Test-Path -LiteralPath (Join-Path $SourceDir "prepare-manifest.json")) -and
    -not (Test-Path -LiteralPath (Join-Path $SourceDir "SOURCE_SNAPSHOT.json"))) {
    throw "Use a separate prepared companion source copy, not the installed/original client."
}
foreach ($Relative in @("CMakeLists.txt", "src\CMakeLists.txt")) {
    $Content = Get-Content -LiteralPath (Join-Path $SourceDir $Relative) -Raw
    if ($Content -match "APDOOM_PACKAGE_SCRIPT|APDOOM_WEB_DOWNLOAD_DIR|package-apdoom-launcher") {
        throw "Remove historical platform publication hooks before building."
    }
}
$EmbedSource = Get-Content -LiteralPath (Join-Path $SourceDir "embed\CMakeLists.txt") -Raw
if ($EmbedSource -notmatch "#define NUM_EMBEDDED_FILES 0") {
    throw "Companion build must generate an empty embedded-file table."
}
$EmbeddedArchives = Get-ChildItem -LiteralPath (Join-Path $SourceDir "embed") -File |
    Where-Object { $_.Extension -in @(".zip", ".apworld") }
if ($EmbeddedArchives) {
    throw "Remove unreviewed archives from the companion embed directory before building."
}
foreach ($Resource in Get-ChildItem -LiteralPath (Join-Path $SourceDir "src") -Filter "*-res.rc.in" -File) {
    if ((Get-Content -LiteralPath $Resource.FullName -Raw) -match '(?m)^\s*\d+\s+ICON\s+') {
        throw "Remove unreviewed program Windows icons before building: $($Resource.Name)"
    }
}
foreach ($Name in @("doom", "heretic", "hexen", "strife", "setup")) {
    $Icon = Join-Path $SourceDir "src\$Name\${Name}_icon.c"
    $Content = Get-Content -LiteralPath $Icon -Raw
    if ($Content.Length -gt 512 -or $Content -notmatch 'icon_data\[\]\s*=\s*\{0\}') {
        throw "Companion source still contains an unreviewed generated icon array: $Icon"
    }
}

$DevShell = Join-Path $BuildToolsRoot "Common7\Tools\Launch-VsDevShell.ps1"
$CMake = Join-Path $BuildToolsRoot "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$Ninja = Join-Path $BuildToolsRoot "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
foreach ($Tool in @($DevShell, $CMake, $Ninja)) {
    if (-not (Test-Path -LiteralPath $Tool -PathType Leaf)) {
        throw "Missing local build tool: $Tool"
    }
}
& $DevShell -VsInstallationPath $BuildToolsRoot -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
& $CMake -S $SourceDir -B $BuildDir -G Ninja "-DCMAKE_MAKE_PROGRAM=$Ninja" `
    "-DCMAKE_BUILD_TYPE=Release" "-DCMAKE_PREFIX_PATH=$LibraryPrefix" `
    "-DAPMINE_GAMEPLAY_DIAGNOSTICS:BOOL=$([int]$GameplayDiagnostics.IsPresent)"
if ($LASTEXITCODE -ne 0) {
    throw "Companion CMake configure failed."
}
& $CMake --build $BuildDir --target archipelago-doom apdoom-launcher apdoom-setup --parallel 4
if ($LASTEXITCODE -ne 0) {
    throw "Companion local build failed."
}
$EmbeddedHeader = Get-Content -LiteralPath (Join-Path $BuildDir "embedded_files.h") -Raw
if ($EmbeddedHeader -match "EMBEDDED_FILE_BASEASSETS_ZIP" -or $EmbeddedHeader -notmatch "#define NUM_EMBEDDED_FILES 0") {
    throw "Built companion unexpectedly contains embedded assets."
}
Write-Host "Private companion build complete: $(Join-Path $BuildDir 'bin')"
Write-Host "No package was published or installed. Runtime assets and release verification remain separate."
