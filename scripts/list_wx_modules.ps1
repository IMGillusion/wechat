$ErrorActionPreference = "Continue"
$out = @()
foreach ($p in Get-Process -Name "Weixin","WeChatAppEx" -ErrorAction SilentlyContinue) {
  $mods = $p.Modules
  $wx = $mods | Where-Object { $_.ModuleName -eq "Weixin.dll" } | Select-Object -First 1
  $vd = $mods | Where-Object { $_.ModuleName -eq "version.dll" } | Select-Object -First 1
  $wxbase = if ($wx) { "0x{0:X}" -f $wx.BaseAddress } else { "none" }
  $vdpath = if ($vd) { $vd.FileName } else { "none" }
  $line = "{0} pid={1} wx={2} vdl={3}" -f $p.Name, $p.Id, $wxbase, $vdpath
  $out += $line
}
$out | Out-File -Encoding ascii "C:\wechat\wx_modules.txt"
Write-Output "DONE " + $out.Count
