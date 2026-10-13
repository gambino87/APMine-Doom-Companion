param(
    [Parameter(Mandatory = $true)][string] $Bundle,
    [Parameter(Mandatory = $true)][string] $BuildRoot,
    [Parameter(Mandatory = $true)][string] $PythonExecutable,
    [string] $BuildToolsRoot = "C:\BuildTools"
)

$ErrorActionPreference = "Stop"
$Bundle = (Resolve-Path -LiteralPath $Bundle).ProviderPath
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$Repository = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
foreach ($Root in @($Bundle, $Repository)) {
    if ($BuildRoot.StartsWith($Root + "\", [System.StringComparison]::OrdinalIgnoreCase) -or $BuildRoot -eq $Root) {
        throw "Dependency outputs must be separate from source inputs and the platform checkout."
    }
}
& $PythonExecutable -m tools.doom_companion.dependency_sources --verify $Bundle
if ($LASTEXITCODE -ne 0) { throw "Dependency source verification failed." }
$SnapshotPath = Join-Path $Bundle "DEPENDENCY_SOURCES.json"
$Hash = (Get-FileHash -LiteralPath $SnapshotPath -Algorithm SHA256).Hash
$Marker = Join-Path $BuildRoot "dependency-build.json"
if (Test-Path -LiteralPath $BuildRoot) {
    if (-not (Test-Path -LiteralPath $Marker)) { throw "Existing output directory has no owned build marker." }
    $Previous = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
    if ($Previous.bundle -ne $Bundle -or $Previous.snapshot_sha256 -ne $Hash) {
        throw "Existing output directory belongs to different source inputs."
    }
} else {
    New-Item -ItemType Directory -Path $BuildRoot | Out-Null
    @{ bundle = $Bundle; snapshot_sha256 = $Hash; release_ready = $false } |
        ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
}
$DevShell = Join-Path $BuildToolsRoot "Common7\Tools\Launch-VsDevShell.ps1"
$CMake = Join-Path $BuildToolsRoot "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$Ninja = Join-Path $BuildToolsRoot "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
foreach ($Tool in @($DevShell, $CMake, $Ninja)) {
    if (-not (Test-Path -LiteralPath $Tool -PathType Leaf)) { throw "Missing local build tool: $Tool" }
}
& $DevShell -VsInstallationPath $BuildToolsRoot -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
$Prefix = Join-Path $BuildRoot "installed"
foreach ($Name in @("zlib", "libogg", "libvorbis", "wavpack", "libpng", "libsamplerate", "sdl2", "sdl2-mixer")) {
    $Cache = Get-Content -LiteralPath (Join-Path $Bundle "metadata\$Name\CMakeCache.txt")
    $Options = @()
    foreach ($Line in $Cache) {
        if ($Line -match '^((?:SDL_|SDL2_|SDL2MIXER_|ZLIB_|PNG_|WAVPACK_|LIBSAMPLERATE_)[A-Z0-9_]+|BUILD_SHARED_LIBS|BUILD_TESTING|INSTALL_DOCS|INSTALL_PKG_CONFIG_MODULE):(BOOL|STRING)=(.*)$') {
            if ($Matches[1] -eq "ZLIB_CONF_WRITTEN") { continue }
            $Options += "-D$($Matches[1]):$($Matches[2])=$($Matches[3])"
        }
    }
    if ($Name -eq "zlib") { $Options += "-UZLIB_CONF_WRITTEN" }
    $Output = Join-Path $BuildRoot $Name
    Write-Host "Building $Name from the staged preferred source."
    & $CMake -S (Join-Path $Bundle "sources\$Name") -B $Output -G Ninja `
        "-DCMAKE_MAKE_PROGRAM=$Ninja" "-DCMAKE_BUILD_TYPE=Release" `
        "-DCMAKE_INSTALL_PREFIX=$Prefix" "-DCMAKE_PREFIX_PATH=$Prefix" `
        "-DCMAKE_FIND_ROOT_PATH=$Prefix" "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY" `
        "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY" "-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY" `
        "-DCMAKE_FIND_USE_CMAKE_ENVIRONMENT_PATH=OFF" "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF" `
        "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF" "-DFETCHCONTENT_FULLY_DISCONNECTED=ON" `
        "-DBUILD_SHARED_LIBS=ON" "-DBUILD_TESTING=OFF" @Options
    if ($LASTEXITCODE -ne 0) { throw "$Name configure failed." }
    & $CMake --build $Output --parallel 4
    if ($LASTEXITCODE -ne 0) { throw "$Name build failed." }
    & $CMake --install $Output
    if ($LASTEXITCODE -ne 0) { throw "$Name install failed." }
    if ($Name -eq "zlib") {
        # Preserve the recorded vcpkg port's shared-only consumer fixups.
        $Fixups = @(
            @{ path = "lib\cmake\zlib\ZLIBConfig.cmake"; before = '_ZLIB_supported_components "shared" "static"'; after = '_ZLIB_supported_components "shared"' },
            @{ path = "include\zconf.h"; before = 'ifdef ZLIB_DLL'; after = 'if 1' }
        )
        foreach ($Fixup in $Fixups) {
            $Path = Join-Path $Prefix $Fixup.path
            $Content = Get-Content -LiteralPath $Path -Raw
            if ($Content.Contains($Fixup.before)) {
                [System.IO.File]::WriteAllText($Path, $Content.Replace($Fixup.before, $Fixup.after))
            } elseif (-not $Content.Contains($Fixup.after)) {
                throw "Unexpected installed zlib consumer configuration: $Path"
            }
        }
    }
}
& $PythonExecutable -m tools.doom_companion.dependency_sources --verify $Bundle
if ($LASTEXITCODE -ne 0) { throw "Source inputs changed during dependency rebuild." }
Write-Host "Independent dependency rebuild complete: $Prefix"
Write-Host "No runtime was published or installed for players."
