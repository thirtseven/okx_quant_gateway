# Gate.io live market data fetcher (run on Windows side)
# Fetches real-time 4h candlesticks for all tracked pairs, outputs JSON.
param(
    [string]$Pair = "BTC_USDT",
    [int]$Limit = 200
)
$ErrorActionPreference = "Stop"
try {
    $c = Invoke-RestMethod -Uri "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=$Pair&interval=4h&limit=$Limit" -TimeoutSec 15
    $rows = @()
    foreach ($row in $c) {
        $rows += [PSCustomObject]@{
            t = [long]$row.value[0]
            o = [double]$row.value[1]
            h = [double]$row.value[2]
            l = [double]$row.value[3]
            c = [double]$row.value[4]
            v = [double]$row.value[5]
            closed = $row.value[7]
        }
    }
    $rows | ConvertTo-Json -Compress -Depth 4
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
    exit 1
}
