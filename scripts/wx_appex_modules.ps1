$ErrorActionPreference = "Continue"
$out = @()

# 1. WeChatAppEx command lines
$out += "=== WeChatAppEx cmdlines ==="
foreach ($p in Get-CimInstance Win32_Process -Filter "Name='WeChatAppEx.exe'") {
  $out += ("pid={0} cmd={1}" -f $p.ProcessId, $p.CommandLine.Substring(0, [Math]::Min(200, $p.CommandLine.Length)))
}

# 2. full module list of a couple WeChatAppEx
$wapps = Get-Process -Name "WeChatAppEx" -ErrorAction SilentlyContinue | Select-Object -First 3
foreach ($p in $wapps) {
  $out += ("=== modules of WeChatAppEx pid={0} ===" -f $p.Id)
  try {
    foreach ($m in $p.Modules) {
      if ($m.FileName -and $m.FileName -notmatch "^[A-Za-z]:\\Windows\\") {
        $out += ("  {0}  {1}" -f $m.BaseAddress.ToString("X"), $m.FileName)
      }
    }
  } catch {
    $out += ("  modules error: {0}" -f $_.Exception.Message)
  }
}
$out += "=== END ==="
$out | Out-File -Encoding ascii "C:\wechat\wx_appex.txt"
Write-Output "DONE"
