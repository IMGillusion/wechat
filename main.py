#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat 接收子项目主进程 —— 常驻轮询微信 4.x 本地库，收消息并触发幻日。

职责：
  1. 维持 ssh -L 隧道到 Windows 机 hook 口（默认自管；--no-tunnel 用现成的）
  2. 轮询 message_fts 4 分区 + name2id（复用 receive.py 的 watermark 逻辑）
  3. 过滤掉自己发的消息（sender == my_wxid），只留发给我的
  4. 触发判定（跟 QQ 对齐）：含名字/@我 100%，否则群 5% / 私聊 20%；文件落地按概率通知
  5. 触发时拉该会话最近 20 条写 trigger 缓存，注入 [微信 触发] 到 huanri

只读微信数据，绝不写微信任何文件。
"""
import argparse
import datetime
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import receive as R  # noqa: E402
import filepull as FP  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))

# 配置（也可从 config.yaml 读，这里给内联默认，改配置改这里）
BASE_URL = os.environ.get("WECHAT_BASE_URL", "http://127.0.0.1:30001")
MY_WXID = os.environ.get("WECHAT_MY_WXID", "wxid_YOUR_WXID")   # 本机微信的 wxid（收消息的号）
SELF_NAMES = ["幻日"]
POLL_INTERVAL = float(os.environ.get("WECHAT_POLL_INTERVAL", "5"))
# 触发概率跟 QQ 对齐（本体 2026-09-03）：含名字/@我 100%，否则群 5% / 私聊 20%；
# 文件落地按概率通知（不百分百触发）
GROUP_TRIGGER_PROB = float(os.environ.get("WECHAT_GROUP_TRIGGER_PROB", "0.05"))
PRIVATE_TRIGGER_PROB = float(os.environ.get("WECHAT_PRIVATE_TRIGGER_PROB", "0.20"))
FILE_TRIGGER_PROB = float(os.environ.get("WECHAT_FILE_TRIGGER_PROB", "0.50"))
DEBOUNCE_SEC = 6.0          # 两次注入最小间隔
TMUX_SESSION = os.environ.get("WECHAT_TMUX_SESSION", "huanri")
TRIGGER_CACHE_DIR = os.path.join(HERE, "cache", "triggers")
CONTEXT_LIMIT = 20

SSH_TUNNEL_CMD = [
    "ssh", "-i", "/tmp/wechat_win/id_ed25519_your_key", "-p", "<SSH_PORT>",
    "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o",
    "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10", "-N",
    "-L", "30001:127.0.0.1:30001",
    "<SSH_USER>@<WINDOWS_IP>",
]


def log(tag, msg):
    print(f"[{datetime.datetime.now().strftime('%m-%d %H:%M:%S')}] {tag} {msg}", flush=True)


def port_up(port):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


class Tunnel:
    """自管 ssh -L 隧道：进程死了自动拉起。"""

    def __init__(self, cmd, port):
        self.cmd = cmd
        self.port = port
        self.proc = None

    def ensure(self):
        if port_up(self.port):
            return  # 已有隧道（自管或外部），直接用
        self._spawn()

    def _spawn(self):
        log("tunnel", f"拉起隧道 {self.cmd[-1]}:{self.port}")
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 等端口起来
        for _ in range(20):
            time.sleep(0.5)
            if port_up(self.port):
                log("tunnel", "隧道已就绪")
                return
        log("tunnel", "警告: 隧道 10s 未就绪，继续尝试（下一轮会重试）")

    def keepalive(self):
        # 端口在就不动（不管隧道是不是自己起的）；掉了才重拉
        if port_up(self.port):
            return
        if self.proc is not None and self.proc.poll() is not None:
            self.proc = None
        if self.proc is None:
            self._spawn()


def qdb(base_url, dbname, sql):
    return R.qdb(base_url, dbname, sql)


def is_group(session_username):
    return session_username.endswith("@chatroom") or session_username.endswith("@openim")


def mentions_me(content, names):
    if not content:
        return False
    c = str(content)
    return any(n in c for n in names) or any(f"@{n}" in c for n in names)


def pull_context(base_url, st, session_id, limit=CONTEXT_LIMIT):
    """拉某会话最近 N 条消息（全 FTS 分区），映射 wxid。"""
    rows = []
    for p in R.FTS_PARTITIONS:
        try:
            r = qdb(base_url, "message_fts.db",
                    f"SELECT acontent, session_id, sender_id, create_time, local_type "
                    f"FROM {p} WHERE session_id={int(session_id)} ORDER BY create_time DESC LIMIT {limit}")
        except RuntimeError:
            continue
        rows.extend(r)
    rows.sort(key=lambda x: R.to_int(x.get("create_time")), reverse=True)
    out = []
    for r in rows[:limit]:
        out.append({
            "content": r.get("acontent"),
            "sender": st.get("name2id", {}).get(str(r.get("sender_id")), f"localid:{r.get('sender_id')}"),
            "create_time": R.to_int(r.get("create_time")),
            "local_type": R.to_int(r.get("local_type")),
        })
    return out


def write_trigger_cache(session_username, session_id, msgs):
    os.makedirs(TRIGGER_CACHE_DIR, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(session_username))[:40] or "session"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(TRIGGER_CACHE_DIR, f"wx_{safe}_{ts}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return path


def inject(text):
    """tmux send-keys 注入 huanri。"""
    # 转义: tmux 用单引号包裹，内部单引号用 '\' 转
    payload = text.replace("'", "'\\''")
    cmd = f"tmux send-keys -t {TMUX_SESSION} \"[微信 触发] {payload}\" Enter"
    try:
        subprocess.run(["bash", "-c", cmd], timeout=10)
        return True
    except Exception as e:
        log("inject", f"注入失败: {e}")
        return False


def should_trigger(session_username, content):
    """触发判定（跟 QQ 对齐，本体 2026-09-03）：
    文本含我的名字（含 @我）→ 100%；否则按概率 群 GROUP / 私聊 PRIVATE。
    返回 (是否触发, 原因)。
    """
    if mentions_me(content, SELF_NAMES):
        return True, f"消息含名字「{SELF_NAMES[0]}」"
    if is_group(session_username):
        if random.random() < GROUP_TRIGGER_PROB:
            return True, f"概率触发({GROUP_TRIGGER_PROB:.0%})"
        return False, ""
    if random.random() < PRIVATE_TRIGGER_PROB:
        return True, f"概率触发({PRIVATE_TRIGGER_PROB:.0%})"
    return False, ""


def on_file(path, meta):
    """文件拉回成功 → 按概率注入 [微信 文件] 给幻日（文件不百分百触发，本体 2026-09-03）。
    文件照拉照落地，只是通知按概率走。"""
    if random.random() >= FILE_TRIGGER_PROB:
        log("file", f"文件 {meta.get('name')} 已落地 {path}（概率 {FILE_TRIGGER_PROB:.0%} 未触发，不通知）")
        return
    ct = datetime.datetime.fromtimestamp(meta.get("mtime") or 0).strftime("%m-%d %H:%M")
    line = (f"[微信 文件] 收到新文件 {meta['name']}（{meta['size']} 字节，微信时间 {ct}）"
            f"已落地: {path} | 原因: 新文件落地")
    if inject(line):
        log("file", line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tunnel", action="store_true", help="不自己管隧道，用现成的 30001")
    ap.add_argument("--once", action="store_true", help="扫一轮就退（调试）")
    ap.add_argument("--baseline", action="store_true", help="首轮只设水位不触发历史")
    ap.add_argument("--base-url", default=BASE_URL)
    args = ap.parse_args()

    tunnel = None
    if not args.no_tunnel:
        tunnel = Tunnel(SSH_TUNNEL_CMD, 30001)
        tunnel.ensure()

    # 文件拉回远程脚本（失败不阻塞主流程，下一轮 sync 会自己重试报错）
    try:
        FP.ensure_remote()
    except Exception as e:
        log("filepull", f"远程脚本推送失败（稍后重试）: {e}")

    st = R.load_state()
    seen = {}          # dedup key -> ts
    last_inject = 0.0
    if os.environ.get("WECHAT_DEDUP_STATE", os.path.join(HERE, "seen.json")):
        seen_file = os.path.join(HERE, "seen.json")
        if os.path.exists(seen_file):
            try:
                with open(seen_file) as f:
                    seen = json.load(f)
            except Exception:
                seen = {}

    log("main", f"启动 base={args.base_url} interval={POLL_INTERVAL}s my={MY_WXID}")

    def save_seen():
        # 只留 1 小时内
        now = time.time()
        filtered = {k: v for k, v in seen.items() if now - v < 3600}
        with open(os.path.join(HERE, "seen.json"), "w") as f:
            json.dump(filtered, f)

    first = True
    loop_counter = [0]
    while True:
        if tunnel:
            tunnel.keepalive()
        baseline = first and args.baseline
        try:
            msgs = R.poll_once(args.base_url, st, baseline=baseline)
        except Exception as e:
            log("poll", f"轮询出错: {e}")
            if args.once:
                return
            # 不 continue：置空 msgs 让本轮走到 FP.sync()，文件拉回不被消息轮询异常绑架
            msgs = []

        if not baseline:
            now = time.time()
            for m in msgs:
                # 过滤自己发的
                if m.get("sender_username") == MY_WXID:
                    continue
                key = f"{m.get('partition')}:{m.get('message_local_id')}:{m.get('create_time')}"
                if key in seen:
                    continue
                seen[key] = now
                trig, reason = should_trigger(m.get("session_username"), m.get("content"))
                if not trig:
                    continue
                if now - last_inject < DEBOUNCE_SEC:
                    continue  # 防刷屏，跳过但已记 seen
                try:
                    ctx = pull_context(args.base_url, st, m.get("session_id"))
                    cache_path = write_trigger_cache(m.get("session_username"),
                                                     m.get("session_id"), ctx)
                except Exception as e:
                    cache_path = ""
                    log("ctx", f"拉上下文失败: {e}")
                ct = datetime.datetime.fromtimestamp(m.get("create_time") or 0).strftime("%m-%d %H:%M:%S")
                gtag = "群" if is_group(m.get("session_username")) else "私"
                # 触发消息具体内容不写进注入串（本体 2026-09-03 要求）：
                # 带了内容会让人懒得读缓存，缓存里才有全量上下文
                line = (f"{gtag} {m.get('session_username')} [{ct}] 来了条新消息 | 缓存: {cache_path} | 原因: {reason}")
                if inject(line):
                    last_inject = now
                    log("trigger", line)
            save_seen()

        if args.once:
            break
        first = False
        # 文件拉回：每 6 轮（~30s）一次，独立于消息轮询
        loop_n = loop_counter[0]
        loop_counter[0] += 1
        if loop_n % 6 == 0:
            try:
                FP.sync(notify=on_file)
            except Exception as e:
                log("filepull", f"轮询出错: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
