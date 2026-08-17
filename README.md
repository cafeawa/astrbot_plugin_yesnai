# astrbot_plugin_yesnai

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，用于调用 [YesNovelAI / YesNAI](https://nai.rinko.ai/api-reference) API 生成 NovelAI 风格图片。

## 功能

- ✅ `/nai <提示词>` 直接生图
- ✅ `/nai -t <描述>` / `/nai --translate <描述>` 使用 AstrBot 当前会话 LLM 翻译成 Danbooru Tag 后生图
- ✅ `/artist` 管理画师串，生图时自动拼接到提示词前面
- ✅ `/preset` 查看/设置预设正反提示词
- ✅ `/models` 查看可用模型
- ✅ `/quote` 生图前报价
- ✅ `/tags` 使用 API 的 suggest-tags 工具
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
| `timeout` | 请求超时时间（秒），默认 `120` |
| `preset_positive_prompt` | 预设正面提示词，生成时自动拼接到用户提示词前面 |
| `preset_negative_prompt` | 预设负面提示词，生成时作为 `negative_prompt` |
| `artists` | 画师串预设列表（也可用 `/artist add` 添加） |
| `llm_translation_enabled` | 是否启用 LLM 翻译 Tag 功能 |
| `llm_system_prompt` | LLM 翻译 Tag 的系统提示词 |

## 使用

### 直接生图

```
/nai 1girl, blue sky, masterpiece
```

### LLM 翻译 Tag 后生图

```
/nai -t 一个女孩在图书馆里看书
/nai --translate 夕阳下的海边，一个少年在奔跑
```

### 可选生图参数

```
/nai 1girl --model nai-diffusion-4-5-full --size 832x1216 --steps 28 --scale 5 --seed 123456789 --n 2 --sampler k_euler_ancestral --negative="lowres, bad hands"
```

### 画师串

```
/artist list
/artist add my_artist artist_name, artist_style
/artist set my_artist
/artist clear
/artist del my_artist
```

选择画师串后，生成时会自动把它拼接到最终提示词**前面**：

```
画师串, 预设正面提示词, 用户提示词
```

### 预设正反提示词

```
/preset show
/preset positive masterpiece, best quality, absurdres
/preset negative lowres, bad anatomy, bad hands
```

### 其他

```
/models
/quote 1girl --size 832x1216 --steps 28
/tags 一个女孩
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
