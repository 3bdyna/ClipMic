<#
.SYNOPSIS
    Reproducible 1-click ClipMic build, test, Inno Setup packaging, SHA-256 checksum,
    and optional GitHub Releases publish script (Windows PowerShell 5.1+ compatible).

.EXAMPLE
    .\scripts\build_release.ps1
.EXAMPLE
    .\scripts\build_release.ps1 -Publish -Notes "Initial Windows installer and auto-updater release."
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipBuild,
    [switch]$Publish,
    [switch]$Draft,
    [switch]$InstallLocal,
    [switch]$SkipLocalInstall,
    [string]$Notes = "",
    [string]$NotesFile = ""
)

$ErrorActionPreference = "Stop"

function Resolve-IsccPath {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    }
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            return $p
        }
    }
    return $null
}

function Assert-BundledFile {
    param(
        [string]$DistRoot,
        [string]$RelPath,
        [string]$Label
    )
    $direct = Join-Path $DistRoot $RelPath
    $internal = Join-Path (Join-Path $DistRoot "_internal") $RelPath
    if ((Test-Path $direct) -or (Test-Path $internal)) {
        return
    }
    throw "Required bundled artifact missing from dist\ClipMic: $Label ($RelPath)"
}

function Invoke-Native {
    <#
    Run a native CLI safely on Windows PowerShell 5.1+ without turning stderr into
    a terminating RemoteException under $ErrorActionPreference = 'Stop' and without
    capturing command output into a return value.
    #>
    param(
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [switch]$Quiet,
        [switch]$IgnoreExitCode,
        [string]$FailureMessage = ""
    )
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) {
            & $FilePath @ArgumentList *> $null
        } else {
            & $FilePath @ArgumentList
        }
        $script:LastNativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if (-not $IgnoreExitCode -and $script:LastNativeExitCode -ne 0) {
        if ($FailureMessage) {
            throw "$FailureMessage (exit code $script:LastNativeExitCode)."
        }
        throw "Command '$FilePath' failed (exit code $script:LastNativeExitCode)."
    }
}

function Stop-RunningClipMic {
    $targetPids = @()
    $exeProcs = Get-Process -Name "ClipMic" -ErrorAction SilentlyContinue
    if ($exeProcs) {
        $targetPids += ($exeProcs | Select-Object -ExpandProperty Id)
    }
    $pyProcs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match 'ClipMic[/\\]main\.py') }
    if ($pyProcs) {
        $targetPids += ($pyProcs | Select-Object -ExpandProperty ProcessId)
    }
    $targetPids = @($targetPids | Select-Object -Unique)
    if ($targetPids.Count -eq 0) {
        return
    }

    Write-Host "[InstallLocal] Stopping running ClipMic process(es): $($targetPids -join ', ')..." -ForegroundColor Cyan
    foreach ($procId in $targetPids) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p -and $p.MainWindowHandle -ne [IntPtr]::Zero) {
            $null = $p.CloseMainWindow()
        }
    }

    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        $alive = @()
        foreach ($procId in $targetPids) {
            if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
                $alive += $procId
            }
        }
        if ($alive.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }

    foreach ($procId in $targetPids) {
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 300
}

