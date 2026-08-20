"""AstrBot 插件：YesNAI 生图。

命令：
- /ynai <描述>             默认使用 LLM 翻译成 Danbooru Tag 后生图
- /ynai0 <提示词>          直接生图（不做 LLM 翻译）
- /ynai model              查看可用模型
- /ynai artist             管理画师串
- /ynai preset             查看/设置预设正反提示词（仅管理员）
- /ynai nsfw               查看/切换 NSFW 开关（按会话）

管理员设置：画师串增删、预设命令、NSFW 开关仅管理员可操作。
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import sys
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

# AstrBot 加载 main.py 时不一定把插件目录加入 sys.path，
# 这里手动加入，确保同目录的 yesnai_client.py 可以被导入。
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from yesnai_client import YesNAIClient, YesNAIError

_COMMAND_NAMES = {
    "ynai",
    "ynai0",
}


@register(
    "astrbot_plugin_yesnai",
    "cafe_awa_",
    "调用 YesNovelAI / YesNAI API 生成图像",
    "0.9.2",
)
class YesNAIPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def initialize(self):
        logger.info("YesNAI 生图插件已初始化")
        self._migrate_artists_storage()
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
    def _ensure_tag(text: str, tag: str) -> str:
        """把 tag 追加到逗号分隔的提示词中，已存在则不重复添加。"""
        if not text:
            return tag
        tags = [t.strip() for t in text.split(",") if t.strip()]
        if tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
        return ", ".join(tags)

    def _apply_nsfw_positive(self, prompt: str, nsfw_enabled: bool | None = None) -> str:
        """根据 NSFW 开关在正面提示词中添加 sfw；开启时不注入。"""
        enabled = (
            bool(self.config.get("nsfw_enabled", False))
            if nsfw_enabled is None
            else nsfw_enabled
        )
        if enabled:
            return prompt
        return self._ensure_tag(prompt, "sfw")

    def _apply_nsfw_negative(self, prompt: str, nsfw_enabled: bool | None = None) -> str:
        """根据 NSFW 开关在负面提示词中添加 nsfw；开启时不注入。"""
        enabled = (
            bool(self.config.get("nsfw_enabled", False))
            if nsfw_enabled is None
            else nsfw_enabled
        )
        if enabled:
            return prompt
        return self._ensure_tag(prompt, "nsfw")

    async def _get_nsfw_enabled(self, event: AstrMessageEvent) -> bool:
        """获取当前会话的 NSFW 状态；会话未单独设置时回退到全局配置。"""
        try:
            value = await self.get_kv_data(
                f"nsfw:{event.unified_msg_origin}", None
            )
            if value is not None:
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            pass
        return bool(self.config.get("nsfw_enabled", False))

    def _get_client(self) -> YesNAIClient:
        return YesNAIClient(
            api_base=str(self.config.get("api_base", "https://nai.rinko.ai")),
            api_token=str(self.config.get("api_token", "")),
            timeout=int(self.config.get("timeout", 600) or 600),
        )

    async def _check_paid_quote(
        self,
        client: YesNAIClient,
        model: str,
        action: str,
        input_text: str,
        params: dict[str, Any],
        options: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """非免费请求先查询报价；返回 (是否继续, 需要展示的消息)。"""
        if not self.config.get("confirm_paid_requests", False) or options.get("yes"):
            return True, None
        try:
            # 报价接口使用与生成请求相同的参数，但放在顶层（与报价响应字段一致）
            quote_payload: dict[str, Any] = {
                "model": model,
                "action": action,
                "input": input_text,
            }
            quote_payload.update(params)
            quote = await client.quote(quote_payload)
        except Exception as exc:
            return False, f"报价查询失败，已取消生成：{exc}"

        total_gems = 0
        balance_gems = None
        if isinstance(quote, dict):
            try:
                total_gems = int(quote.get("total_gems") or 0)
            except (TypeError, ValueError):
                total_gems = 0
            balance_gems = quote.get("balance_gems")

        if total_gems > 0:
            balance_text = (
                f"，当前余额 {balance_gems} Gems"
                if balance_gems is not None
                else ""
            )
            return False, (
                f"该请求预计消耗 {total_gems} Gems{balance_text}。"
                "如确认请重新发送命令并加上 -y/--yes"
            )
        return True, None

    @staticmethod
    def _parse_options(text: str) -> tuple[dict[str, Any], str]:
        """解析 /ynai 后面的命令行参数。

        支持：
        -t / --translate
        -nt / --no-translate
        -b / --both
        -s / --style
        -y / --yes
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
            "--strength": "strength",
            "--noise": "noise",
        }
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-t", "--translate"):
                options["translate"] = True
            elif tok in ("-nt", "--no-translate"):
                options["translate"] = False
            elif tok in ("-b", "--both"):
                options["both"] = True
            elif tok in ("-s", "--style"):
                options["style"] = True
            elif tok in ("-y", "--yes"):
                options["yes"] = True
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
        else:
            default_sampler = str(
                self.config.get("default_sampler", "") or ""
            ).strip()
            if default_sampler:
                params["sampler"] = default_sampler

        # YesNAI API 强制要求这些字段，用户未指定时使用插件配置里的默认值
        params.setdefault("width", int(self.config.get("default_width", 832) or 832))
        params.setdefault("height", int(self.config.get("default_height", 1216) or 1216))
        params.setdefault("steps", int(self.config.get("default_steps", 28) or 28))
        params.setdefault("n_samples", int(self.config.get("default_n_samples", 1) or 1))
        return params

    @staticmethod
    def _apply_v4_defaults(
        params: dict[str, Any],
        positive: str,
        negative: str,
    ) -> None:
        """按 NovelAI V4 模型补全兼容参数。"""
        params.setdefault("params_version", 3)
        params.setdefault("use_coords", False)
        params.setdefault("legacy_v3_extend", False)
        params.setdefault("legacy_uc", False)
        params.setdefault("dynamic_thresholding", False)
        params.setdefault("controlnet_strength", 1.0)
        params.setdefault("normalize_reference_strength_multiple", True)
        params.setdefault("deliberate_euler_ancestral_bug", False)
        params.setdefault("prefer_brownian", True)
        params.setdefault("v4_prompt", {
            "caption": {
                "base_caption": positive,
                "char_captions": [],
            },
            "use_coords": False,
            "use_order": True,
        })
        params.setdefault("v4_negative_prompt", {
            "caption": {
                "base_caption": negative or "",
                "char_captions": [],
            },
            "legacy_uc": False,
        })

    def _compose_negative_prompt(
        self, options: dict[str, Any], nsfw_enabled: bool | None = None
    ) -> str:
        preset = str(self.config.get("preset_negative_prompt", "")).strip()
        extra = str(options.get("negative", "")).strip()
        if preset and extra:
            result = f"{preset}, {extra}"
        else:
            result = preset or extra
        return self._apply_nsfw_negative(result, nsfw_enabled)

    async def _get_selected_artist_prompt(self, event: AstrMessageEvent) -> str:
        artist_id = await self.get_kv_data(f"artist:{event.unified_msg_origin}", None)
        if not artist_id:
            return ""
        artist = self._find_artist(artist_id)
        if artist:
            return str(artist.get("prompt", "")).strip()
        return ""

    def _artists_to_storage(self, artists: list[dict[str, Any]]) -> list[str]:
        """把画师串列表保存为 WebUI 可编辑的字符串列表：名称||画师串。"""
        return [
            f"{a.get('name', '')}||{a.get('prompt', '')}" for a in artists
        ]

    def _migrate_artists_storage(self) -> None:
        """把旧的 dict 列表迁移成字符串列表，保证 WebUI 的 list 编辑器可用。"""
        raw = self.config.get("artists", []) or []
        if any(isinstance(item, dict) for item in raw):
            artists = self._normalize_artists()
            self.config["artists"] = self._artists_to_storage(artists)
            try:
                self.config.save_config()
            except Exception:
                pass

    def _normalize_artists(self) -> list[dict[str, Any]]:
        """解析配置中的画师串（支持字符串列表和旧 dict 列表），自动生成数字 id。"""
        raw = self.config.get("artists", []) or []
        artists: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                artists.append(
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "prompt": str(item.get("prompt") or ""),
                    }
                )
            else:
                text = str(item)
                if "||" in text:
                    name, prompt = text.split("||", 1)
                else:
                    name, prompt = text, text
                artists.append(
                    {
                        "id": "",
                        "name": name.strip(),
                        "prompt": prompt.strip(),
                    }
                )

        max_id = 0
        for artist in artists:
            if artist["id"].strip().isdigit():
                max_id = max(max_id, int(artist["id"].strip()))
        next_id = max_id + 1
        for artist in artists:
            if not artist["id"].strip():
                artist["id"] = str(next_id)
                next_id += 1
        return artists

    def _find_artist(self, artist_id: str) -> dict[str, Any] | None:
        artists = self._normalize_artists()
        for artist in artists:
            # 兼容旧数据：没有 id 时用 name 当作 id
            aid = str(artist.get("id") or artist.get("name") or "")
            if aid == artist_id or artist.get("name") == artist_id:
                return artist
        return None

    async def _get_referenced_image(self, event: AstrMessageEvent):
        """从当前消息或引用消息中提取第一张图片组件。"""
        try:
            from astrbot.api.message_components import Image
        except Exception:
            return None

        def is_component(comp, kind: str) -> bool:
            """判断消息组件类型，兼容 ComponentType 枚举和字符串。"""
            ctype = getattr(comp, "type", None)
            if ctype is None:
                return False
            raw = getattr(ctype, "value", ctype)
            return str(raw).lower() == kind.lower() or str(ctype).lower() == (
                f"componenttype.{kind.lower()}"
            )

        def find_image(components) -> Any | None:
            for comp in components or []:
                if isinstance(comp, Image) or is_component(comp, "image"):
                    return comp
                if is_component(comp, "reply"):
                    nested = find_image(getattr(comp, "chain", None))
                    if nested:
                        return nested
            return None

        try:
            return find_image(event.get_messages())
        except Exception:
            return None

    def _compose_positive_prompt(
        self,
        prompt: str,
        artist_prompt: str = "",
        nsfw_enabled: bool | None = None,
    ) -> str:
        parts: list[str] = []
        if artist_prompt:
            parts.append(artist_prompt)
        preset = str(self.config.get("preset_positive_prompt", "")).strip()
        if preset:
            parts.append(preset)
        if prompt:
            parts.append(prompt)
        return self._apply_nsfw_positive(", ".join(parts), nsfw_enabled)

    async def _translate_to_tags(
        self, event: AstrMessageEvent, text: str
    ) -> tuple[bool, str]:
        """使用翻译 LLM 把自然语言描述翻译成 Danbooru Tag。"""
        if not self.config.get("llm_translation_enabled", True):
            return False, "LLM 翻译 Tag 功能未启用，请在插件配置中开启 llm_translation_enabled"

        configured_llm = str(self.config.get("translation_llm", "") or "").strip()
        if configured_llm:
            provider_id = configured_llm
        else:
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
            self.config.get("translation_prompt")
            or self.config.get("llm_system_prompt")
            or "You are a Danbooru tag translator. Convert the user's Chinese or English "
            "description into a comma-separated list of English Danbooru tags for image "
            "generation. Return only tags, no explanations, no markdown, no code fences."
        )

        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=text,
                system_prompt=system_prompt,
            )
            if resp is None:
                return False, "LLM 返回为空或响应格式不正确"
            completion_text = getattr(resp, "completion_text", None)
            if completion_text is None:
                return False, "LLM 返回为空或响应格式不正确"
            if not isinstance(completion_text, str):
                logger.error(
                    f"LLM 翻译响应格式不正确: {type(completion_text).__name__}"
                )
                return False, "LLM 翻译响应格式不正确"
            tags = completion_text.strip()
        except Exception as exc:
            logger.error(f"LLM 翻译失败: {exc}")
            return False, f"LLM 翻译失败: {exc}"

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

    @staticmethod
    def _normalize_reference_image(base64_image: str) -> str:
        """把角色参考图按 NovelAI SDK 规则归一化为 1024x1536 黑边 PNG。"""
        try:
            import io

            from PIL import Image as PILImage

            # 兼容 data URI，例如 data:image/png;base64,xxxx
            if base64_image.startswith("data:") and "," in base64_image:
                base64_image = base64_image.split(",", 1)[1]
            raw = base64.b64decode(base64_image, validate=True)
            with PILImage.open(io.BytesIO(raw)) as source:
                source.load()
                image = source.convert("RGB")
        except Exception as exc:
            raise YesNAIError(
                f"角色参考图预处理失败: {str(exc)[:80]}"
            ) from exc

        try:
            target_width, target_height = 1024, 1536
            source_width, source_height = image.size
            source_ratio = source_width / source_height
            target_ratio = target_width / target_height
            if source_ratio > target_ratio:
                resized_width = target_width
                resized_height = max(1, int(target_width / source_ratio))
            else:
                resized_height = target_height
                resized_width = max(1, int(target_height * source_ratio))

            resized = image.resize(
                (resized_width, resized_height),
                PILImage.Resampling.LANCZOS,
            )
            canvas = PILImage.new("RGB", (target_width, target_height), (0, 0, 0))
            canvas.paste(
                resized,
                (
                    (target_width - resized_width) // 2,
                    (target_height - resized_height) // 2,
                ),
            )
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:
            raise YesNAIError(
                f"角色参考图预处理失败: {str(exc)[:80]}"
            ) from exc
        finally:
            try:
                image.close()
            except Exception:
                pass

    def _save_images(self, images: list[str], image_format: str = "png") -> list[Path]:
        data_dir = self._data_dir()
        paths: list[Path] = []
        for b64 in images:
            raw = base64.b64decode(b64)
            if not raw:
                raise ValueError("图片数据为空")
            path = data_dir / f"yesnai_{uuid.uuid4().hex}.{image_format}"
            path.write_bytes(raw)
            paths.append(path)
        return paths

    @staticmethod
    def _extract_generated_images(
        resp: dict[str, Any],
    ) -> tuple[list[str], str]:
        """从生图响应中安全提取 base64 图片列表和格式。

        响应结构不对、缺少图片、或图片数据非法时抛出 YesNAIError，
        并尽量带上 API 返回的错误信息。
        """
        if not isinstance(resp, dict):
            raise YesNAIError("生图接口返回的数据格式不正确")

        def server_message() -> str:
            for key in ("error", "detail", "message", "msg"):
                value = resp.get(key)
                if value:
                    if isinstance(value, dict):
                        value = (
                            value.get("message")
                            or value.get("msg")
                            or value.get("detail")
                            or value
                        )
                    return str(value)
            if resp.get("status") in ("error", "failed", "fail", False, "false"):
                return "API 返回错误状态"
            return ""

        images = resp.get("images")
        if not isinstance(images, list):
            raise YesNAIError(server_message() or "生图接口返回的 images 字段不是列表")
        if not images:
            raise YesNAIError(server_message() or "生图接口未返回图片")

        cleaned: list[str] = []
        for item in images:
            if not isinstance(item, str):
                raise YesNAIError("生图接口返回的图片数据格式不正确")
            # 兼容 JSON 中可能出现的换行/空白、URL-safe base64 和省略 padding
            compact = "".join(item.split()).replace("-", "+").replace("_", "/")
            if not compact:
                raise YesNAIError("生图接口返回的图片数据为空")
            compact += "=" * (-len(compact) % 4)
            try:
                raw = base64.b64decode(compact, validate=True)
            except Exception as exc:
                raise YesNAIError("生图接口返回的图片 base64 数据无效") from exc
            if not raw:
                raise YesNAIError("生图接口返回的图片数据为空")
            cleaned.append(compact)

        fmt = str(resp.get("image_format") or "png")
        if "/" in fmt:
            fmt = fmt.rsplit("/", 1)[-1]
        fmt = "".join(ch for ch in fmt if ch.isalnum()).lower() or "png"
        return cleaned, fmt

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

    @staticmethod
    def _ynai_help_text() -> str:
        return (
            "# YesNAI 生图插件\n\n"
            "## 生图\n"
            "- `/ynai <描述>`：LLM 翻译 Tag 后生图（默认）\n"
            "- `/ynai0 <提示词>`：直接生图\n\n"
            "## 其他命令\n"
            "- `/ynai model`：查看可用模型\n"
            "- `/ynai i2i <描述>`：图重绘（需引用图片，默认 LLM 翻译，可加 `-nt` 跳过）\n"
            "- `/ynai ref <描述>`：参考角色生成（需引用图片，默认 LLM 翻译；`-nt` 跳过翻译、`-s` 纯画风、`-b` 角色+画风）\n"
            "- `/ynai artist list/set/add/del/clear`：管理画师串\n"
            "- `/ynai preset show/positive/negative`：预设正反提示词（管理员）\n"
            "- `/ynai nsfw on/off/status/reset`：NSFW 开关（按会话）\n\n"
            "## 生图可选参数\n"
            "`--model`、`--size 832x1216`、`--steps`、`--scale`、"
            "`--seed`、`--n`、`--sampler`、`--negative=\"lowres, bad hands\"`"
        )

    @staticmethod
    def _ynai_help_html() -> str:
        """自包含的帮助卡片 HTML，不依赖外部 CDN/JS，避免文转图服务渲染空白。"""
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #ffffff;
    color: #333333;
    padding: 28px 32px;
    margin: 0;
  }
  h1 {
    color: #3276dc;
    font-size: 32px;
    margin: 0 0 6px 0;
  }
  h2 {
    color: #3276dc;
    font-size: 22px;
    border-bottom: 2px solid #3276dc;
    padding-bottom: 6px;
    margin: 22px 0 10px 0;
  }
  ul {
    margin: 6px 0;
    padding-left: 22px;
  }
  li {
    font-size: 17px;
    line-height: 1.9;
  }
  code {
    background: #f2f2f2;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: Menlo, Monaco, Consolas, monospace;
    font-size: 15px;
    color: #c7254e;
  }
  .footer {
    margin-top: 24px;
    color: #999999;
    font-size: 13px;
  }
