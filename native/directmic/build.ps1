# Build ClipMic Direct Mic APO DLL and offline testhost using MinGW-w64 x86_64 g++
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

$gxx = Get-Command g++ -ErrorAction SilentlyContinue
if (-not $gxx) {
    $candidates = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.*\mingw64\bin\g++.exe",
        "C:\mingw64\bin\g++.exe",
        "C:\msys64\mingw64\bin\g++.exe",
        "C:\msys64\ucrt64\bin\g++.exe"
    )
    foreach ($pat in $candidates) {
        $hit = Get-ChildItem -Path $pat -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { $gxx = $hit.FullName; break }
    }
} else {
    $gxx = $gxx.Source
}

if (-not $gxx) {
    Write-Error "MinGW-w64 g++ not found. Install via: winget install BrechtSanders.WinLibs.POSIX.UCRT"
    exit 1
}

Write-Host "Using compiler: $gxx"
& $gxx -O2 -Wall -Wextra -shared -static -static-libgcc -static-libstdc++ `
    -o "$dir\clipmic_apo.dll" "$dir\clipmic_apo.cpp" `
    -lole32 -luuid -ladvapi32 "-Wl,--kill-at"

& $gxx -O2 -Wall -Wextra -static -static-libgcc -static-libstdc++ `
    -o "$dir\testhost.exe" "$dir\testhost.cpp" `
    -lole32 -luuid

Write-Host "Built: $dir\clipmic_apo.dll and $dir\testhost.exe"
