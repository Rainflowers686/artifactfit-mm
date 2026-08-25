param(
    [Parameter(Mandatory = $true)][string]$PidFile,
    [Parameter(Mandatory = $true)][string]$StopFile,
    [Parameter(Mandatory = $true)][string]$TraceFile,
    [Parameter(Mandatory = $true)][string]$SummaryFile,
    [int]$IntervalMilliseconds = 100
)

$started = [System.Diagnostics.Stopwatch]::StartNew()
$rows = [System.Collections.Generic.List[object]]::new()
$peak = 0L
$observedPid = $null
$errorMessage = $null

while (-not (Test-Path -LiteralPath $StopFile)) {
    if (Test-Path -LiteralPath $PidFile) {
        try {
            $observedPid = [int](Get-Content -LiteralPath $PidFile -Raw)
        }
        catch {
            $observedPid = $null
        }
    }
    $used = 0L
    if ($null -ne $observedPid) {
        try {
            $samples = Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -MaxSamples 1 -ErrorAction Stop |
                Select-Object -ExpandProperty CounterSamples |
                Where-Object { $_.InstanceName -match "^pid_$($observedPid)_" }
            $used = [long](($samples | Measure-Object -Property CookedValue -Sum).Sum)
            if ($used -gt $peak) {
                $peak = $used
            }
        }
        catch {
            $errorMessage = $_.Exception.Message
        }
    }
    $rows.Add([pscustomobject]@{
        elapsed_s = [math]::Round($started.Elapsed.TotalSeconds, 6)
        pid = $observedPid
        gpu_bytes = $used
    })
    Start-Sleep -Milliseconds $IntervalMilliseconds
}

$traceParent = Split-Path -Parent $TraceFile
if ($traceParent) {
    New-Item -ItemType Directory -Path $traceParent -Force | Out-Null
}
$rows | Export-Csv -LiteralPath $TraceFile -NoTypeInformation -Encoding utf8
$summary = [ordered]@{
    independent_sampler = 'separate-process PowerShell Get-Counter GPU Process Memory Dedicated Usage'
    pid = $observedPid
    peak_gpu_bytes = $peak
    sample_count = $rows.Count
    error = $errorMessage
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $SummaryFile -Encoding utf8
