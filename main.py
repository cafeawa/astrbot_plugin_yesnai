"""AstrBot 插件：YesNAI 生图。

功能：
- /nai <prompt> 直接生图
- /nai -t <描述> 或 /nai --translate <描述> 使用 AstrBot 当前 LLM 翻译为 Danbooru Tag 后生图
- /artist 管理画师串，生图时自动拼接到提示词前面
- /preset 查看/设置预设正反提示词
- /nsfw 查看/切换 NSFW 开关（on/off 仅管理员）
- /models 查看可用模型
- /quote 生图前报价
- /tags 使用 API 的 suggest-tags 工具

管理员设置：画师串增删、预设提示词修改、NSFW 开关仅管理员可操作。
"""

from __future__ import annotations

import base64
import shlex
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from yesnai_client import YesNAIClient, YesNAIError

_COMMAND_NAMES = {
    "nai",
    "models",
    "quote",
    "artist",
    "preset",
    "tags",
    "nsfw",
}

_NSFW_KEYWORDS = (
    "nsfw",
    "nude",
    "nudity",
    "naked",
    "sex",
    "sexual",
    "erotic",
    "explicit",
    "hentai",
    "porn",
    "porno",
    "pornographic",
    "xxx",
)


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
        logger.info("YesNAI 生图插件已初始化")
        if not self.config.get("api_token"):
            logger.warning("YesNAI 插件尚未配置 API Token，请到插件配置中填写")

    async def terminate(self):
        logger.info("YesNAI 生图插件已卸载")

    # ---------------------------------------------------------------
    # 工具方法
    # ---------------------------------------------------------------
    def _command_args(self, event: AstrMessageEvent) -> str:
        """从事件文本中提取命令参数（兼容 message_str 含/不含命令前缀的情况）。"""
        text = (event.message_str or "").strip()
        parts = text.split(maxsplit=1)
        if parts and parts[0].lstrip("/").lower() in _COMMAND_NAMES:
            return parts[1].strip() if len(parts) > 1 else ""
        return text

    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    @staticmethod
    def _nsfw_blocked(text: str) -> bool:
        lower = text.lower()
        return any(keyword in lower for keyword in _NSFW_KEYWORDS)

    def _nsfw_check(self, text: str) -> str | None:
        """NSFW 关闭时返回拦截文案；开启或未命中返回 None。"""
        if self.config.get("nsfw_enabled", False):
            return None
        if self._nsfw_blocked(text):
            return (
                "当前未开启 NSFW，已拦截该请求。\n"
                "如确需生成，请联系管理员发送 /nsfw on 开启。"
            )
        return None

    def _get_client(self) -> YesNAIClient:
        return YesNAIClient(
            api_base=str(self.config.get("api_base", "https://nai.rinko.ai")),
            api_token=str(self.config.get("api_token", "")),
            timeout=int(self.config.get("timeout", 120) or 120),
        )

    @staticmethod
    def _parse_options(text: str) -> tuple[dict[str, Any], str]:
        """解析 /nai 后面的命令行参数。

        支持：
        -t / --translate
        --model, --size, --steps, --scale, --seed, --n, --sampler, --negative
        返回 (options, 去除参数后的提示词)。
        """
        try:
            tokens = shlex.split(text, posix=True)
        except ValueError:
            tokens = text.split()

        options: dict[str, Any] = {}
        prompt_parts: list[str] = []
        single_value_flags = {
            "--model": "model",
            "--size": "size",
            "--steps": "steps",
            "--scale": "scale",
            "--seed": "seed",
            "--n": "n",
            "--sampler": "sampler",
            "--negative": "negative",
        }
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-t", "--translate"):
                options["translate"] = True
            elif tok.startswith("--") and "=" in tok:
                key, value = tok[2:].split("=", 1)
                options[key] = value
            elif tok in single_value_flags:
                key = single_value_flags[tok]
                if i + 1 < len(tokens):
                    options[key] = tokens[i + 1]
                    i += 1
            else:
                prompt_parts.append(tok)
            i += 1

        return options, " ".join(prompt_parts).strip()

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int] | None:
        size = size.strip().lower().replace("×", "x")
        sep = "x" if "x" in size else ("," if "," in size else None)
        if not sep:
            return None
        try:
            w, h = size.split(sep, 1)
            return int(w), int(h)
        except ValueError:
            return None

    def _build_parameters(self, options: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        size = options.get("size")
        if size:
            parsed = self._parse_size(str(size))
            if parsed:
                params["width"], params["height"] = parsed
        if options.get("steps"):
            try:
                params["steps"] = int(options["steps"])
            except ValueError:
                pass
        if options.get("scale"):
            try:
                params["scale"] = float(options["scale"])
            except ValueError:
                pass
        if options.get("seed"):
            try:
                params["seed"] = int(options["seed"])
            except ValueError:
                pass
        if options.get("n") or options.get("n_samples"):
            try:
                params["n_samples"] = int(options.get("n") or options["n_samples"])
            except ValueError:
                pass
        if options.get("sampler"):
            params["sampler"] = str(options["sampler"])
        return params

    def _compose_negative_prompt(self, options: dict[str, Any]) -> str:
        preset = str(self.config.get("preset_negative_prompt", "")).strip()
        extra = str(options.get("negative", "")).strip()
        if preset and extra:
            return f"{preset}, {extra}"
        return preset or extra

    async def _get_selected_artist_prompt(self, event: AstrMessageEvent) -> str:
        name = await self.get_kv_data(f"artist:{event.unified_msg_origin}", None)
        if not name:
            return ""
        for artist in self.config.get("artists", []) or []:
            if artist.get("name") == name:
                return str(artist.get("prompt", "")).strip()
        return ""

    def _find_artist(self, name: str) -> dict[str, Any] | None:
        for artist in self.config.get("artists", []) or []:
            if artist.get("name") == name:
                return artist
        return None

    def _compose_positive_prompt(self, prompt: str, artist_prompt: str = "") -> str:
        parts: list[str] = []
        if artist_prompt:
            parts.append(artist_prompt)
        preset = str(self.config.get("preset_positive_prompt", "")).strip()
        if preset:
            parts.append(preset)
        if prompt:
            parts.append(prompt)
        return ", ".join(parts)

    async def _translate_to_tags(
        self, event: AstrMessageEvent, text: str
    ) -> tuple[bool, str]:
        """使用 AstrBot 当前会话的 LLM 把自然语言描述翻译成 Danbooru Tag。"""
        if not self.config.get("llm_translation_enabled", True):
            return False, "LLM 翻译 Tag 功能未启用，请在插件配置中开启 llm_translation_enabled"

        try:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
        except Exception as exc:
            logger.error(f"获取当前 LLM Provider 失败: {exc}")
            return False, f"获取当前 LLM Provider 失败: {exc}"

        if not provider_id:
            return False, "当前会话没有可用的 LLM Provider，无法翻译 Tag"

        system_prompt = str(
            self.config.get(
                "llm_system_prompt",
                "You are a Danbooru tag translator. Convert the user's Chinese or English "
                "description into a comma-separated list of English Danbooru tags for image "
                "generation. Return only tags, no explanations, no markdown, no code fences.",
            )
        )

        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=text,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.error(f"LLM 翻译失败: {exc}")
            return False, f"LLM 翻译失败: {exc}"

        tags = (resp.completion_text or "").strip()
        if not tags:
            return False, "LLM 返回为空"
        return True, tags

    def _data_dir(self) -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = Path(get_astrbot_data_path())
        except Exception:
            base = Path("data")
        path = base / "plugin_data" / "astrbot_plugin_yesnai"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_images(self, images: list[str], image_format: str = "png") -> list[Path]:
        data_dir = self._data_dir()
        paths: list[Path] = []
        for b64 in images:
            raw = base64.b64decode(b64)
            path = data_dir / f"yesnai_{uuid.uuid4().hex}.{image_format}"
            path.write_bytes(raw)
            paths.append(path)
        return paths

    def _quote_payload(
        self,
        prompt: str,
        options: dict[str, Any],
        artist_prompt: str = "",
    ) -> dict[str, Any]:
        model = str(
            options.get("model")
            or self.config.get("default_model", "nai-diffusion-4-5-full")
        )
        final_prompt = self._compose_positive_prompt(prompt, artist_prompt)
        params = self._build_parameters(options)
        negative = self._compose_negative_prompt(options)

        payload: dict[str, Any] = {
            "model": model,
            "action": "generate",
            "input": final_prompt,
        }
        if params.get("width") is not None:
            payload["width"] = params["width"]
        if params.get("height") is not None:
            payload["height"] = params["height"]
        if params.get("steps") is not None:
            payload["steps"] = params["steps"]
        if params.get("n_samples") is not None:
            payload["n_samples"] = params["n_samples"]
        if negative:
            payload["negative_prompt"] = negative
        return payload

    # ---------------------------------------------------------------
    # 指令
    # ---------------------------------------------------------------
    @filter.command("nai")
    async def nai(self, event: AstrMessageEvent):
        """YesNAI 生图：/nai <prompt> 或 /nai -t <描述>"""
        args = self._command_args(event)
        if not args:
            yield event.plain_result(
                "用法：\n"
                "/nai <提示词>\n"
                "/nai -t <中文描述>  # 先用 LLM 翻译成 Tag 再生图\n"
                "可选参数：--model, --size 832x1216, --steps, --scale, "
                "--seed, --n, --sampler, --negative=\"lowres, bad hands\""
            )
            return

        options, prompt = self._parse_options(args)

        blocked = self._nsfw_check(prompt)
        if blocked:
            yield event.plain_result(blocked)
            return

        if options.get("translate"):
            if not prompt:
                yield event.plain_result("请提供要翻译的描述，例如：/nai -t 一个女孩在图书馆")
                return
            yield event.plain_result("正在用 LLM 翻译 Tag...")
            ok, translated = await self._translate_to_tags(event, prompt)
            if not ok:
                yield event.plain_result(translated)
                return
            blocked = self._nsfw_check(translated)
            if blocked:
                yield event.plain_result(blocked)
                return
            prompt = translated

        if not prompt:
            yield event.plain_result("请提供提示词")
            return

        try:
            artist_prompt = await self._get_selected_artist_prompt(event)
            final_prompt = self._compose_positive_prompt(prompt, artist_prompt)
            negative = self._compose_negative_prompt(options)
            params = self._build_parameters(options)
            if negative:
                params["negative_prompt"] = negative

            model = str(
                options.get("model")
                or self.config.get("default_model", "nai-diffusion-4-5-full")
            )

            yield event.plain_result(
                f"正在生成...\n模型: {model}\nPrompt: {final_prompt[:200]}"
            )

            client = self._get_client()
            resp = await client.generate_image(
                model=model,
                input_text=final_prompt,
                parameters=params,
            )
            images = resp.get("images") or []
            if not images:
                yield event.plain_result(f"生成失败或没有返回图片：{resp}")
                return

            fmt = str(resp.get("image_format", "png"))
            paths = self._save_images(images, fmt)
            job = resp.get("job", {})
            cost = job.get("cost_gems", "?")
            yield event.plain_result(f"生成完成，消耗 {cost} Gems")
            for path in paths:
                yield event.image_result(str(path))
        except YesNAIError as exc:
            logger.error(f"YesNAI 生图失败: {exc}")
            yield event.plain_result(f"生图失败: {exc}")
        except Exception as exc:
            logger.exception("YesNAI 生图出现未预期错误")
            yield event.plain_result(f"生图失败: {exc}")

    @filter.command("models")
    async def models(self, event: AstrMessageEvent):
        """查看可用模型列表"""
        try:
            client = self._get_client()
            models = await client.get_models()
            if not models:
                yield event.plain_result("没有获取到模型")
                return
            text = "可用模型：\n" + "\n".join(
                str(m.get("id", "")) for m in models
            )
            yield event.plain_result(text)
        except Exception as exc:
            logger.error(f"获取模型列表失败: {exc}")
            yield event.plain_result(f"获取模型列表失败: {exc}")

    @filter.command("quote")
    async def quote(self, event: AstrMessageEvent):
        """生图前报价：/quote <prompt> [--model ...] [--size ...] ..."""
        args = self._command_args(event)
        if not args:
            yield event.plain_result(
                "用法：/quote <提示词> [--model 模型] [--size 832x1216] "
                "[--steps 28] [--n 1]"
            )
            return

        options, prompt = self._parse_options(args)
        if not prompt:
            yield event.plain_result("请提供提示词")
            return

        try:
            artist_prompt = await self._get_selected_artist_prompt(event)
            payload = self._quote_payload(prompt, options, artist_prompt)
            client = self._get_client()
            resp = await client.quote(payload)

            lines = [
                f"模型: {resp.get('model', '?')}",
                f"Pricing mode: {resp.get('pricing_mode', '?')}",
                f"Estimated Anlas: {resp.get('estimated_anlas', '?')}",
                f"Total Gems: {resp.get('total_gems', '?')}",
                f"Balance Gems: {resp.get('balance_gems', '?')}",
            ]
            if resp.get("base_gems") is not None:
                lines.append(f"Base Gems: {resp.get('base_gems')}")
            yield event.plain_result("报价（不扣费）：\n" + "\n".join(lines))
        except Exception as exc:
            logger.error(f"报价失败: {exc}")
            yield event.plain_result(f"报价失败: {exc}")

    @filter.command("artist")
    async def artist(self, event: AstrMessageEvent):
        """管理画师串：/artist list|set|add|del|clear"""
        args = self._command_args(event)
        parts = args.split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action in ("list", "ls", ""):
            artists = self.config.get("artists", []) or []
            if not artists:
                yield event.plain_result(
                    "还没有画师串。\n添加：/artist add <名称> <画师串>\n"
                    "选择：/artist set <名称>"
                )
                return
            selected = await self.get_kv_data(
                f"artist:{event.unified_msg_origin}", None
            )
            lines = []
            for artist_item in artists:
                name = str(artist_item.get("name", "?"))
                mark = "✔" if name == selected else " "
                prompt = str(artist_item.get("prompt", ""))[:60]
                lines.append(f"{mark} {name}: {prompt}")
            yield event.plain_result("画师串列表：\n" + "\n".join(lines))

        elif action == "set":
            if not rest:
                yield event.plain_result("用法：/artist set <名称>")
                return
            name = rest.strip()
            if not self._find_artist(name):
                yield event.plain_result(f"没有找到画师串：{name}")
                return
            await self.put_kv_data(f"artist:{event.unified_msg_origin}", name)
            yield event.plain_result(f"已选择画师串：{name}（生图时自动拼接到提示词前面）")

        elif action == "clear":
            await self.delete_kv_data(f"artist:{event.unified_msg_origin}")
            yield event.plain_result("已清除当前画师串选择")

        elif action == "add":
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以添加/修改画师串")
                return
            add_parts = rest.split(maxsplit=1)
            if len(add_parts) < 2:
                yield event.plain_result("用法：/artist add <名称> <画师串>")
                return
            name, prompt = add_parts[0], add_parts[1].strip()
            if not name or not prompt:
                yield event.plain_result("名称和画师串都不能为空")
                return
            artists = self.config.get("artists", []) or []
            found = False
            for artist_item in artists:
                if artist_item.get("name") == name:
                    artist_item["prompt"] = prompt
                    found = True
                    break
            if not found:
                artists.append(
                    {"__template_key": "artist", "name": name, "prompt": prompt}
                )
            self.config["artists"] = artists
            self.config.save_config()
            yield event.plain_result(f"已保存画师串：{name}")

        elif action == "del":
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以删除画师串")
                return
            if not rest:
                yield event.plain_result("用法：/artist del <名称>")
                return
            name = rest.strip()
            artists = self.config.get("artists", []) or []
            new_artists = [
                a for a in artists if a.get("name") != name
            ]
            if len(new_artists) == len(artists):
                yield event.plain_result(f"没有找到画师串：{name}")
                return
            self.config["artists"] = new_artists
            self.config.save_config()
            yield event.plain_result(f"已删除画师串：{name}")

        else:
            yield event.plain_result("未知子命令。可用：list / set / add / del / clear")

    @filter.command("preset")
    async def preset(self, event: AstrMessageEvent):
        """查看/设置预设正反提示词：/preset show|positive|negative"""
        args = self._command_args(event)
        parts = args.split(maxsplit=1)
        action = parts[0].lower() if parts else "show"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action in ("show", ""):
            positive = str(self.config.get("preset_positive_prompt", ""))
            negative = str(self.config.get("preset_negative_prompt", ""))
            yield event.plain_result(
                "当前预设：\n"
                f"正面：{positive}\n"
                f"负面：{negative}\n\n"
                "设置：/preset positive <内容> 或 /preset negative <内容>"
            )

        elif action in ("positive", "pos"):
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以修改预设提示词")
                return
            if not rest:
                yield event.plain_result("用法：/preset positive <提示词>")
                return
            self.config["preset_positive_prompt"] = rest
            self.config.save_config()
            yield event.plain_result("已设置预设正面提示词")

        elif action in ("negative", "neg"):
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以修改预设提示词")
                return
            if not rest:
                yield event.plain_result("用法：/preset negative <提示词>")
                return
            self.config["preset_negative_prompt"] = rest
            self.config.save_config()
            yield event.plain_result("已设置预设负面提示词")

        else:
            yield event.plain_result("未知子命令。可用：show / positive / negative")

    @filter.command("nsfw")
    async def nsfw(self, event: AstrMessageEvent):
        """NSFW 开关：/nsfw on|off|status（on/off 仅管理员）"""
        args = self._command_args(event)
        action = args.strip().lower() if args.strip() else "status"

        if action == "status":
            state = "开启" if self.config.get("nsfw_enabled", False) else "关闭"
            yield event.plain_result(f"当前 NSFW 开关：{state}")
            return

        if action not in ("on", "off"):
            yield event.plain_result("用法：/nsfw on|off|status")
            return

        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以修改 NSFW 开关")
            return

        self.config["nsfw_enabled"] = action == "on"
        self.config.save_config()
        yield event.plain_result(f"NSFW 开关已{'开启' if action == 'on' else '关闭'}")

    @filter.command("tags")
    async def tags(self, event: AstrMessageEvent):
        """使用 YesNAI suggest-tags 工具获取推荐 Tag"""
        args = self._command_args(event)
        if not args:
            yield event.plain_result("用法：/tags <描述> [--model 模型]")
            return

        options, prompt = self._parse_options(args)
        if not prompt:
            yield event.plain_result("请提供描述")
            return

        try:
            model = str(
                options.get("model")
                or self.config.get("default_model", "nai-diffusion-4-5-full")
            )
            client = self._get_client()
            data = await client.suggest_tags(prompt, model)
            tags = data.get("tags", [])
            if not tags:
                yield event.plain_result("没有获取到推荐 Tag")
                return
            text = "推荐 Tags：\n" + ", ".join(
                str(t.get("tag", "")) for t in tags
            )
            yield event.plain_result(text)
        except Exception as exc:
            logger.error(f"获取 Tag 失败: {exc}")
            yield event.plain_result(f"获取 Tag 失败: {exc}")
