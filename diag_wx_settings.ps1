[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$base = "D:\program\xwechat_files\wxid_YOUR_WXID_<hash>"

Write-Output "=== TOP LEVEL DIRS under base ==="
Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Output ("DIR  {0}" -f $_.Name)
}
Write-Output ""
Write-Output "=== TOP LEVEL FILES under base ==="
Get-ChildItem -Path $base -File -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Output ("FILE {0}  {1}" -f $_.Name, $_.Length)
}

Write-Output ""
Write-Output "=== ALL .db under db_storage ==="
$db = "$base\db_storage"
if (Test-Path $db) {
    Get-ChildItem -Path $db -Filter *.db -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Output ("{0}  {1}" -f $_.FullName, $_.Length)
    }
}

Write-Output ""
Write-Output "=== ALL subdirs under base (2 levels) ==="
Get-ChildItem -Path $base -Directory -Recurse -ErrorAction SilentlyContinue -Depth 1 | ForEach-Object {
    Write-Output $_.FullName
}

Write-Output ""
Write-Output "=== files with settings/config/preference/general in name (whole base) ==="
Get-ChildItem -Path $base -File -Recurse -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match "(?i)(setting|config|prefer|general|storage|cache)" 
} | Select-Object -First 60 | ForEach-Object {
    Write-Output ("{0}  {1}" -f $_.FullName, $_.Length)
}
Write-Output "DONE"
