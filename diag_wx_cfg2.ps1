[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$base = "D:\program\xwechat_files\wxid_YOUR_WXID_<hash>"

function HexDump($path, $max) {
    Write-Output ("#### " + $path + " ####")
    if (-not (Test-Path $path)) { Write-Output "  MISSING"; return }
    $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $len = [Math]::Min($max, $fs.Length)
        $buf = New-Object byte[] $len
        [void]$fs.Read($buf, 0, $len)
        Write-Output ("  filelen=" + $fs.Length + " shown=" + $len)
        for ($i=0; $i -lt $len; $i += 32) {
            $n = [Math]::Min(32, $len - $i)
            $hex = ""
            $asc = ""
            for ($j=0; $j -lt $n; $j++) {
                $b = $buf[$i+$j]
                $hex += $b.ToString("x2") + " "
                if ($b -ge 32 -and $b -le 126) { $asc += [char]$b } else { $asc += "." }
            }
            Write-Output ($i.ToString("x6") + "  " + $hex + " |" + $asc + "|")
        }
    } catch {
        Write-Output ("  ERR " + $_.Exception.Message)
    } finally {
        $fs.Close()
    }
    Write-Output ""
}

HexDump "$base\config\file_config_2026" 1024
HexDump "$base\config\newclientconfig\config" 1024
HexDump "$base\resource\config" 1024
Write-Output "DONE"
