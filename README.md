# wechat

微信 4.x 接入通道（Hook + 无 root 部署）。在 Windows 机上对微信 4.x 做
内存 Hook 拿到 QueryDB 查询服务，Linux 侧通过 SSH 隧道直连，
实现消息接收、文件拉取、发送。

## 组成

- `main.py` —— 主入口：SSH 隧道管理 + QueryDB 消息轮询 + 触发判定 + 文件拉取调度
- `receive.py` —— QueryDB 接收层（轮询新消息、按群/私聊概率触发、文件落地）
- `filepull.py` —— 微信大文件/文件消息拉取（SSH 到 Windows 机扫落盘目录）
- `wx_filepoll.ps1` —— Windows 侧落盘目录轮询脚本
- `config.yaml` —— 配置（隧道口、触发概率、扫描间隔）
- `diag_*.ps1` / `pull_wx_src.ps1` / `scripts/*.ps1` —— 诊断与源码拉取辅助脚本

## 部署前置

1. Windows 机装微信 4.x，Hook 版 version.dll 部署后重启微信（IsLogin 是登录状态唯一判据）
2. Windows 机开 SSH，SSH key 配好（config 里占位符替换成你的）
3. Linux 侧 `ssh -L 30001:127.0.0.1:30001 <win机>` 建隧道
4. 替换 `config.yaml` 和 `main.py`/`filepull.py`/`*.ps1` 里的 `<WINDOWS_IP>` `<SSH_USER>` `<SSH_PORT>` `wxid_YOUR_WXID` 占位符

## 触发规则（与 QQ 对齐）

- 消息含本机名字/@我：100% 触发
- 群消息：5% / 私聊：20%
- 文件落地：50%

## 赞助

如果这个项目对你有用，欢迎赞助支持一下，请我喝杯奶茶：

![sponsor](assets/sponsor.jpg)

—— 幻日出品
