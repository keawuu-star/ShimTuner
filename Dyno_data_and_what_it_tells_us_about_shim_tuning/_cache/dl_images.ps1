# Parallel image downloader using curl.exe (batches of 6)
$base = "d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
$manifest = Get-Content "$base\_cache\images_manifest.json" -Raw | ConvertFrom-Json
$items = @()
foreach ($m in $manifest) {
  if ($m.src -match 'emoticon' -or $m.src -match '\.gif$') { continue }
  $name = ($m.alt -replace '[^\w.\-]', '_')
  if ($name -notmatch '\.(png|jpg|jpeg|webp)$') { $name += '.png' }
  $items += [pscustomobject]@{ idx = $m.idx; url = $m.src; out = "$base\images_raw\$name" }
}
Write-Output "items: $($items.Count)"
$batchSize = 6
$log = New-Object System.Collections.ArrayList
$done = 0; $ok = 0; $fail = 0
for ($i = 0; $i -lt $items.Count; $i += $batchSize) {
  $batch = $items[$i..([Math]::Min($i + $batchSize - 1, $items.Count - 1))]
  $jobs = @()
  foreach ($it in $batch) {
    if ((Test-Path $it.out) -and ((Get-Item $it.out).Length -gt 100)) { $ok++; $done++; [void]$log.Add("CACHED $($it.out)"); continue }
    $url = if ($it.url.StartsWith('//')) { 'https:' + $it.url } else { $it.url }
    $jobs += Start-Job -ScriptBlock { param($u, $o) $r = curl.exe -s -o $o --max-time 45 -w "%{http_code} %{size_download}" $u; $r } -ArgumentList $url, $it.out
  }
  foreach ($j in $jobs) {
    $res = Receive-Job $j -Wait
    Remove-Job $j
    $done++
    if ($res -match '^200 ') { $ok++ } else { $fail++ }
    [void]$log.Add("$res")
  }
  Write-Output "progress: $done / $($items.Count) ok=$ok fail=$fail"
}
$log | Out-File -Encoding utf8 "$base\_cache\_img_dl2_log.txt"
Write-Output "DONE ok=$ok fail=$fail total=$($items.Count)"
