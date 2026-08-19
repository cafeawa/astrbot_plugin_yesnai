# AGENTS.md

本文件给 AI Agent / 协作者提供开发这个 AstrBot 插件时的必要上下文。

## 项目概览

- 仓库：`astrbot_plugin_yesnai`
- 类型：AstrBot 插件（Star）
- 功能：调用 YesNovelAI / YesNAI API 生成 NovelAI 风格图片
- 主语言：Python
- 主要依赖：`aiohttp`、AstrBot SDK（运行在 AstrBot 环境内）

## 目录结构

```text
astrbot_plugin_yesnai/
├── main.py              # AstrBot 插件入口、命令、Agent 工具
├── yesnai_client.py     # YesNAI API 客户端
├── _conf_schema.json    # 插件配置 Schema
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 依赖
├── README.md            # 用户文档
└── AGENTS.md            # 本文件
```

## 插件命令

| 命令 | 说明 |
|---|---|
| `/ynai <描述>` | 默认 LLM 翻译 Tag 后生图 |
| `/ynai0 <提示词>` | 直接生图 |
| `/ynai model` | 查看可用模型 |
| `/ynai i2i <描述>` | 图重绘，必须引用/附带一张图片 |
| `/ynai ref <描述>` | 单图角色参考，必须引用/附带一张图片 |
| `/ynai artist list/set/add/del/clear` | 管理画师串（按会话选择） |
| `/ynai preset show/positive/negative` | 预设正反提示词（仅管理员） |
| `/ynai nsfw on/off/status/reset` | NSFW 开关（按会话，修改仅管理员） |
| `/ynai help` | 帮助卡片（使用配置的 `t2i_url`，否则纯文本） |

## Agent 工具

插件暴露了一个 LLM 工具：

- 名称：`ynai_generate`
- 参数：`tags`（逗号分隔的 Danbooru tag）
- 行为：使用默认生图配置生成图片并发送到当前会话
- 注意：工具只需要 LLM 提供 tags，不要传模型/尺寸等参数

## 配置项

`_conf_schema.json` 中主要配置：

| 配置 | 说明 |
|---|---|
| `api_base` | YesNAI API 地址 |
| `api_token` | YesNAI API Token |
| `default_model` | 默认模型 |
| `timeout` | 请求超时（默认 600） |
| `default_width/height/steps/n_samples/sampler` | 默认生图参数 |
| `preset_positive_prompt` / `preset_negative_prompt` | 预设正反提示词 |
| `artists` | 画师串列表，格式 `名称\|\|画师串` |
| `nsfw_enabled` | 全局默认 NSFW 开关 |
| `llm_translation_enabled` / `translation_llm` / `translation_prompt` | LLM 翻译 Tag 相关 |
| `show_tags` | 生图时是否展示最终 Prompt/Tag |
| `t2i_url` | 自定义文转图服务地址，仅使用该地址，不回退 AstrBot 默认 |

## 关键实现约束

- 网络请求统一走 `yesnai_client.py`，使用 `aiohttp`，不要使用 `requests`。
- 生图 API 强制要求 `width`、`height`、`steps`、`n_samples`，默认从配置读取。
- NSFW 逻辑：
  - 关闭：正面自动加 `sfw`，负面自动加 `nsfw`
  - 开启：不注入任何内容
  - NSFW 状态按会话保存，未设置时使用全局默认。
- 画师串选择按会话保存。
- `i2i` 和 `ref` 必须从当前消息或引用消息中读取图片，不使用 `--image` 参数。
- `ref` 当前只支持单图参考，不要实现多角色参考。
- 不实现 `inpaint`。
- `/ynai help` 使用插件配置的 `t2i_url` 渲染文转图卡片；未配置或失败时返回纯文本，不回退 AstrBot 默认文转图。

## 开发与测试

### 本地语法检查

```bash
python3 -m py_compile main.py yesnai_client.py
```

### 复制到 AstrBot 测试

```bash
DST=/mnt/data1/astr/data/plugins/astrbot_plugin_yesnai
cp main.py yesnai_client.py _conf_schema.json metadata.yaml requirements.txt README.md "$DST/"
rm -rf "$DST/__pycache__"
```

### 重启 AstrBot

AstrBot 由 systemd 管理，普通用户无法 `systemctl restart`，可通过 kill 主进程让服务自动重启：

```bash
PID=$(pgrep -f '/mnt/data1/astr/.venv/bin/python3 main.py' | head -1)
kill "$PID"
```

### 查看日志

```bash
journalctl -u astrbot --since '1 min ago' --no-pager | grep -i yesnai
```

### 真实 API 冒烟测试

```bash
curl -sS https://nai.rinko.ai/v1/models
```

### 手动单元测试

在没有完整 AstrBot 环境时，可以用 stub 导入 `main.py` 测试纯逻辑方法（如 `_parse_options`、`_build_parameters`、`_compose_positive_prompt`、`_normalize_artists`）。

## Git 约定

- 一个 commit 只做一个功能/修复，不要混多个无关改动。
- 提交信息使用 Conventional Commits，例如：
  - `feat: ...`
  - `fix: ...`
  - `revert: ...`
  - `refactor: ...`
- 当前仓库有 safe.directory 和 GPG 签名问题，本机提交时使用：

```bash
GIT_CONFIG_GLOBAL=/tmp/gitconfig_yesnai git add <files>
GIT_CONFIG_GLOBAL=/tmp/gitconfig_yesnai git -c commit.gpgsign=false \
  -c user.name='cafe_awa_' -c user.email='120783878+cafeawa@users.noreply.github.com' \
  commit -m '...'
GIT_CONFIG_GLOBAL=/tmp/gitconfig_yesnai git push origin main
```

- 远程：`git@github.com:cafeawa/astrbot_plugin_yesnai.git`
- 推送用 SSH（已配置 deploy key）。
- 如果重写历史，需要 force push，并提前告知用户。

## 不要做的事

- 不要回退到 AstrBot 默认文转图服务。
- 不要使用 `--image` 参数让用户手动传图。
- 不要重新加入多角色参考，除非用户明确要求。
- 不要移除管理员权限检查（`event.is_admin()`）。
- 不要修改 `yesnai_client.py` 的 API 端点路径而不先核对 YesNAI 文档。
