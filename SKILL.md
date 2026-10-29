---
name: wechat-group-summary
description: 读取本机已登录 Windows 微信（4.x，xwechat_files）中指定群聊在指定时间范围内的本地聊天记录，导出结构化消息数据，并生成中文总结报告、离线 HTML 网页与 PNG 长图。当用户要求"总结微信群聊天记录""导出某个群的聊天""做群聊日报/周报""读取微信群消息并生成报告"时使用。仅在用户本人账号数据上运行，需微信在本机保持登录。
agent_created: true
---

# 微信群聊记录读取与总结报告

## Overview

从本机已登录的 Windows 微信 4.x 本地数据库中，按"完整群名 + 时间窗口"导出聊天记录（自动完成密钥提取、解密、WAL 合并、去重、发送人映射），再撰写中文总结并渲染离线 HTML 与 PNG 长图。所有读取均为只读，密钥只存内存，临时明文库用完即覆写删除。

已在微信 4.1.15.56（Windows 11）上实测通过。解密能力来自内置的 vendor/wcdb-key-tool（TANGandXUE, MIT, 固定提交 79f1b5b）。

## 环境要求

- Windows + 微信 4.x 已登录（Weixin.exe 运行中）
- Python 解释器：优先用托管环境 `~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（已装 Pillow + zstandard）
- 若该 venv 不存在或缺依赖，用 `~/.workbuddy/binaries/python/versions/3.13.12/python.exe -m venv` 重建并 `pip install pillow zstandard`
- PNG 中文字体自动回退：msyh → simhei → simsun（Windows 自带，无需安装）

## 工作流程

### 第 1 步：确认参数

- **群名**：缺失时用 AskUserQuestion 询问。先运行 `groups` 命令列出本机群聊供用户选择。要求完整群名（备注也可匹配）；同名群会自动按群 ID 消歧，仍有歧义时列出候选让用户选。
- **时间范围**：默认最近 24 小时，可选 48h/72h，或 `--start`/`--end` 自定义（含起点、不含终点）。
- **多账号**：自动锁定当前登录账号（按 all_users/login 下 key_info.db 修改时间判定），不会混读；用户显式要求时才用 `--account` 指定。

### 第 2 步：导出消息

在用户的工作目录下运行（outputs/ 会生成在该目录）：

```bash
cd "<用户工作目录>"
PY="$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
CLI="$HOME/.workbuddy/skills/wechat-group-summary/scripts/wxsum_cli.py"

"$PY" "$CLI" groups                                   # 列出群聊（需要时）
"$PY" "$CLI" export --group "完整群名" --range 24h     # 导出
# 自定义时间窗： --start "2026-10-27 12:00" --end "2026-10-28 12:00"
```

导出成功输出 `{"out_dir": "outputs/<时间戳>_<群名>"}`，内含 messages.json / messages.txt。导出过程约 30–60 秒（内存扫描 + 解密）。

### 第 3 步：撰写总结

1. 通读导出目录的 messages.txt **全部内容**（消息多时分批读完，禁止只总结开头几条）。
2. 读取 `references/report-format.md`，按其规范在同目录写 `summary.md` 和 `report.json`。
3. 核心要求：每个结论关联真实消息 ID `[mNN]`；严格区分建议/决定、收到/同意、同意/完成、观点/事实；无负责人或截止时间写"未明确"；图片视频语音禁止描述内容（语音有内嵌转写时可用并注明）。

### 第 4 步：渲染与交付

```bash
"$PY" "$CLI" render --dir "outputs/<导出目录>"
```

生成 index.html 与 report.png。然后：
1. 核验：report.json 引用的 msg_ids 全部存在于 messages.json；meta 统计与消息数一致；PNG 无乱码/截断/重叠（用 Read 查看）；HTML 无外部脚本/字体依赖。
2. 用 present_files 交付 index.html、report.png、summary.md、messages.json。

## 安全约束（已内建于代码，执行时遵守）

- 仅处理用户本人当前登录账号；原始数据库全程只读（先复制快照再解密）
- 密钥只在内存短暂使用，禁止写入日志/配置/报告/仓库
- 临时明文库位于 `scripts/.secure_tmp/`，退出时自动覆写删除；异常中断后若发现残留，先覆写再删除
- 不部署公网；HTML 对群名/昵称/正文全部转义

## 故障排查

| 现象 | 处理 |
|---|---|
| "未获得密钥" / 扫描 0 候选 | 确认微信已登录且为 4.x；重启微信后重试 |
| 群名找不到 | 运行 `groups` 核对完整群名（注意 emoji、空格）；用群备注也可 |
| 同名群歧义 | 工具会列出候选群 ID 与元信息，交给用户选择 |
| 消息数偏少 | 本机只含已同步消息，不代表完整历史；报告必须注明该范围限制 |
| 微信大版本更新后失效 | 密钥机制可能变化，先运行 `selftest_fake.py` 确认管线本身正常，再核查 wcdb-key-tool 上游是否有新版 |

## 自检

修改代码后或不碰微信的回归验证：

```bash
"$PY" "$HOME/.workbuddy/skills/wechat-group-summary/scripts/selftest_fake.py"
```

用虚构数据验证解析/去重/时间边界/渲染全链路，全部断言通过会输出 OK。

## Resources

- `scripts/wxsum_cli.py` — 命令行启动器（groups / export / render）
- `scripts/wxsum/` — 导出与渲染核心包（解密快照、群解析、消息解析、HTML/PNG 渲染）
- `scripts/vendor/wcdb-key-tool/` — 微信 4.x 密钥提取与 SQLCipher 解密（上游 MIT 许可，含 LICENSE）
- `scripts/selftest_fake.py` — 虚构数据端到端自检
- `references/report-format.md` — summary.md / report.json 格式规范、消息引用规则、口径规范
