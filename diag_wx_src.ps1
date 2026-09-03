[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Output "=== wxbuild dir contents ==="
Get-ChildItem -Path "C:\Users\Administrator\wxbuild" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Output ("{0}  {1}" -f $_.Name, $(if($_.PSIsContainer){"<DIR>"}else{$_.Length}))
}
Write-Output ""
Write-Output "=== source files (cpp/h/vcxproj/sln) under wxbuild ==="
Get-ChildItem -Path "C:\Users\Administrator\wxbuild" -Recurse -Include *.cpp,*.h,*.cc,*.hpp,*.vcxproj,*.sln,*.py,*.json -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Output ("{0}  {1}" -f $_.FullName, $_.Length)
}
Write-Output ""
Write-Output "=== search whole C:\Users\Administrator for hook project (vcxproj with version/wxhook) ==="
Get-ChildItem -Path "C:\Users\Administrator" -Recurse -Include *.vcxproj,*.sln -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "(?i)(hook|version|wx|weixin)" } | ForEach-Object {
    Write-Output $_.FullName
}
Write-Output "DONE"
