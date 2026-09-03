[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$p = "C:\Users\Administrator\wxbuild\WeChat-Hook-6b8ea7c289235ef276b6802464d42294049b21be"
$stage = "C:\Users\Administrator\wxbuild\srcpull"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
New-Item -ItemType Directory -Force -Path "$stage\include" | Out-Null
# copy all src
Copy-Item -Recurse -Force "$p\src\*" "$stage\src\"
# copy custom headers, skip the big 3rdparty ones
Get-ChildItem -Path "$p\include\*.h" | Where-Object { $_.Name -notin @('json.hpp','httplib.h','lz4.h') } | ForEach-Object {
    Copy-Item -Force $_.FullName "$stage\include\"
}
# vcxproj + sln
Copy-Item -Force "$p\x64_Version_dll.vcxproj" "$stage\"
Copy-Item -Force "$p\x64_Version_dll.sln" "$stage\" 2>$null
# zip
$zip = "C:\Users\Administrator\wxbuild\srcpull.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force
Write-Output ("ZIP_SIZE=" + (Get-Item $zip).Length)
Write-Output "DONE_PULL"
