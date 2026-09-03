[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$myWxid = 'wxid_YOUR_WXID'
$root = 'D:\program\xwechat_files'
$stage = 'C:\Users\Administrator\wxbuild\staging'
$knownFile = 'C:\Users\Administrator\wxbuild\known_files.txt'
$cleanHours = 24

New-Item -ItemType Directory -Path $stage -Force | Out-Null
$cut2 = (Get-Date).AddHours(-2)
Get-ChildItem $stage -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cut2 } | Remove-Item -Force -ErrorAction SilentlyContinue

$dir = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like ($myWxid + '_*') } | Select-Object -First 1
if (-not $dir) { Write-Output '{"error":"no_wxid_dir"}'; exit 0 }
$fileRoot = Join-Path $dir.FullName 'msg\file'
if (-not (Test-Path $fileRoot)) { Write-Output '{"error":"no_file_root","dir":"' + $dir.FullName + '"}'; exit 0 }

$known = @{}
if (Test-Path $knownFile) {
    Get-Content $knownFile -Encoding UTF8 | ForEach-Object { if ($_.Trim() -ne '') { $known[$_.Trim()] = $true } }
}

$files = @(Get-ChildItem $fileRoot -Recurse -File -ErrorAction SilentlyContinue)
$new = @()
$cleaned = @()
$epochBase = [DateTime]::new(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
$now = Get-Date
foreach ($f in $files) {
    $mt = [int64][Math]::Floor(($f.LastWriteTime.ToUniversalTime() - $epochBase).TotalSeconds)
    $sig = $f.Name + '|' + $f.Length + '|' + $mt
    if ($known.ContainsKey($sig)) {
        if (($now - $f.LastWriteTime).TotalHours -gt $cleanHours) {
            Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
            if ($cleaned -notcontains $f.Name) { $cleaned += $f.Name }
        }
        continue
    }
    $sha1 = [System.BitConverter]::ToString((New-Object System.Security.Cryptography.SHA1Managed).ComputeHash([System.Text.Encoding]::UTF8.GetBytes($sig))).Replace('-','').ToLower()
    $dst = Join-Path $stage ($sha1 + '.bin')
    try {
        Copy-Item $f.FullName $dst -Force
        $new += [ordered]@{ name = $f.Name; size = [int64]$f.Length; mtime = $mt; sha1 = $sha1 }
    } catch {
        # maybe still writing, retry next round
    }
}
$result = [ordered]@{ new = $new; cleaned = $cleaned; total = $files.Count }
Write-Output ($result | ConvertTo-Json -Compress -Depth 5)
