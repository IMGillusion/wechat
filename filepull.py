#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filepull —— 微信 4.x 文件自动拉回（落盘即捡）

背景（2026-09-02 实测）：微信 4.x 收到文件消息会**自动下载**落盘 D 盘
（落盘 mtime == 消息到达时间，两个样本均早于任何预览点击），不需要点预览。
所以全自动文件接收 = 轮询落盘目录 + scp 拉回 + 定期清理，不用抓包。

流程（每轮 ~30s，由 main.py 循环驱动）：
  1. 本地 state 的已拉回签名(name|size|mtime) 写 known_files.txt scp 到 Windows
  2. 远程 wx_filepoll.ps1：枚举 D 盘落盘目录，新文件 sha1 改名拷到暂存区，
     已拉回且超过 CLEANUP_HOURS 的从 D 盘删除（本体已批准定期清理）
  3. 本地 scp 暂存文件回 media/files/，校验大小，写 state，回调 notify

state 独立存 state_files.json，与 receive 的 state.json 分开。
只读微信数据；D 盘删除仅限「已拉回成功 + 超过保留期」的文件。
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(HERE, "media", "files")
STATE_FILE = os.path.join(HERE, "state_files.json")
LOCAL_KNOWN = os.path.join(HERE, "known_files_local.txt")
LOCAL_PS1 = os.path.join(HERE, "wx_filepoll.ps1")

SSH_KEY = "/tmp/wechat_win/id_ed25519_your_key"
SSH_PORT = "<SSH_PORT>"
SSH_USER_HOST = "<SSH_USER>@<WINDOWS_IP>"
REMOTE_PS1 = r"C:\Users\Administrator\wxbuild\wx_filepoll.ps1"
REMOTE_KNOWN = r"C:\Users\Administrator\wxbuild\known_files.txt"
REMOTE_STAGE = r"C:\Users\Administrator\wxbuild\staging"

MY_WXID = "wxid_YOUR_WXID"
CLEANUP_HOURS = 24      # 已拉回文件的 D 盘保留时长，超了删
STATE_MAX = 300         # state 只留最近 N 条
RUN_TIMEOUT = 90
SCP_TIMEOUT = 300

# 远程脚本：枚举 + 拷暂存 + 清理，输出单行 JSON。
# 注意：PS5.1 ConvertTo-Json 不转义非 ASCII，控制台默认 GBK 输出会把中文打乱，
# 所以脚本头强制 OutputEncoding=UTF8（纯 ASCII 过 ssh 到本地 utf-8 解码）。
WX_FILEPOLL_PS1 = r"""[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
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
"""


def log(tag, msg):
    print(f"[{datetime.datetime.now().strftime('%m-%d %H:%M:%S')}] {tag} {msg}", flush=True)


def _ssh_base():
    return ["ssh", "-i", SSH_KEY, "-p", SSH_PORT, "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3", SSH_USER_HOST]


def ssh(cmd, timeout=RUN_TIMEOUT):
    r = subprocess.run(_ssh_base() + [cmd], capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ssh fail {r.returncode}: {r.stderr.decode('utf-8', 'ignore')[:200]}")
    return r.stdout.decode("utf-8", "replace")


def scp(src, dst, timeout=SCP_TIMEOUT):
    r = subprocess.run(["scp", "-i", SSH_KEY, "-P", SSH_PORT, "-o", "BatchMode=yes",
                        "-o", "ConnectTimeout=10", src, dst],
                       capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"scp fail {r.returncode}: {r.stderr.decode('utf-8', 'ignore')[:200]}")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(st):
    items = sorted(st.items(), key=lambda kv: kv[1].get("pulled_at", 0), reverse=True)[:STATE_MAX]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(items), f, ensure_ascii=False, indent=1)


def sanitize(name):
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name).strip().lstrip(".")
    return name[:150] or "file"


def ensure_remote():
    """推送 ps1（内容变了才推：比对远程 wc -c 太麻烦，直接每轮前不推，启动推一次+失败重推）。"""
    with open(LOCAL_PS1, "w", encoding="utf-8") as f:
        f.write(WX_FILEPOLL_PS1)
    scp(LOCAL_PS1, f"{SSH_USER_HOST}:{REMOTE_PS1}")
    log("filepull", "远程脚本已推送")


def push_known(st):
    with open(LOCAL_KNOWN, "w", encoding="utf-8") as f:
        for k in st.keys():
            f.write(k + "\n")
    scp(LOCAL_KNOWN, f"{SSH_USER_HOST}:{REMOTE_KNOWN}")


def sync(notify=None):
    """跑一轮。notify(local_path, meta) 在每拉回一个新文件后回调。返回新拉回列表。"""
    st = load_state()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        push_known(st)
        out = ssh(f"powershell -NoProfile -ExecutionPolicy Bypass -File {REMOTE_PS1}")
    except Exception as e:
        log("filepull", f"远程轮询失败: {e}")
        raise
    line = out.strip().splitlines()[-1] if out.strip() else "{}"
    try:
        data = json.loads(line)
    except Exception:
        log("filepull", f"远程输出解析失败: {line[:120]}")
        return []
    if data.get("error"):
        log("filepull", f"远程报错: {data['error']}")
        return []

    pulled = []
    for m in data.get("new", []):
        name, size, sha1 = m.get("name"), int(m.get("size", 0)), m.get("sha1")
        mt = int(m.get("mtime", 0))
        sig = f"{name}|{size}|{mt}"
        if sig in st:
            continue
        tmp = os.path.join(HERE, "media", f".pulling_{sha1}.bin")
        try:
            scp(f"{SSH_USER_HOST}:{REMOTE_STAGE}/{sha1}.bin", tmp)
            if os.path.getsize(tmp) != size:
                os.remove(tmp)
                log("filepull", f"大小不符，跳过 {name} (本地{os.path.getsize(tmp) if os.path.exists(tmp) else '?'}≠远端{size})")
                continue
            dst = os.path.join(MEDIA_DIR, sanitize(name))
            if os.path.exists(dst):
                dst = dst + f".{int(time.time())}"
            os.replace(tmp, dst)
            st[sig] = {"name": name, "size": size, "mtime": mt,
                       "pulled_at": time.time(), "path": dst}
            save_state(st)
            pulled.append((dst, m))
            log("filepull", f"拉回 {name} {size}B -> {dst}")
            if notify:
                try:
                    notify(dst, m)
                except Exception as e:
                    log("filepull", f"notify 失败: {e}")
        except Exception as e:
            log("filepull", f"拉取失败 {name}: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    for cname in data.get("cleaned", []):
        log("filepull", f"D 盘清理: {cname}")
    return pulled


def main():
    """调试：推脚本 + 跑一轮。"""
    ensure_remote()
    pulled = sync()
    print(f"done, pulled={len(pulled)}")


if __name__ == "__main__":
    main()
