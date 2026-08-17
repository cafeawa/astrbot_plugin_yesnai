# astrbot_plugin_yesnai

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，用于调用 [YesNovelAI / YesNAI](https://nai.rinko.ai/api-reference) API 生成 NovelAI 风格图片。

## 当前状态

- [x] 初始化 AstrBot 插件骨架
- [x] 配置项：API 地址、API Token、默认模型、超时时间
- [ ] 调用 `POST /v1/nai/generate-image` 生图
- [ ] 支持模型列表 `GET /v1/models`
- [ ] 支持参数：尺寸、steps、sampler、scale、seed、negative_prompt
- [ ] 支持多图 `n_samples` 和图片发送
- [ ] 可选的生图前报价 `POST /ai/generate-image/quote`

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

## 使用

插件加载后发送：

```
/nai <提示词>
```

> 当前为开发中骨架，生图逻辑尚未实现。

## 参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [YesNovelAI API Reference](https://nai.rinko.ai/api-reference)