function Invoke-LocalInstall {
    param(
        [string]$InstallerPath,
        [string]$Version
    )
    Stop-RunningClipMic
    Write-Host "[InstallLocal] Running silent in-place installer..." -ForegroundColor Cyan
    $installerArgs = @(
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/RELAUNCH"
    )
    $proc = Start-Process -FilePath $InstallerPath -ArgumentList $installerArgs -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "Local installer failed with exit code $($proc.ExitCode)."
    }
    Write-Host "[InstallLocal] Local installed ClipMic updated to $Version and relaunched." -ForegroundColor Green
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $RepoRoot
try {
    # 1. Read single source-of-truth APP_VERSION from paths.py
    Write-Host "[1/7] Reading APP_VERSION from paths.py..." -ForegroundColor Cyan
    $versionOut = & python -c "import paths; print(paths.APP_VERSION)"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to execute Python to read paths.APP_VERSION (exit code $LASTEXITCODE)."
    }
    $version = ([string]$versionOut).Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+$') {
        throw "paths.APP_VERSION must be strict MAJOR.MINOR.PATCH semver; got '$version'."
    }
    Write-Host "      Version: $version" -ForegroundColor Green

    $tag = "v$version"
    if ($Publish) {
        Write-Host "[Publish Pre-Check] Validating clean git tree, gh auth, and tag $tag..." -ForegroundColor Cyan
        $dirty = & git status --porcelain
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to check git status."
        }
        if ($dirty) {
            throw "Refusing to publish from a dirty git working tree. Commit or stash changes first."
        }

        $ghCmd = Get-Command gh -ErrorAction SilentlyContinue
        if (-not $ghCmd) {
            throw "GitHub CLI (gh) is required for -Publish. Install it with: winget install -e --id GitHub.cli"
        }
        Invoke-Native -FilePath "gh" -ArgumentList @("auth", "status") -FailureMessage "GitHub CLI is not authenticated. Run 'gh auth login' first"

        $localTag = & git tag -l $tag
        if ($localTag) {
            throw "Git tag '$tag' already exists locally. Bump paths.APP_VERSION before publishing."
        }
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $remoteTag = & git ls-remote --tags origin "refs/tags/$tag" 2>$null
            $lsRemoteRc = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEap
        }
        if ($lsRemoteRc -eq 0 -and $remoteTag) {
            throw "Git tag '$tag' already exists on origin. Bump paths.APP_VERSION before publishing."
        }

        Invoke-Native -FilePath "gh" -ArgumentList @("release", "view", $tag) -Quiet -IgnoreExitCode
        if ($script:LastNativeExitCode -eq 0) {
            throw "GitHub release '$tag' already exists remotely. Bump paths.APP_VERSION before publishing."
        }
    }

    # 2. Run unit test suite unless -SkipTests
    if (-not $SkipTests) {
        Write-Host "[2/7] Running unit tests..." -ForegroundColor Cyan
        $prevPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = ".;tests"
            Invoke-Native -FilePath "python" -ArgumentList @("-m", "unittest", "test_clipmic", "-v") -FailureMessage "Unit tests failed. Aborting release"
        } finally {
            $env:PYTHONPATH = $prevPythonPath
        }
    } else {
        Write-Host "[2/7] Skipping unit tests (-SkipTests)." -ForegroundColor Yellow
    }

    # 3. Verify required native DLLs and static assets exist before packaging
    Write-Host "[3/7] Verifying native DLLs and assets..." -ForegroundColor Cyan
    $requiredInputs = @(
        "native\directmic\clipmic_apo.dll",
        "native\procloopback\clipmic_procloop.dll",
        "clipmic.ico",
        "LICENSE",
        "THIRD_PARTY_NOTICES.txt",
        "ClipMic.spec",
        "installer\ClipMic.iss"
    )
    foreach ($rel in $requiredInputs) {
        $full = Join-Path $RepoRoot $rel
        if (-not (Test-Path $full)) {
            throw "Required project file missing: $rel"
        }
    }

    # 4. Build PyInstaller one-folder bundle unless -SkipBuild
    $distAppDir = Join-Path $RepoRoot "dist\ClipMic"
    if (-not $SkipBuild) {
        Write-Host "[4/7] Building PyInstaller bundle (ClipMic.spec)..." -ForegroundColor Cyan
        Invoke-Native -FilePath "pyinstaller" -ArgumentList @("ClipMic.spec", "--noconfirm", "--clean") -FailureMessage "PyInstaller build failed"
    } else {
        Write-Host "[4/7] Skipping PyInstaller build (-SkipBuild)." -ForegroundColor Yellow
    }

    if (-not (Test-Path (Join-Path $distAppDir "ClipMic.exe"))) {
        throw "Built executable not found at dist\ClipMic\ClipMic.exe"
    }
    Assert-BundledFile -DistRoot $distAppDir -RelPath "native\directmic\clipmic_apo.dll" -Label "DirectMic APO DLL"
    Assert-BundledFile -DistRoot $distAppDir -RelPath "native\procloopback\clipmic_procloop.dll" -Label "ProcLoopback DLL"
    Assert-BundledFile -DistRoot $distAppDir -RelPath "ffmpeg.exe" -Label "FFmpeg binary"
    Assert-BundledFile -DistRoot $distAppDir -RelPath "_ssl.pyd" -Label "Python TLS/SSL extension"
    Assert-BundledFile -DistRoot $distAppDir -RelPath "clipmic.ico" -Label "App icon"
    Write-Host "      Verified ClipMic.exe, native DLLs, ffmpeg.exe, and _ssl.pyd." -ForegroundColor Green

    # 5. Locate Inno Setup 6 ISCC.exe
    Write-Host "[5/7] Locating Inno Setup 6 (ISCC.exe)..." -ForegroundColor Cyan
    $isccPath = Resolve-IsccPath
    if (-not $isccPath) {
        Write-Error (
            "Inno Setup 6 (ISCC.exe) was not found.`n" +
            "Install it with: winget install -e --id JRSoftware.InnoSetup"
        )
        exit 1
    }
    Write-Host "      Using: $isccPath" -ForegroundColor Green

    # 6. Compile Inno Setup installer
    Write-Host "[6/7] Compiling Inno Setup installer..." -ForegroundColor Cyan
    $releaseDir = Join-Path $RepoRoot "dist\release"
    if (-not (Test-Path $releaseDir)) {
        New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    }
    $issPath = Join-Path $RepoRoot "installer\ClipMic.iss"
    Invoke-Native -FilePath $isccPath -ArgumentList @("/DMyAppVersion=$version", $issPath) -FailureMessage "ISCC.exe failed"

    $installerName = "ClipMic-Setup-$version.exe"
    $installerPath = Join-Path $releaseDir $installerName
    if (-not (Test-Path $installerPath)) {
        throw "Expected installer output not found: $installerPath"
    }

    # 7. Compute SHA-256 and write UTF-8 (no BOM) SHA256SUMS.txt
    Write-Host "[7/7] Computing SHA-256 checksum..." -ForegroundColor Cyan
    $sha256 = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $sumsPath = Join-Path $releaseDir "SHA256SUMS.txt"
    $sumsLine = "$sha256  $installerName`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($sumsPath, $sumsLine, $utf8NoBom)

    $sizeBytes = (Get-Item $installerPath).Length
    $sizeMb = [Math]::Round($sizeBytes / 1MB, 2)

    Write-Host ""
    Write-Host "================ ClipMic Release Ready ================" -ForegroundColor Green
    Write-Host "Version   : $version"
    Write-Host "Installer : $installerPath ($sizeMb MB)"
    Write-Host "SHA-256   : $sha256"
    Write-Host "Checksums : $sumsPath"
    Write-Host "=======================================================" -ForegroundColor Green

    if ($Publish) {
        $ghArgs = @(
            "release", "create", $tag,
            $installerPath, $sumsPath,
            "--title", "ClipMic $tag"
        )
        if ($Draft) {
            $ghArgs += "--draft"
        }
        if ($NotesFile) {
            if (-not (Test-Path $NotesFile)) {
                throw "NotesFile not found: $NotesFile"
            }
            $ghArgs += @("--notes-file", $NotesFile)
        } elseif ($Notes) {
            $ghArgs += @("--notes", $Notes)
        } else {
            $ghArgs += "--generate-notes"
        }

        Write-Host ""
        Write-Host "[Publish] Creating GitHub release $tag..." -ForegroundColor Cyan
        Invoke-Native -FilePath "gh" -ArgumentList $ghArgs -FailureMessage "gh release create failed"
        Write-Host "[Publish] Release $tag published successfully." -ForegroundColor Green
    } elseif (-not $InstallLocal) {
        Write-Host ""
        Write-Host "Next step to publish to GitHub Releases:" -ForegroundColor Yellow
        Write-Host "  .\scripts\build_release.ps1 -SkipBuild -SkipTests -Publish"
    }

    if ($InstallLocal -or ($Publish -and -not $SkipLocalInstall)) {
        Write-Host ""
        Invoke-LocalInstall -InstallerPath $installerPath -Version $version
    }
} finally {
    Pop-Location
}
