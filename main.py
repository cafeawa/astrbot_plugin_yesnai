from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_yesnai",
    "cafe_awa_",
    "调用 YesNovelAI / YesNAI API 生成图像",
    "0.1.0",
)
class YesNAIPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def initialize(self):
        """插件初始化时调用，可在这里预加载模型列表或校验配置。"""
        logger.info("YesNAI 生图插件已初始化")
        # TODO: 可调用 GET /v1/models 拉取可用模型，并缓存到 self 中。
        # TODO: 可校验 self.config.api_token 是否已填写。

    @filter.command("nai")
    async def nai(self, event: AstrMessageEvent):
        """YesNAI 生图命令：生成 NovelAI 风格图片（开发中）"""
        # TODO: 解析用户参数（prompt、模型、尺寸、steps 等）。
        # TODO: 调用 POST /v1/nai/generate-image。
        # TODO: 把返回的 Base64 图片保存到 data/ 目录或直接发送。
        yield event.plain_result("YesNAI 生图插件已加载，生图功能开发中。")

    async def terminate(self):
        """插件卸载/停用时调用。"""
        logger.info("YesNAI 生图插件已卸载")
