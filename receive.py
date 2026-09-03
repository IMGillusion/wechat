#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat receiver poller —— 轮询微信 4.x 本地库，近实时接收消息。

原理（2026-09-02 实测验证）：
  微信 4.x 消息不落老的 MicroMsg.db，落在 hook 能读到的 sqlite 里：
    - message_fts.db 的 4 个全文索引分区 message_fts_v4_0..v4_3 = 消息正文流
      （列 acontent=正文 / session_id / sender_id / create_time(毫秒) / local_type）
    - name2id（只一列 username，本地id=rowid）= 本地id ↔ wxid/群id 映射
    - session.db SessionTable = 变更检测（谁有新消息/未读/最后一条摘要）

轮询逻辑：
  每个 FTS 分区记一个 create_time watermark（存 state.json），
  每次只取 create_time > watermark 的行 = 新消息。name2id 定期刷新（新联系人会加）。

数据源是 hook 的 HTTP 接口（走 ssh -L 隧道到 Windows 机的 30001）。
本脚本只读，不写微信任何文件。

用法：
  python3 receive.py --once                 # 扫一轮，打印新消息（首次 watermark=0 会灌全量）
  python3 receive.py --once --baseline      # 首轮只设 watermark 不输出（灌基线，避免把历史全刷出来）
  python3 receive.py --loop --interval 5    # 常驻轮询，每 5 秒一轮，新消息 append 到 cache/
  python3 receive.py --loop --interval 3 --baseline
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
FTS_PARTITIONS = ["message_fts_v4_0", "message_fts_v4_1", "message_fts_v4_2", "message_fts_v4_3"]


def qdb(base_url: str, dbname: str, sql: str):
    """POST /QueryDB/execute，返回 data 列表（失败抛异常）。"""
    body = json.dumps({"optDbName": dbname, "SQL": sql}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/QueryDB/execute",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    if d.get("status") != 0:
        raise RuntimeError(f"QueryDB failed {dbname}: {d.get('desc')}")
    return d.get("data", [])


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"fts_watermarks": {}, "name2id": {}, "name2id_ts": 0}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def refresh_name2id(base_url: str, st: dict, force=False, max_age=300):
    """刷新 本地id→wxid 映射（新联系人会新增 rowid）。"""
    now = time.time()
    if not force and now - st.get("name2id_ts", 0) < max_age and st.get("name2id"):
        return
    rows = qdb(base_url, "message_fts.db", "SELECT rowid, username FROM name2id")
    mapping = {str(r["rowid"]): r["username"] for r in rows}
    st["name2id"] = mapping
    st["name2id_ts"] = now
    return mapping


def wxid(st: dict, local_id):
    return st.get("name2id", {}).get(str(local_id), f"localid:{local_id}")


def to_int(v, default=0):
    """QueryDB 一律返回字符串，数字字段强转 int（失败给默认）。"""
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return default


def poll_once(base_url: str, st: dict, baseline=False, verbose=True):
    """扫一轮，返回新消息列表。baseline 模式下首轮只记 watermark 不返回。"""
    refresh_name2id(base_url, st)
    new_msgs = []
    for p in FTS_PARTITIONS:
        wm = st["fts_watermarks"].get(p, 0)
        try:
            rows = qdb(
                base_url,
                "message_fts.db",
                f"SELECT acontent, message_local_id, sort_seq, local_type, session_id, "
                f"sender_id, create_time FROM {p} WHERE create_time > {wm} "
                f"ORDER BY create_time ASC LIMIT 500",
            )
        except RuntimeError:
            # 分区可能不存在（微信版本差异），跳过
            continue
        if not rows:
            continue
        # 推进 watermark 到本批最大值（create_time 是秒，强转 int）
        st["fts_watermarks"][p] = max(to_int(r["create_time"]) for r in rows)
        if baseline:
            continue
        for r in rows:
            new_msgs.append({
                "session_id": to_int(r.get("session_id")),
                "session_username": wxid(st, r.get("session_id")),
                "sender_id": to_int(r.get("sender_id")),
                "sender_username": wxid(st, r.get("sender_id")),
                "content": r.get("acontent"),
                "local_type": to_int(r.get("local_type")),
                "message_local_id": r.get("message_local_id"),
                "sort_seq": to_int(r.get("sort_seq")),
                "create_time": to_int(r.get("create_time")),
                "source": "message_fts",
                "partition": p,
            })
    new_msgs.sort(key=lambda m: m.get("create_time") or 0)
    # 遇到解析不到的 localid（新联系人/会话），强制刷 name2id 重解析一次
    if any(m.get("session_username", "").startswith("localid:")
           or m.get("sender_username", "").startswith("localid:") for m in new_msgs):
        refresh_name2id(base_url, st, force=True)
        for m in new_msgs:
            m["session_username"] = wxid(st, m["session_id"])
            m["sender_username"] = wxid(st, m["sender_id"])
    save_state(st)
    return new_msgs


def fmt_msg(m):
    ct = m.get("create_time") or 0
    try:
        t = datetime.datetime.fromtimestamp(ct).strftime("%m-%d %H:%M:%S")
    except Exception:
        t = str(ct)
    return f"[{t}] {m.get('session_username')} -> {m.get('content')}"


def write_cache(msgs):
    if not msgs:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    path = os.path.join(CACHE_DIR, f"wechat_{stamp}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:30001")
    ap.add_argument("--once", action="store_true", help="扫一轮就退出")
    ap.add_argument("--loop", action="store_true", help="常驻轮询")
    ap.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒")
    ap.add_argument("--baseline", action="store_true", help="首轮只设 watermark 不输出历史")
    ap.add_argument("--verbose", action="store_true", default=True)
    ap.add_argument("--state", help="自定义 state 文件路径")
    args = ap.parse_args()

    global STATE_FILE
    if args.state:
        STATE_FILE = args.state

    if not args.once and not args.loop:
        args.once = True

    st = load_state()
    if args.once:
        msgs = poll_once(args.base_url, st, baseline=args.baseline, verbose=args.verbose)
        if not args.baseline:
            path = write_cache(msgs)
            for m in msgs:
                print(fmt_msg(m))
            print(f"\n({len(msgs)} 条新消息" + (f" -> {path}" if path else "") + ")")
        else:
            print(f"baseline 已设: {json.dumps(st['fts_watermarks'], ensure_ascii=False)}")
        return

    # 常驻轮询
    print(f"[wechat-receiver] 常驻轮询 base={args.base_url} interval={args.interval}s")
    first = True
    while True:
        try:
            baseline = first and args.baseline
            msgs = poll_once(args.base_url, st, baseline=baseline)
            if msgs and not baseline:
                path = write_cache(msgs)
                for m in msgs:
                    print(fmt_msg(m), flush=True)
                print(f"  (+{len(msgs)} 条 -> {path})", flush=True)
        except Exception as e:
            print(f"[wechat-receiver] 轮询出错: {e}", flush=True)
        first = False
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