</style>
</head>
<body>
  <h1>YesNAI 生图插件</h1>
  <h2>生图</h2>
  <ul>
    <li><code>/ynai &lt;描述&gt;</code>：LLM 翻译 Tag 后生图（默认）</li>
    <li><code>/ynai0 &lt;提示词&gt;</code>：直接生图</li>
  </ul>
  <h2>其他命令</h2>
  <ul>
    <li><code>/ynai model</code>：查看可用模型</li>
    <li><code>/ynai i2i &lt;描述&gt;</code>：图重绘（需引用图片，默认 LLM 翻译，可加 <code>-nt</code> 跳过）</li>
    <li><code>/ynai ref &lt;描述&gt;</code>：参考角色生成（需引用图片，默认 LLM 翻译；<code>-nt</code> 跳过翻译、<code>-s</code> 纯画风、<code>-b</code> 角色+画风）</li>
    <li><code>/ynai artist list/set/add/del/clear</code>：管理画师串</li>
    <li><code>/ynai preset show/positive/negative</code>：预设正反提示词（管理员）</li>
    <li><code>/ynai nsfw on/off/status/reset</code>：NSFW 开关（按会话）</li>
  </ul>
  <h2>生图可选参数</h2>
  <ul>
    <li><code>--model</code>、<code>--size 832x1216</code>、<code>--steps</code>、<code>--scale</code></li>
    <li><code>--seed</code>、<code>--n</code>、<code>--sampler</code>、<code>--negative="lowres, bad hands"</code></li>
  </ul>
  <div class="footer">AstrBot YesNAI 生图插件帮助卡片</div>
