# astrbot_plugin_yesnai

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，用于调用 [YesNovelAI / YesNAI](https://nai.rinko.ai/api-reference) API 生成 NovelAI 风格图片。

## 功能

- ✅ `/ynai <描述>` 默认使用 LLM 翻译成 Danbooru Tag 后生图
- ✅ `/ynai0 <提示词>` 直接生图（不做 LLM 翻译）
- ✅ `/ynai artist` 管理画师串，生图时自动拼接到提示词前面
- ✅ `/ynai preset` 查看/设置预设正反提示词（仅管理员）
- ✅ `/ynai nsfw` NSFW 开关（`on/off` 仅管理员）
- ✅ `/ynai model` 查看可用模型
- ✅ 管理员设置：画师串增删、预设命令、NSFW 开关仅管理员可操作
- ✅ 完整封装 YesNAI 公开 API（生成、报价、模型、Vibe、放大、图像处理、标注、Native 兼容端点）

## 安装

将本插件目录放到 AstrBot 的 `data/plugins/` 下，然后在 AstrBot WebUI 中启用插件。

```bash
git clone https://github.com/cafe_awa_/astrbot_plugin_yesnai.git
# 或者手动复制到 AstrBot/data/plugins/astrbot_plugin_yesnai
```

## 配置

插件配置在 AstrBot WebUI 的插件管理页面中填写：

| 配置项 | 说明 |
|---|---|
| `api_base` | YesNAI API 基础地址，默认 `https://nai.rinko.ai` |
| `api_token` | 你的 API Token，格式 `ynai-...`，在控制台 API Keys 页面创建 |
| `default_model` | 默认生图模型，如 `nai-diffusion-4-5-full` |
| `timeout` | 请求超时时间（秒），默认 `600`，生图慢时可调大 |
| `default_width` | 默认生图宽度，默认 `832` |
| `default_height` | 默认生图高度，默认 `1216` |
| `default_steps` | 默认采样步数，默认 `28` |
| `default_n_samples` | 默认生成数量（每次生成几张图），默认 `1` |
| `default_sampler` | 默认采样器名称，默认 `k_euler_ancestral` |
| `preset_positive_prompt` | 预设正面提示词，生成时自动拼接到用户提示词前面 |
| `preset_negative_prompt` | 预设负面提示词，生成时作为 `negative_prompt` |
| `artists` | 画师串预设列表，每项格式 `名称\|\|画师串`，ID 按顺序自动生成（也可用 `/ynai artist add` 添加） |
| `nsfw_enabled` | 是否允许 NSFW 内容，默认关闭 |
| `llm_translation_enabled` | 是否启用 LLM 翻译 Tag 功能 |
| `show_tags` | 生图时是否展示 Tag/最终提示词，默认开启 |
| `translation_llm` | 翻译 Tag 使用的 LLM；留空自动使用当前会话 LLM |
| `translation_prompt` | 翻译 LLM 的翻译提示词（系统提示词） |

## 使用

### LLM 翻译 Tag 后生图（默认）

```
/ynai 一个女孩在图书馆里看书
/ynai 夕阳下的海边，一个少年在奔跑
```

### 直接生图

```
/ynai0 1girl, blue sky, masterpiece
```

### 可选生图参数

```
/ynai 一个女孩 --model nai-diffusion-4-5-full --size 832x1216 --steps 28 --scale 5 --seed 123456789 --n 2 --sampler k_euler_ancestral --negative="lowres, bad hands"
```

### 画师串

```
/ynai artist list
/ynai artist add 我的画师 artist_name, artist_style
/ynai artist set 1
/ynai artist clear
/ynai artist del 1
```

画师串由三部分组成：`ID`（唯一标识，程序自动生成 1、2、3...）、`名称`（展示名）、`内容`（具体画师串）。

在 WebUI 配置里添加时，每行填：`名称||画师串`，例如：

```
我的画师||artist_name, artist_style
```

ID 会自动按列表顺序生成。选择画师串后，生成时会自动把它拼接到最终提示词**前面**：

```
画师串, 预设正面提示词, 用户提示词
```

### 预设正反提示词（仅管理员）

```
/ynai preset show
/ynai preset positive masterpiece, best quality, absurdres
/ynai preset negative lowres, bad anatomy, bad hands
```

### NSFW

```
/ynai nsfw status
/ynai nsfw on    # 仅管理员
/ynai nsfw off   # 仅管理员
```

> NSFW 开关不再拦截关键词，而是通过 Tag 控制：
> - 关闭（安全模式）：正面提示词自动添加 `sfw`，负面提示词自动添加 `nsfw`
> - 开启（NSFW 模式）：正面提示词自动添加 `nsfw`，负面提示词自动添加 `sfw`

### 其他

```
/ynai model
/ynai help
```

## 目录结构

```text
astrbot_plugin_yesnai/
├── main.py              # AstrBot 插件入口与指令
├── yesnai_client.py     # YesNAI API 客户端
├── _conf_schema.json    # 插件配置 Schema
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 依赖
└── README.md
```

## 参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [YesNovelAI API Reference](https://nai.rinko.ai/api-reference)
