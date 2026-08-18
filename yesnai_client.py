"""YesNovelAI / YesNAI API 客户端。

封装 API Reference 中公开的常用端点：
- GET  /v1/models
- POST /v1/nai/generate-image
- POST /ai/generate-image/quote
- POST /ai/encode-vibe
- POST /ai/upscale
- POST /ai/augment-image
- POST /ai/annotate-image
- GET  /ai/generate-image/suggest-tags
- Native 非流式端点（generate-image、encode-vibe、upscale、augment-image、
  annotate-image、suggest-tags、user/subscription）
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import aiohttp


class YesNAIError(Exception):
    """YesNAI API 请求失败。"""


class YesNAIClient:
    def __init__(
        self,
        api_base: str = "https://nai.rinko.ai",
        api_token: str = "",
        timeout: int = 120,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        auth_token = token if token is not None else self.api_token
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.api_base}{path}"

    async def _request_json(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                async with session.request(
                    method,
                    self._url(path),
                    headers=self._headers(),
                    **kwargs,
                ) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise YesNAIError(
                            f"{method} {path} 失败: HTTP {resp.status} - {body[:500]}"
                        )
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise YesNAIError(
                            f"{method} {path} 返回非 JSON 响应: {body[:500]}"
                        ) from exc
        except asyncio.TimeoutError as exc:
            raise YesNAIError(
                f"{method} {path} 请求超时（{self.timeout}s），"
                "请调大插件配置里的 timeout"
            ) from exc
        except aiohttp.ClientError as exc:
            raise YesNAIError(f"{method} {path} 网络错误: {exc}") from exc

    async def _request_json_with_token(
        self,
        method: str,
        path: str,
        token: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                async with session.request(
                    method,
                    self._url(path),
                    headers=self._headers(token),
                    **kwargs,
                ) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise YesNAIError(
                            f"{method} {path} 失败: HTTP {resp.status} - {body[:500]}"
                        )
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise YesNAIError(
                            f"{method} {path} 返回非 JSON 响应: {body[:500]}"
                        ) from exc
        except asyncio.TimeoutError as exc:
            raise YesNAIError(
                f"{method} {path} 请求超时（{self.timeout}s），"
                "请调大插件配置里的 timeout"
            ) from exc
        except aiohttp.ClientError as exc:
            raise YesNAIError(f"{method} {path} 网络错误: {exc}") from exc

    async def _request_bytes(
        self, method: str, path: str, **kwargs: Any
    ) -> tuple[bytes, str]:
        """请求二进制接口，返回 (body, content_type)。"""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                async with session.request(
                    method,
                    self._url(path),
                    headers=self._headers(),
                    **kwargs,
                ) as resp:
                    body = await resp.read()
                    if resp.status >= 400:
                        raise YesNAIError(
                            f"{method} {path} 失败: HTTP {resp.status} - "
                            f"{body[:500].decode('utf-8', errors='ignore')}"
                        )
                    return body, resp.headers.get("Content-Type", "")
        except asyncio.TimeoutError as exc:
            raise YesNAIError(
                f"{method} {path} 请求超时（{self.timeout}s），"
                "请调大插件配置里的 timeout"
            ) from exc
        except aiohttp.ClientError as exc:
            raise YesNAIError(f"{method} {path} 网络错误: {exc}") from exc

    async def get_models(self) -> list[dict[str, Any]]:
        data = await self._request_json("GET", "/v1/models")
        return data.get("data", [])

    async def generate_image(
        self,
        model: str,
        input_text: str,
        parameters: dict[str, Any] | None = None,
        action: str = "generate",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "action": action,
            "input": input_text,
        }
        if parameters:
            payload["parameters"] = parameters
        return await self._request_json(
            "POST", "/v1/nai/generate-image", json=payload
        )

    async def quote(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/ai/generate-image/quote", json=payload)

    # ---- OpenAI 兼容端点 ----
    async def images_generations(
        self,
        model: str,
        prompt: str,
        size: str = "512x512",
        n: int = 1,
        response_format: str = "b64_json",
        nai: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": n,
            "response_format": response_format,
        }
        if nai:
            payload["nai"] = nai
        return await self._request_json(
            "POST", "/v1/images/generations", json=payload
        )

    async def chat_completions(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        return await self._request_json(
            "POST", "/v1/chat/completions", json=payload
        )

    async def encode_vibe(
        self,
        image: str | bytes,
        model: str,
        information_extracted: float = 1.0,
    ) -> tuple[bytes, str]:
        if isinstance(image, bytes):
            image = base64.b64encode(image).decode("ascii")
        payload = {
            "image": image,
            "model": model,
            "information_extracted": information_extracted,
        }
        return await self._request_bytes("POST", "/ai/encode-vibe", json=payload)

    async def upscale(
        self,
        image: str | bytes,
        scale: int = 4,
        model: str | None = None,
    ) -> tuple[bytes, str]:
        if isinstance(image, bytes):
            image = base64.b64encode(image).decode("ascii")
        payload: dict[str, Any] = {"image": image, "scale": scale}
        if model:
            payload["model"] = model
        return await self._request_bytes("POST", "/ai/upscale", json=payload)

    async def augment_image(
        self,
        image: str | bytes,
        req_type: str,
        model: str | None = None,
    ) -> tuple[bytes, str]:
        if isinstance(image, bytes):
            image = base64.b64encode(image).decode("ascii")
        payload: dict[str, Any] = {"image": image, "req_type": req_type}
        if model:
            payload["model"] = model
        return await self._request_bytes("POST", "/ai/augment-image", json=payload)

    async def annotate_image(
        self,
        image: str | bytes,
        req_type: str,
        model: str | None = None,
    ) -> tuple[bytes, str]:
        if isinstance(image, bytes):
            image = base64.b64encode(image).decode("ascii")
        payload: dict[str, Any] = {"image": image, "req_type": req_type}
        if model:
            payload["model"] = model
        return await self._request_bytes("POST", "/ai/annotate-image", json=payload)

    async def suggest_tags(
        self, prompt: str, model: str
    ) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            "/ai/generate-image/suggest-tags",
            params={"prompt": prompt, "model": model},
        )

    # ---- Playground（登录 JWT）端点 ----
    async def playground_quote(
        self, jwt_token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request_json_with_token(
            "POST", "/api/ynai/playground/quote", jwt_token, json=payload
        )

    async def playground_generate(
        self, jwt_token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request_json_with_token(
            "POST",
            "/api/ynai/playground/images/generations",
            jwt_token,
            json=payload,
        )

    # ---- Native 兼容端点 ----
    async def native_generate_image(
        self, payload: dict[str, Any]
    ) -> tuple[bytes, str]:
        return await self._request_bytes(
            "POST", "/native/ai/generate-image", json=payload
        )

    async def native_generate_image_stream(
        self, payload: dict[str, Any]
    ):
        """调用 Native 流式生成接口，按长度前缀解析 MessagePack 帧并逐个返回。"""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            async with session.post(
                self._url("/native/ai/generate-image-stream"),
                headers=self._headers(),
                json=payload,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.read()
                    raise YesNAIError(
                        "POST /native/ai/generate-image-stream 失败: "
                        f"HTTP {resp.status} - "
                        f"{body[:500].decode('utf-8', errors='ignore')}"
                    )
                data = await resp.read()
                offset = 0
                while offset + 4 <= len(data):
                    length = int.from_bytes(data[offset : offset + 4], "big")
                    offset += 4
                    if offset + length > len(data):
                        break
                    yield data[offset : offset + length]
                    offset += length

    async def native_subscription(self) -> dict[str, Any]:
        return await self._request_json("GET", "/native/user/subscription")

    async def native_encode_vibe(self, payload: dict[str, Any]) -> tuple[bytes, str]:
        return await self._request_bytes(
            "POST", "/native/ai/encode-vibe", json=payload
        )

    async def native_upscale(self, payload: dict[str, Any]) -> tuple[bytes, str]:
        return await self._request_bytes("POST", "/native/ai/upscale", json=payload)

    async def native_augment_image(self, payload: dict[str, Any]) -> tuple[bytes, str]:
        return await self._request_bytes(
            "POST", "/native/ai/augment-image", json=payload
        )

    async def native_annotate_image(self, payload: dict[str, Any]) -> tuple[bytes, str]:
        return await self._request_bytes(
            "POST", "/native/ai/annotate-image", json=payload
        )

    async def native_suggest_tags(
        self, prompt: str, model: str
    ) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            "/native/ai/generate-image/suggest-tags",
            params={"prompt": prompt, "model": model},
        )