</body>
</html>"""

    async def _render_help_via_t2i(self, t2i_url: str) -> str:
        """使用插件配置的自定义文转图服务地址渲染帮助卡片。"""
        from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy

        renderer = NetworkRenderStrategy(base_url=t2i_url)
        return await renderer.render_custom_template(
            self._ynai_help_html(),
            {},
            return_url=True,
            options={"full_page": True, "type": "png", "quality": 90},
        )

    async def _ynai_help_card(self, event: AstrMessageEvent):
        """把帮助文本渲染成文转图卡片。

        仅使用插件配置的 t2i_url；未配置或失败时不使用 AstrBot 默认文转图，
        直接返回纯文本帮助。
        """
        t2i_url = str(self.config.get("t2i_url", "") or "").strip()
        if not t2i_url:
            yield event.plain_result(self._ynai_help_text())
            return

        try:
            url = await self._render_help_via_t2i(t2i_url)
            yield event.image_result(url)
        except Exception as exc:
            logger.exception(f"自定义文转图服务渲染失败: {exc}")
            yield event.plain_result(self._ynai_help_text())

    async def _run_safely(
        self, event: AstrMessageEvent, generator
    ):
        """消费一个异步生成器，未知异常时记录堆栈并给用户友好提示。"""
        try:
            async for result in generator:
                yield result
        except YesNAIError as exc:
            logger.error(f"YesNAI 命令失败: {exc}")
            yield event.plain_result(f"操作失败: {exc}")
        except Exception as exc:
            logger.exception("YesNAI 命令出现未预期错误")
            yield event.plain_result(f"操作失败: {exc}")

    # ---------------------------------------------------------------
    # 公共生成逻辑
    # ---------------------------------------------------------------
    async def _ynai_generate(
        self, event: AstrMessageEvent, args: str, translate: bool
    ):
        options, prompt = self._parse_options(args)

        if not prompt:
            yield event.plain_result(
                "请提供提示词。\n"
                f"{'/ynai <描述>' if translate else '/ynai0 <提示词>'}"
            )
            return

        if translate:
            if not self.config.get("llm_translation_enabled", True):
                yield event.plain_result(
                    "LLM 翻译 Tag 功能未启用，请管理员在插件配置中开启 "
                    "llm_translation_enabled"
                )
                return
            yield event.plain_result("正在用 LLM 翻译 Tag...")
            try:
                ok, translated = await self._translate_to_tags(event, prompt)
            except Exception as exc:
                logger.exception("LLM 翻译出现未预期错误")
                yield event.plain_result(f"LLM 翻译出现未预期错误: {exc}")
                return
            if not ok:
                yield event.plain_result(translated)
                return
            prompt = translated

        try:
            artist_prompt = await self._get_selected_artist_prompt(event)
            nsfw_enabled = await self._get_nsfw_enabled(event)
            final_prompt = self._compose_positive_prompt(
                prompt, artist_prompt, nsfw_enabled=nsfw_enabled
            )
            negative = self._compose_negative_prompt(options, nsfw_enabled=nsfw_enabled)
            params = self._build_parameters(options)
            if negative:
                params["negative_prompt"] = negative
            self._apply_v4_defaults(params, final_prompt, negative)

            model = str(
                options.get("model")
                or self.config.get("default_model", "nai-diffusion-4-5-full")
            )

            if self.config.get("show_tags", True):
                generate_msg = (
                    f"正在生成...\n模型: {model}\nPrompt: {final_prompt[:200]}"
                )
            else:
                generate_msg = f"正在生成...\n模型: {model}"
            if self.config.get("show_api_prompt", False):
                generate_msg += f"\n[API Tag] {final_prompt}"
                if negative:
                    generate_msg += f"\n[API Negative] {negative}"
            yield event.plain_result(generate_msg)

            client = self._get_client()
            ok_quote, quote_msg = await self._check_paid_quote(
                client, model, "generate", final_prompt, params, options
            )
            if not ok_quote:
                yield event.plain_result(quote_msg)
                return
            resp = await client.generate_image(
                model=model,
                input_text=final_prompt,
                parameters=params,
            )
            images, fmt = self._extract_generated_images(resp)
            paths = self._save_images(images, fmt)
            job = resp.get("job")
            cost = job.get("cost_gems", "?") if isinstance(job, dict) else "?"
            yield event.plain_result(f"生成完成，消耗 {cost} Gems")
            for path in paths:
                yield event.image_result(str(path))
        except YesNAIError as exc:
            logger.error(f"YesNAI 生图失败: {exc}")
            yield event.plain_result(f"生图失败: {exc}")
        except Exception as exc:
            logger.exception("YesNAI 生图出现未预期错误")
            yield event.plain_result(f"生图失败: {exc}")

    @filter.llm_tool(name="ynai_generate")
    async def ynai_generate_tool(
        self,
        event: AstrMessageEvent,
        tags: str = "",
        **kwargs,
    ) -> str:
        """使用默认生图配置生成图片并发送到当前会话。只需要提供 Danbooru tags。

        Args:
            tags(string): 逗号分隔的 Danbooru tag 列表，例如 "1girl, blue sky, masterpiece"。
        """
        tags = (tags or "").strip()
        if not tags:
            return json.dumps(
                {"status": "error", "message": "缺少 tags 参数，请提供逗号分隔的 Danbooru tag"},
                ensure_ascii=False,
            )

        try:
            artist_prompt = await self._get_selected_artist_prompt(event)
            nsfw_enabled = await self._get_nsfw_enabled(event)
            final_prompt = self._compose_positive_prompt(
                tags, artist_prompt, nsfw_enabled=nsfw_enabled
            )
            negative = self._compose_negative_prompt({}, nsfw_enabled=nsfw_enabled)
            params = self._build_parameters({})
            if negative:
                params["negative_prompt"] = negative

            model = str(
                self.config.get("default_model", "nai-diffusion-4-5-full")
            )
            client = self._get_client()
            resp = await client.generate_image(
                model=model,
                input_text=final_prompt,
                parameters=params,
            )
            images, fmt = self._extract_generated_images(resp)
            paths = self._save_images(images, fmt)
            job = resp.get("job")
            cost = job.get("cost_gems", "?") if isinstance(job, dict) else "?"

            from astrbot.api.message_components import Image, Plain

            chain: list = [Plain(f"生成完成，消耗 {cost} Gems")]
            for path in paths:
                chain.append(Image.fromFileSystem(str(path)))

            sent = False
            try:
                await event.send(event.chain_result(chain))
                sent = True
            except Exception:
                try:
                    from astrbot.api.event import MessageEventResult

                    await event.send(MessageEventResult(chain=chain))
                    sent = True
                except Exception as exc:
                    logger.error(f"ynai_generate 工具图片发送失败: {exc}")

            return json.dumps(
                {
                    "status": "success" if sent else "delivery_failed",
                    "success": bool(sent),
                    "generated": True,
                    "sent": sent,
                    "images": len(paths),
                    "cost_gems": cost,
                    "message": "图片已发送" if sent else "图片已生成但发送失败",
                },
                ensure_ascii=False,
            )
        except YesNAIError as exc:
            logger.error(f"ynai_generate 工具生图失败: {exc}")
            return json.dumps(
                {"status": "error", "message": f"生图失败: {exc}"},
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.exception("ynai_generate 工具生图出现未预期错误")
            return json.dumps(
                {"status": "error", "message": f"生图失败: {exc}"},
                ensure_ascii=False,
            )

    # ---------------------------------------------------------------
    # 子命令
    # ---------------------------------------------------------------
    async def _ynai_img2img(self, event: AstrMessageEvent, args: str):
        """图重绘：必须引用/附带一张图片。"""
        options, prompt = self._parse_options(args)
        if not prompt:
            yield event.plain_result(
                "用法：/ynai i2i <描述> [--strength 0.5] [--noise 0.0] "
                "[--model 模型] [--size 832x1216] [-nt] [-y]"
            )
            return

        image = await self._get_referenced_image(event)
        if not image:
            yield event.plain_result(
                "请回复/引用一张图片后再使用 /ynai i2i <描述>"
            )
            return

        if options.get("translate", True):
            if not self.config.get("llm_translation_enabled", True):
                yield event.plain_result(
                    "LLM 翻译 Tag 功能未启用，请管理员在插件配置中开启 "
                    "llm_translation_enabled"
                )
                return
            yield event.plain_result("正在用 LLM 翻译 Tag...")
            try:
                ok, translated = await self._translate_to_tags(event, prompt)
            except Exception as exc:
                logger.exception("LLM 翻译出现未预期错误")
                yield event.plain_result(f"LLM 翻译出现未预期错误: {exc}")
                return
            if not ok:
                yield event.plain_result(translated)
                return
            prompt = translated

        try:
            base64_image = await image.convert_to_base64()
        except Exception as exc:
            yield event.plain_result(f"图片读取失败: {exc}")
            return

        try:
            artist_prompt = await self._get_selected_artist_prompt(event)
            nsfw_enabled = await self._get_nsfw_enabled(event)
            final_prompt = self._compose_positive_prompt(
                prompt, artist_prompt, nsfw_enabled=nsfw_enabled
            )
            negative = self._compose_negative_prompt(options, nsfw_enabled=nsfw_enabled)

            params = self._build_parameters(options)
            self._apply_v4_defaults(params, final_prompt, negative)
            strength = float(options.get("strength", 0.5))
            noise = float(options.get("noise", 0.0))
            params["image"] = base64_image
            params["strength"] = strength
            params["noise"] = noise
            params["img2img"] = {"color_correct": True, "strength": strength}
            if negative:
                params["negative_prompt"] = negative

            model = str(
                options.get("model")
                or self.config.get("default_model", "nai-diffusion-4-5-full")
            )

            generate_msg = f"正在重绘...\n模型: {model}"
            if self.config.get("show_api_prompt", False):
                generate_msg += f"\n[API Tag] {final_prompt}"
                if negative:
                    generate_msg += f"\n[API Negative] {negative}"
            yield event.plain_result(generate_msg)

            client = self._get_client()
            ok_quote, quote_msg = await self._check_paid_quote(
                client, model, "img2img", final_prompt, params, options
            )
            if not ok_quote:
                yield event.plain_result(quote_msg)
                return
            resp = await client.generate_image(
                model=model,
                input_text=final_prompt,
                parameters=params,
                action="img2img",
            )
            images, fmt = self._extract_generated_images(resp)
            paths = self._save_images(images, fmt)
            job = resp.get("job")
            cost = job.get("cost_gems", "?") if isinstance(job, dict) else "?"
            yield event.plain_result(f"重绘完成，消耗 {cost} Gems")
            for path in paths:
                yield event.image_result(str(path))
        except YesNAIError as exc:
            logger.error(f"YesNAI 图重绘失败: {exc}")
            yield event.plain_result(f"图重绘失败: {exc}")
        except Exception as exc:
            logger.exception("YesNAI 图重绘出现未预期错误")
            yield event.plain_result(f"图重绘失败: {exc}")

    async def _ynai_ref(self, event: AstrMessageEvent, args: str):
        """角色参考：必须引用/附带一张图片。"""
        options, prompt = self._parse_options(args)
        if not prompt:
            yield event.plain_result(
                "用法：/ynai ref <描述> [--strength 1.0] [--model 模型] "
                "[--size 832x1216] [-nt] [-s] [-b] [-y]"
            )
            return

        image = await self._get_referenced_image(event)
        if not image:
            yield event.plain_result(
                "请回复/引用一张图片后再使用 /ynai ref <描述>"
            )
            return

        if options.get("translate", True):
            if not self.config.get("llm_translation_enabled", True):
                yield event.plain_result(
                    "LLM 翻译 Tag 功能未启用，请管理员在插件配置中开启 "
                    "llm_translation_enabled"
                )
                return
            yield event.plain_result("正在用 LLM 翻译 Tag...")
            try:
                ok, translated = await self._translate_to_tags(event, prompt)
            except Exception as exc:
                logger.exception("LLM 翻译出现未预期错误")
                yield event.plain_result(f"LLM 翻译出现未预期错误: {exc}")
                return
            if not ok:
                yield event.plain_result(translated)
                return
            prompt = translated

        try:
            base64_image = await image.convert_to_base64()
        except Exception as exc:
            yield event.plain_result(f"图片读取失败: {exc}")
            return
        base64_image = self._normalize_reference_image(base64_image)

        try:
            artist_prompt = await self._get_selected_artist_prompt(event)
            nsfw_enabled = await self._get_nsfw_enabled(event)
            final_prompt = self._compose_positive_prompt(
                prompt, artist_prompt, nsfw_enabled=nsfw_enabled
            )
            negative = self._compose_negative_prompt(options, nsfw_enabled=nsfw_enabled)

            params = self._build_parameters(options)
            params.setdefault("scale", 5.0)
            self._apply_v4_defaults(params, final_prompt, negative)
            params["director_reference_images"] = [base64_image]
            params["director_reference_strength_values"] = [
                float(options.get("strength", 1.0))
            ]
            params["director_reference_secondary_strength_values"] = [
                float(options.get("secondary_strength", 0.5))
            ]
            params["director_reference_information_extracted"] = [1.0]
            reference_caption = (
                "character&style"
                if options.get("both")
                else "style"
                if options.get("style")
                else "character"
            )
            params["director_reference_descriptions"] = [
                {
                    "caption": {
                        "base_caption": reference_caption,
                        "char_captions": [],
                    },
                    "legacy_uc": False,
                }
            ]
            if negative:
                params["negative_prompt"] = negative

            model = str(
                options.get("model")
                or self.config.get("default_model", "nai-diffusion-4-5-full")
            )

            generate_msg = f"正在使用参考角色生成...\n模型: {model}"
            if self.config.get("show_api_prompt", False):
                generate_msg += f"\n[API Tag] {final_prompt}"
                if negative:
                    generate_msg += f"\n[API Negative] {negative}"
            yield event.plain_result(generate_msg)

            client = self._get_client()
            ok_quote, quote_msg = await self._check_paid_quote(
                client, model, "generate", final_prompt, params, options
            )
            if not ok_quote:
                yield event.plain_result(quote_msg)
                return
            resp = await client.generate_image(
                model=model,
                input_text=final_prompt,
                parameters=params,
                action="generate",
            )
            images, fmt = self._extract_generated_images(resp)
            paths = self._save_images(images, fmt)
            job = resp.get("job")
            cost = job.get("cost_gems", "?") if isinstance(job, dict) else "?"
            yield event.plain_result(f"生成完成，消耗 {cost} Gems")
            for path in paths:
                yield event.image_result(str(path))
        except YesNAIError as exc:
            logger.error(f"YesNAI 参考角色生成失败: {exc}")
            yield event.plain_result(f"参考角色生成失败: {exc}")
        except Exception as exc:
            logger.exception("YesNAI 参考角色生成出现未预期错误")
            yield event.plain_result(f"参考角色生成失败: {exc}")

    async def _ynai_model(self, event: AstrMessageEvent, args: str):
        try:
            client = self._get_client()
            models = await client.get_models()
            if not isinstance(models, list) or not models:
                yield event.plain_result("没有获取到模型")
                return
            text = "可用模型：\n" + "\n".join(
                str(m.get("id", "")) for m in models
            )
            yield event.plain_result(text)
        except YesNAIError as exc:
            logger.error(f"获取模型列表失败: {exc}")
            yield event.plain_result(f"获取模型列表失败: {exc}")
        except Exception as exc:
            logger.exception("获取模型列表出现未预期错误")
            yield event.plain_result(f"获取模型列表失败: {exc}")

    async def _ynai_artist(self, event: AstrMessageEvent, args: str):
        parts = args.split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action in ("list", "ls", ""):
            artists = self._normalize_artists()
            if not artists:
                yield event.plain_result(
                    "还没有画师串。\n"
                    "添加：/ynai artist add <名称> <画师串>\n"
                    "选择：/ynai artist set <ID>"
                )
                return
            selected = await self.get_kv_data(
                f"artist:{event.unified_msg_origin}", None
            )
            lines = []
            for artist_item in artists:
                aid = str(artist_item.get("id") or artist_item.get("name") or "?")
                name = str(artist_item.get("name", ""))
                mark = "✔" if aid == selected else " "
                prompt = str(artist_item.get("prompt", ""))[:60]
                lines.append(f"{mark} [{aid}] {name}: {prompt}")
            yield event.plain_result("画师串列表：\n" + "\n".join(lines))

        elif action == "set":
            if not rest:
                yield event.plain_result("用法：/ynai artist set <ID>")
                return
            artist_id = rest.strip()
            if not self._find_artist(artist_id):
                yield event.plain_result(f"没有找到画师串：{artist_id}")
                return
            await self.put_kv_data(f"artist:{event.unified_msg_origin}", artist_id)
            yield event.plain_result(
                f"已选择画师串：{artist_id}（生图时自动拼接到提示词前面）"
            )

        elif action == "clear":
            await self.delete_kv_data(f"artist:{event.unified_msg_origin}")
            yield event.plain_result("已清除当前画师串选择")

        elif action == "add":
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以添加/修改画师串")
                return
            add_parts = rest.split(maxsplit=1)
            if len(add_parts) < 2:
                yield event.plain_result(
                    "用法：/ynai artist add <名称> <画师串>"
                )
                return
            name, prompt = add_parts[0].strip(), add_parts[1].strip()
            if not name or not prompt:
                yield event.plain_result("名称和画师串都不能为空")
                return
            artists = self._normalize_artists()
            found = False
            for artist_item in artists:
                if artist_item.get("name") == name:
                    artist_item["name"] = name
                    artist_item["prompt"] = prompt
                    found = True
                    break
            if not found:
                max_id = 0
                for artist_item in artists:
                    raw_id = artist_item.get("id")
                    if raw_id is not None and str(raw_id).strip().isdigit():
                        max_id = max(max_id, int(str(raw_id).strip()))
                artist_id = str(max_id + 1)
                artists.append(
                    {
                        "__template_key": "artist",
                        "id": artist_id,
                        "name": name,
                        "prompt": prompt,
                    }
                )
            self.config["artists"] = self._artists_to_storage(artists)
            self.config.save_config()
            artist_id = next(
                (
                    str(a.get("id"))
                    for a in artists
                    if a.get("name") == name and a.get("id")
                ),
                "?",
            )
            yield event.plain_result(f"已保存画师串：{artist_id} ({name})")

        elif action == "del":
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以删除画师串")
                return
            if not rest:
                yield event.plain_result("用法：/ynai artist del <ID>")
                return
            artist_id = rest.strip()
            artists = self._normalize_artists()
            new_artists = []
            removed = False
            for artist_item in artists:
                aid = str(artist_item.get("id") or artist_item.get("name") or "")
                if aid == artist_id or artist_item.get("name") == artist_id:
                    removed = True
                    continue
                new_artists.append(artist_item)
            if not removed:
                yield event.plain_result(f"没有找到画师串：{artist_id}")
                return
            self.config["artists"] = self._artists_to_storage(new_artists)
            self.config.save_config()
            yield event.plain_result(f"已删除画师串：{artist_id}")

        else:
            yield event.plain_result(
                "未知子命令。可用：list / set / add / del / clear"
            )

    async def _ynai_preset(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以使用 /ynai preset 命令")
            return
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
                "设置：/ynai preset positive <内容> 或 /ynai preset negative <内容>"
            )

        elif action in ("positive", "pos"):
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以修改预设提示词")
                return
            if not rest:
                yield event.plain_result("用法：/ynai preset positive <提示词>")
                return
            self.config["preset_positive_prompt"] = rest
            self.config.save_config()
            yield event.plain_result("已设置预设正面提示词")

        elif action in ("negative", "neg"):
            if not self._is_admin(event):
                yield event.plain_result("只有管理员可以修改预设提示词")
                return
            if not rest:
                yield event.plain_result("用法：/ynai preset negative <提示词>")
                return
            self.config["preset_negative_prompt"] = rest
            self.config.save_config()
            yield event.plain_result("已设置预设负面提示词")

        else:
            yield event.plain_result(
                "未知子命令。可用：show / positive / negative"
            )

    async def _ynai_nsfw(self, event: AstrMessageEvent, args: str):
        action = args.strip().lower() if args.strip() else "status"
        kv_key = f"nsfw:{event.unified_msg_origin}"

        if action == "status":
            session_value = await self.get_kv_data(kv_key, None)
            if session_value is not None:
                session_enabled = (
                    session_value
                    if isinstance(session_value, bool)
                    else str(session_value).strip().lower()
                    in ("1", "true", "yes", "on")
                )
                state = "开启" if session_enabled else "关闭"
                yield event.plain_result(f"当前会话 NSFW：{state}（本会话单独设置）")
            else:
                state = "开启" if self.config.get("nsfw_enabled", False) else "关闭"
                yield event.plain_result(f"当前 NSFW（全局默认）：{state}")
            return

        if action == "reset":
            await self.delete_kv_data(kv_key)
            yield event.plain_result("已清除当前会话的 NSFW 单独设置，恢复全局默认")
            return

        if action not in ("on", "off"):
            yield event.plain_result("用法：/ynai nsfw on|off|status|reset")
            return

        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以修改 NSFW 开关")
            return

        await self.put_kv_data(kv_key, action == "on")
        yield event.plain_result(
            f"当前会话 NSFW 开关已{'开启' if action == 'on' else '关闭'}"
        )

    # ---------------------------------------------------------------
    # 指令入口
    # ---------------------------------------------------------------
    @filter.command("ynai")
    async def ynai(self, event: AstrMessageEvent):
        """YesNAI 主命令：/ynai <描述> 默认 LLM 翻译生图，/ynai model 等为子命令"""
        args = self._command_args(event)
        if not args:
            async for result in self._run_safely(
                event, self._ynai_help_card(event)
            ):
                yield result
            return

        first, _, rest = args.partition(" ")
        sub = first.lower()
        sub_args = rest.strip()

        if sub in ("help", "h", "?"):
            async for result in self._run_safely(
                event, self._ynai_help_card(event)
            ):
                yield result
        elif sub in ("model", "models", "m"):
            async for result in self._run_safely(
                event, self._ynai_model(event, sub_args)
            ):
                yield result
        elif sub in ("artist", "a"):
            async for result in self._run_safely(
                event, self._ynai_artist(event, sub_args)
            ):
                yield result
        elif sub in ("preset", "p"):
            async for result in self._run_safely(
                event, self._ynai_preset(event, sub_args)
            ):
                yield result
        elif sub in ("nsfw",):
            async for result in self._run_safely(
                event, self._ynai_nsfw(event, sub_args)
            ):
                yield result
        elif sub in ("img2img", "i2i"):
            async for result in self._run_safely(
                event, self._ynai_img2img(event, sub_args)
            ):
                yield result
        elif sub in ("ref", "reference"):
            async for result in self._run_safely(
                event, self._ynai_ref(event, sub_args)
            ):
                yield result
        else:
            async for result in self._run_safely(
                event,
                self._ynai_generate(event, args, translate=True),
            ):
                yield result

    @filter.command("ynai0")
    async def ynai0(self, event: AstrMessageEvent):
        """YesNAI 直接生图：/ynai0 <提示词>"""
        args = self._command_args(event)
        if not args:
            yield event.plain_result("用法：/ynai0 <提示词> [--model ...] [--size ...] ... [-y]")
            return
        async for result in self._run_safely(
            event, self._ynai_generate(event, args, translate=False)
        ):
            yield result
