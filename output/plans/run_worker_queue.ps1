param(
    [Parameter(Mandatory = $true)]
    [string]$WorkerId,

    [Parameter(Mandatory = $true)]
    [string]$QueueCsv,

    [string]$Root = 'C:\Users\osart\ATR_ProjectV2'
)

$ErrorActionPreference = 'Stop'

$pythonExe = 'C:\Users\osart\AppData\Local\Programs\Python\Python313\python.exe'
$mavenBin = Join-Path $Root 'tools\apache-maven-3.9.9\bin'
$workerOutput = Join-Path $Root ("output\worker_{0}" -f $WorkerId)
$workerSamples = Join-Path $workerOutput 'samples'
$workerRepo = Join-Path $Root ("compiledrepos\worker_{0}\42949039" -f $WorkerId)
$baseRepo = Join-Path $Root 'compiledrepos\42949039'
$classesPath = Join-Path $workerOutput 'classes.csv'
$projectInfoPath = Join-Path $workerOutput 'project_info.json'
$progressPath = Join-Path $workerOutput 'progress.csv'
$stdinToken = [Guid]::NewGuid().ToString('N')
$stdinPath = Join-Path $env:TEMP ("agone_{0}_{1}.in" -f $WorkerId, $stdinToken)

if (-not (Test-Path $QueueCsv)) {
    throw "Queue CSV not found: $QueueCsv"
}
if (-not (Test-Path $workerRepo)) {
    throw "Worker compiled repo not found: $workerRepo"
}
if (-not (Test-Path $baseRepo)) {
    throw "Base compiled repo not found: $baseRepo"
}

New-Item -ItemType Directory -Force -Path $workerOutput | Out-Null
New-Item -ItemType Directory -Force -Path $workerSamples | Out-Null

$rootProjectInfoPath = Join-Path $Root 'output\project_info.json'
$rootInfo = Get-Content -Path $rootProjectInfoPath -Raw | ConvertFrom-Json
$baseProjectInfo = $rootInfo.'42949039'
if ($null -eq $baseProjectInfo) {
    throw "Missing project_info entry for 42949039"
}

function Write-WorkerProjectInfo {
    param(
        [object]$BaseInfo,
        [string]$Path
    )
    # Force project-scope processing (no "modules" key) so output CSV paths do not
    # embed module separators that break file discovery on Windows.
    $entry = [ordered]@{
        java_version = $BaseInfo.java_version
        testng_version = $BaseInfo.testng_version
        junit_version = $BaseInfo.junit_version
        type = $BaseInfo.type
        version = $BaseInfo.version
    }
    (@{ '42949039' = $entry } | ConvertTo-Json -Depth 10) | Set-Content -Path $Path -Encoding ascii
}

function Invoke-RobocopyMirror {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DestinationDir
    )
    if (-not (Test-Path $SourceDir)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    robocopy $SourceDir $DestinationDir /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy mirror failed from '$SourceDir' to '$DestinationDir' (exit=$LASTEXITCODE)"
    }
}

function Resolve-ModulePath {
    param(
        [object]$Row,
        [string]$RelativeFocalPath
    )
    if ($Row.Module) {
        return (($Row.Module -replace '\\', '/') -replace '^/+', '' -replace '/+$', '')
    }
    if ($RelativeFocalPath -match '^([^/]+/[^/]+)/') {
        return $Matches[1]
    }
    return $null
}

$rows = @(Import-Csv -Path $QueueCsv)
$total = $rows.Count
if ($total -eq 0) {
    throw "Queue has no rows: $QueueCsv"
}

$env:PATH = "$mavenBin;$env:PATH"
@('2', 'Y', 'N', 'Y') | Set-Content -Path $stdinPath -Encoding ascii

Write-Host ("[{0}] Queue size: {1}" -f $WorkerId, $total)

