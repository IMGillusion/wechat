[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$cfg = "D:\program\xwechat_files\wxid_YOUR_WXID_<hash>\config"

function ShowFile($path) {
    Write-Output ("########## {0} ##########" -f $path)
    if (-not (Test-Path $path)) { Write-Output "  (missing)"; return }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    Write-Output ("  size={0}" -f $bytes.Length)
    # detect if mostly printable text
    $printable = 0
    $sample = [Math]::Min($bytes.Length, 4096)
    for ($i=0; $i -lt $sample; $i++) {
        $b = $bytes[$i]
        if (($b -ge 32 -and $b -le 126) -or $b -eq 10 -or $b -eq 9 -or $b -eq 13) { $printable++ }
    }
    $ratio = [Math]::Round($printable / $sample, 3)
    Write-Output ("  printable_ratio(sample4k)={0}" -f $ratio)
    if ($ratio -gt 0.85) {
        $txt = [System.Text.Encoding]::UTF8.GetString($bytes)
        # truncate for display
        if ($txt.Length -gt 4000) { $txt = $txt.Substring(0,4000) + "...[trunc]" }
        Write-Output "  --- TEXT ---"
        Write-Output $txt
    } else {
        # hex dump first 512 bytes
        $hexlen = [Math]::Min($bytes.Length, 512)
        $hex = ($bytes[0..($hexlen-1)] | ForEach-Object { $_.ToString("x2") }) -join " "
        Write-Output "  --- HEX(first512) ---"
        $chunk = 32
        for ($i=0; $i -lt $hexlen; $i += $chunk) {
            $n = [Math]::Min($chunk, $hexlen - $i)
            $line = ($bytes[$i..($i+$n-1)] | ForEach-Object { $_.ToString("x2") }) -join " "
            $asc = -join ($bytes[$i..($i+$n-1)] | ForEach-Object { if ($_ -ge 32 -and $_ -le 126) { [char]$_ } else { "." } })
            Write-Output ("  {0,x8}  {1,-{2}s}  {3}" -f $i, $line, ($n*3-1), $asc)
        }
    }
    Write-Output ""
}

ShowFile "$cfg\file_config_2026"
ShowFile "$cfg\file_config_2026v2"
ShowFile "$cfg\rs_config"
ShowFile "D:\program\xwechat_files\wxid_YOUR_WXID_<hash>\resource\config"
Write-Output "DONE_CONFIG"
