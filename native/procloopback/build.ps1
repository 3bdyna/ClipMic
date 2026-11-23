# Build ClipMic Per-Process Audio Loopback DLL using MinGW-w64 x86_64 g++
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
& $gxx -O2 -std=c++17 -Wall -Wextra -shared -static -static-libgcc -static-libstdc++ `
    -o "$dir\clipmic_procloop.dll" "$dir\clipmic_procloop.cpp" `
    -lole32 -luuid "-Wl,--kill-at"

Write-Host "Built: $dir\clipmic_procloop.dll"