for ($i = 0; $i -lt $total; $i++) {
    $row = $rows[$i]
    $index = $i + 1
    $sampleKey = if ($row.Sample_Key) { $row.Sample_Key } else { "42949039_{0}" -f $index }
    $sampleFile = if ($row.Sample_File) { $row.Sample_File } else { '-' }
    $sampleDir = Join-Path $workerSamples $sampleKey
    $doneMarker = Join-Path $sampleDir 'done.ok'

    if (Test-Path $doneMarker) {
        Write-Host ("[{0}] ({1}/{2}) {3} already complete; skipping" -f $WorkerId, $index, $total, $sampleKey)
        continue
    }

    New-Item -ItemType Directory -Force -Path $sampleDir | Out-Null

    Write-Host ("[{0}] ({1}/{2}) Running {3}" -f $WorkerId, $index, $total, $sampleKey)

    $relFocal = ($row.Focal_Path -replace '^repos/42949039/', '')
    $relTest = ($row.Test_Path -replace '^repos/42949039/', '')
    $moduleNorm = Resolve-ModulePath -Row $row -RelativeFocalPath $relFocal
    if ([string]::IsNullOrWhiteSpace($moduleNorm)) {
        throw "Unable to resolve module path for sample $sampleKey"
    }

    # Hard reset module source trees before each sample to avoid cross-sample contamination.
    $srcMainDir = Join-Path $baseRepo ($moduleNorm + '/src/main/java')
    $dstMainDir = Join-Path $workerRepo ($moduleNorm + '/src/main/java')
    $srcTestDir = Join-Path $baseRepo ($moduleNorm + '/src/test/java')
    $dstTestDir = Join-Path $workerRepo ($moduleNorm + '/src/test/java')
    Invoke-RobocopyMirror -SourceDir $srcMainDir -DestinationDir $dstMainDir
    Invoke-RobocopyMirror -SourceDir $srcTestDir -DestinationDir $dstTestDir

    # Remove compiled outputs so Maven cannot reuse stale mutated bytecode.
    $moduleTargetDir = Join-Path $workerRepo ($moduleNorm + '/target')
    if (Test-Path $moduleTargetDir) {
        Remove-Item -Path $moduleTargetDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $srcFocal = Join-Path $baseRepo $relFocal
    $srcTest = Join-Path $baseRepo $relTest
    $dstFocal = Join-Path $workerRepo $relFocal
    $dstTest = Join-Path $workerRepo $relTest

    if (Test-Path $srcFocal) { Copy-Item -Path $srcFocal -Destination $dstFocal -Force }
    if (Test-Path $srcTest) { Copy-Item -Path $srcTest -Destination $dstTest -Force }

    $basePom = Join-Path $baseRepo 'pom.xml'
    $workerPom = Join-Path $workerRepo 'pom.xml'
    if (Test-Path $basePom) { Copy-Item -Path $basePom -Destination $workerPom -Force }

    $modulePomRel = "$moduleNorm/pom.xml"
    $srcModulePom = Join-Path $baseRepo $modulePomRel
    $dstModulePom = Join-Path $workerRepo $modulePomRel
    if (Test-Path $srcModulePom) { Copy-Item -Path $srcModulePom -Destination $dstModulePom -Force }

    $projectOutDir = Join-Path $workerOutput '42949039'
    if (Test-Path $projectOutDir) {
        Remove-Item -Path $projectOutDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path $projectOutDir | Out-Null

    $singleRow = [pscustomobject]@{
        Project = $row.Project
        Focal_Class = $row.Focal_Class
        Test_Class = $row.Test_Class
        Focal_Path = $row.Focal_Path
        Test_Path = $row.Test_Path
        Module = $row.Module
        Focal_Method = $row.Focal_Method
        Test_Case = $row.Test_Case
        AST_Focal_Method = $row.AST_Focal_Method
        AST_Test_Method = $row.AST_Test_Method
    }
    $singleRow | Export-Csv -Path $classesPath -NoTypeInformation -Encoding ascii
    $singleRow | Export-Csv -Path (Join-Path $sampleDir 'sample_row.csv') -NoTypeInformation -Encoding ascii

    $outLog = Join-Path $sampleDir 'run.out.log'
    $errLog = Join-Path $sampleDir 'run.err.log'

    Write-WorkerProjectInfo -BaseInfo $baseProjectInfo -Path $projectInfoPath

    $env:AGONE_WORKER_ID = $WorkerId
    $proc = Start-Process -FilePath $pythonExe -ArgumentList 'Agone_Test\agone_test.py' -WorkingDirectory $Root -RedirectStandardInput $stdinPath -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden -PassThru -Wait
    $exitCode = $proc.ExitCode

    foreach ($name in @('42949039_Output.csv', 'maven_smoke_diagnostics.log', 'latest_failure_log.txt', 'codex_last_prompt.txt', 'codex_last_response.txt', 'codex_last_message.txt')) {
        $src = Join-Path $projectOutDir $name
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination (Join-Path $sampleDir $name) -Force
        }
    }

    $responseJavaFiles = @(Get-ChildItem -Path $projectOutDir -File -Filter 'response_codex-cli_*.java' -ErrorAction SilentlyContinue)
    foreach ($responseFile in $responseJavaFiles) {
        Copy-Item -Path $responseFile.FullName -Destination (Join-Path $sampleDir $responseFile.Name) -Force
    }

    $projectOutputCsvFiles = @(Get-ChildItem -Path $projectOutDir -Recurse -Filter '*_Output.csv' -File -ErrorAction SilentlyContinue)
    foreach ($csvFile in $projectOutputCsvFiles) {
        $relativeCsv = $csvFile.FullName.Substring($projectOutDir.Length).TrimStart('\')
        $safeCsvName = ($relativeCsv -replace '[\\/]', '__')
        Copy-Item -Path $csvFile.FullName -Destination (Join-Path $sampleDir $safeCsvName) -Force
    }

    $sampleOutputCsvCount = $projectOutputCsvFiles.Count
    $sampleOutputDataRows = 0
    foreach ($csvFile in $projectOutputCsvFiles) {
        $lineCount = (Get-Content -Path $csvFile.FullName -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
        if ($lineCount -gt 1) {
            $sampleOutputDataRows += ($lineCount - 1)
        }
    }

    $runOutText = if (Test-Path $outLog) { Get-Content -Path $outLog -Raw } else { '' }
    $runErrText = if (Test-Path $errLog) { Get-Content -Path $errLog -Raw } else { '' }
    $hasAstSkip = $runOutText -match 'no AST-verified samples remain after pre-filtering'
    $hasOutputDirError = $runErrText -match "Cannot save file into a non-existent directory"
    $hasCompletionBanner = $runOutText -match 'File processing completed!'

    if ($hasAstSkip) {
        $status = 'SKIPPED_AST'
    }
    elseif (($exitCode -ne 0) -or $hasOutputDirError) {
        $status = 'FAILED'
    }
    elseif ($sampleOutputDataRows -gt 0) {
        $status = 'OK'
    }
    elseif ($hasCompletionBanner -and ($sampleOutputCsvCount -gt 0)) {
        $status = 'FAILED_NO_ROWS'
    }
    else {
        $status = 'FAILED_NO_ARTIFACT'
    }
    $record = [pscustomobject]@{
        Timestamp = (Get-Date).ToString('s')
        Worker = $WorkerId
        Index = $index
        Total = $total
        Sample_Key = $sampleKey
        Sample_File = $sampleFile
        Status = $status
        Exit_Code = $exitCode
    }

    if (-not (Test-Path $progressPath)) {
        $record | Export-Csv -Path $progressPath -NoTypeInformation -Encoding ascii
    }
    else {
        $record | Export-Csv -Path $progressPath -NoTypeInformation -Encoding ascii -Append
    }

    if ($status -eq 'OK') {
        New-Item -ItemType File -Path $doneMarker -Force | Out-Null
    }

    Write-Host ("[{0}] ({1}/{2}) {3} -> {4} (exit={5}, outputs={6}, data_rows={7})" -f $WorkerId, $index, $total, $sampleKey, $status, $exitCode, $sampleOutputCsvCount, $sampleOutputDataRows)
}

if (Test-Path $stdinPath) {
    Remove-Item -Path $stdinPath -Force -ErrorAction SilentlyContinue
}

Write-Host ("[{0}] Queue completed." -f $WorkerId)
