param(
    [string]$CompilerDirectory = "",
    [string]$OutputDirectory = ""
)
$ErrorActionPreference = 'Stop'
$sourceDirectory = $PSScriptRoot
if (-not $CompilerDirectory) {
    $CompilerDirectory = Split-Path (Get-Command gcc.exe -ErrorAction Stop).Source
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $sourceDirectory 'bin'
}
$gcc = Join-Path $CompilerDirectory 'gcc.exe'
$dlltool = Join-Path $CompilerDirectory 'dlltool.exe'
if (-not (Test-Path -LiteralPath $gcc) -or -not (Test-Path -LiteralPath $dlltool)) {
    throw 'MinGW GCC and dlltool are required.'
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$buildDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('frytrader-focus-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildDirectory | Out-Null
$oldPath = $env:PATH
try {
    $env:PATH = "$CompilerDirectory;$oldPath"
    foreach ($systemLibrary in @('user32', 'kernel32')) {
        & $dlltool -m i386 -f '--32' -k -d (Join-Path $sourceDirectory "$systemLibrary.def") -l (Join-Path $buildDirectory "lib$systemLibrary.a")
        if ($LASTEXITCODE -ne 0) { throw "dlltool failed: $systemLibrary" }
    }
    $common = @('-m32', '-Os', '-nostdlib', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-Wl,--dynamicbase', '-Wl,--nxcompat', '-Wl,--no-insert-timestamp')
    & $gcc @common '-shared' '-Wl,--entry,_DllMain@12' '-Wl,--kill-at' (Join-Path $sourceDirectory 'focus_guard.c') (Join-Path $buildDirectory 'libuser32.a') -o (Join-Path $OutputDirectory 'focus_guard32.dll')
    if ($LASTEXITCODE -ne 0) { throw 'Building focus_guard32.dll failed.' }
    & $gcc @common '-Wl,--entry,_guard_entry@0' '-Wl,--subsystem,windows' (Join-Path $sourceDirectory 'focus_host.c') (Join-Path $buildDirectory 'libuser32.a') (Join-Path $buildDirectory 'libkernel32.a') -o (Join-Path $OutputDirectory 'focus_host32.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Building focus_host32.exe failed.' }
    Get-FileHash -LiteralPath (Join-Path $OutputDirectory 'focus_guard32.dll'), (Join-Path $OutputDirectory 'focus_host32.exe') -Algorithm SHA256
} finally {
    $env:PATH = $oldPath
    # Leave the uniquely named build directory for compiler diagnostics.
    Write-Verbose "Import-library build files: $buildDirectory"
}
