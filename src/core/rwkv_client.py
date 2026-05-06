"""RWKV API 客户端 - 封装 rwkv_lightning 的所有 API 端点

支持的端点:
- /v1/chat/completions       : 批量同步推理（支持全部采样参数）
- /v2/chat/completions       : 连续批处理（支持全部采样参数）
- /big_batch/completions     : 超级并发批量推理（最快，仅支持 temperature）
- /openai/v1/chat/completions: OpenAI 兼容格式
- /state/chat/completions    : 有状态缓存推理（session_id）
- /FIM/v1/batch-FIM          : FIM 填充推理
"""

import json
import time
import requests
from typing import Any, Dict, List, Optional, Tuple

from .config import SamplingParams, APIConfig
from .logger import Logger
from .error_handler import ErrorHandler, RetryConfig


class RWKVClient:
    """RWKV Lightning API 客户端"""

    # 默认停止 token: 0=EOF, 261=<\n>, 24281=常见结束符
    DEFAULT_STOP_TOKENS = [0]

    def __init__(self, api_config: APIConfig, logger: Optional[Logger] = None, error_handler: Optional[ErrorHandler] = None):
        self.base_url = api_config.base_url.rstrip('/')
        self.api_key = api_config.api_key
        self.password = api_config.password
        self.model = api_config.model
        self._logger = logger or Logger.get()
        self._error_handler = error_handler or ErrorHandler(self._logger)
        self._session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _build_params(self, sampling: SamplingParams, **overrides) -> Dict:
        """构造通用采样参数"""
        params = {
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "top_k": sampling.top_k,
            "alpha_presence": sampling.alpha_presence,
            "alpha_frequency": sampling.alpha_frequency,
            "alpha_decay": sampling.alpha_decay,
            "max_tokens": sampling.max_tokens,
            "stop_tokens": self.DEFAULT_STOP_TOKENS,
        }
        if self.password:
            params["password"] = self.password
        params.update(overrides)
        return params

    # ================================================================
    #  /v1/chat/completions - 批量同步推理（支持全部采样参数）
    # ================================================================
    def chat_completions_v1(
        self,
        contents: List[str],
        sampling: SamplingParams,
        stream: bool = False,
    ) -> List[str]:
        """批量同步推理

        Args:
            contents: 多个 prompt 字符串列表
            sampling: 采样参数
            stream: 是否流式

        Returns:
            每个prompt对应的生成文本列表
        """
        payload = self._build_params(sampling, contents=contents, stream=stream)
        return self._post_and_parse("/v1/chat/completions", payload, batch=True)

    # ================================================================
    #  /v2/chat/completions - 连续批处理（支持全部采样参数）
    # ================================================================
    def chat_completions_v2(
        self,
        contents: List[str],
        sampling: SamplingParams,
        stream: bool = False,
        chunk_size: int = 128,
        pad_zero: bool = True,
    ) -> List[str]:
        """连续批处理推理

        Args:
            contents: 多个 prompt 字符串列表
            sampling: 采样参数
            stream: 是否流式
            chunk_size: 推理块大小
            pad_zero: 是否零填充对齐
        """
        payload = self._build_params(
            sampling,
            contents=contents,
            stream=stream,
            chunk_size=chunk_size,
            pad_zero=pad_zero,
        )
        return self._post_and_parse("/v2/chat/completions", payload, batch=True)

    # ================================================================
    #  /big_batch/completions - 超级并发（最快，仅支持 temperature）
    # ================================================================
    def big_batch_completions(
        self,
        contents: List[str],
        sampling: SamplingParams,
        stream: bool = False,
        chunk_size: int = 8,
    ) -> List[str]:
        """超级并发批量推理 - 核心并发创作端点

        仅支持 temperature 采样参数，但速度最快。
        单次请求可驱动数百至上千路并发。

        Args:
            contents: 批量 prompt 列表（最大960路）
            sampling: 采样参数（仅 temperature 生效）
            stream: 是否流式
            chunk_size: 推理块大小
        """
        payload = {
            "contents": contents,
            "max_tokens": sampling.max_tokens,
            "stop_tokens": self.DEFAULT_STOP_TOKENS,
            "temperature": sampling.temperature,
            "chunk_size": chunk_size,
            "stream": stream,
        }
        if self.password:
            payload["password"] = self.password
        return self._post_and_parse("/big_batch/completions", payload, batch=True)

    # ================================================================
    #  /openai/v1/chat/completions - OpenAI 兼容格式
    # ================================================================
    def openai_chat_completions(
        self,
        messages: List[Dict[str, str]],
        sampling: SamplingParams,
        stream: bool = False,
        session_id: Optional[str] = None,
    ) -> str:
        """OpenAI 兼容格式推理

        Args:
            messages: OpenAI 格式的消息列表 [{"role": "user", "content": "..."}]
            sampling: 采样参数
            stream: 是否流式
            session_id: 可选，有状态会话ID
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "max_tokens": sampling.max_tokens,
            "stream": stream,
        }
        if session_id:
            payload["session_id"] = session_id
        if self.api_key:
            # OpenAI 格式用 Authorization header
            pass
        result = self._post_and_parse("/openai/v1/chat/completions", payload, batch=False)
        return result

    # ================================================================
    #  /state/chat/completions - 有状态缓存推理
    # ================================================================
    def state_chat_completions(
        self,
        contents: List[str],
        sampling: SamplingParams,
        session_id: str,
        stream: bool = False,
        chunk_size: int = 128,
    ) -> List[str]:
        """有状态缓存推理 - 支持 L1/L2/L3 三级缓存

        Args:
            contents: prompt 列表
            sampling: 采样参数
            session_id: 会话唯一标识
            stream: 是否流式
            chunk_size: 推理块大小
        """
        payload = self._build_params(
            sampling,
            contents=contents,
            stream=stream,
            chunk_size=chunk_size,
            session_id=session_id,
        )
        return self._post_and_parse("/state/chat/completions", payload, batch=True)

    # ================================================================
    #  /FIM/v1/batch-FIM - FIM 填充推理
    # ================================================================
    def fim_batch(
        self,
        prefix: List[str],
        suffix: List[str],
        sampling: SamplingParams,
        stream: bool = False,
    ) -> List[str]:
        """FIM (Fill In the Middle) 批量推理

        Args:
            prefix: 前文列表
            suffix: 后文列表
            sampling: 采样参数
            stream: 是否流式
        """
        payload = self._build_params(
            sampling,
            prefix=prefix,
            suffix=suffix,
            stream=stream,
        )
        return self._post_and_parse("/FIM/v1/batch-FIM", payload, batch=True)

    # ================================================================
    #  State Management API
    # ================================================================
    def state_status(self) -> Dict:
        """查询状态池状态"""
        payload = {}
        if self.password:
            payload["password"] = self.password
        resp = self._session.post(
            f"{self.base_url}/state/status",
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def state_delete(self, session_id: str) -> Dict:
        """删除指定会话的状态"""
        payload = {"session_id": session_id}
        if self.password:
            payload["password"] = self.password
        resp = self._session.post(
            f"{self.base_url}/state/delete",
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # ================================================================
    #  内部方法
    # ================================================================
    def _post_and_parse(
        self,
        endpoint: str,
        payload: Dict,
        batch: bool = True,
    ) -> Any:
        """发送POST请求并解析响应，带自动重试"""
        url = f"{self.base_url}{endpoint}"
        start = time.time()

        def _do_request():
            if payload.get("stream", False):
                return self._post_stream(url, payload, batch)

            resp = self._session.post(url, headers=self._headers(), json=payload, timeout=300)
            resp.raise_for_status()

            # rwkv_lightning 的 /big_batch/completions 即使 stream=False
            # 也返回 SSE (text/event-stream) 格式，需要自动检测
            content_type = resp.headers.get("Content-Type", "")
            if "event-stream" in content_type:
                result = self._parse_sse_response(resp.text, batch)
                elapsed = (time.time() - start) * 1000
                self._logger.info(f"API {endpoint} (SSE) completed in {elapsed:.0f}ms")
                return result

            data = resp.json()

            elapsed = (time.time() - start) * 1000
            self._logger.info(f"API {endpoint} completed in {elapsed:.0f}ms")

            return self._extract_text(data, batch)

        try:
            return self._error_handler.with_retry(_do_request)
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            self._logger.error(f"API {endpoint} failed after {elapsed:.0f}ms: {e}")
            raise

    def _parse_sse_response(self, text: str, batch: bool) -> Any:
        """解析 SSE 流式响应文本（非流式请求但服务器返回SSE格式的情况）"""
        # 按index分组收集每个batch item的文本
        items: Dict[int, str] = {}

        for line in text.split('\n'):
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                for choice in choices:
                    idx = choice.get("index", 0)
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")
                    if idx not in items:
                        items[idx] = ""
                    items[idx] += content
            except json.JSONDecodeError:
                continue

        if batch:
            # 按index排序返回列表
            return [items.get(i, "") for i in range(max(items.keys()) + 1)] if items else []
        else:
            return items.get(0, "")

    def _post_stream(self, url: str, payload: Dict, batch: bool) -> Any:
        """流式请求处理"""
        results = [] if batch else ""
        with self._session.post(url, headers=self._headers(), json=payload, stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        text = self._extract_text(data, batch=False)
                        if batch:
                            results.append(text)
                        else:
                            results += text
                    except json.JSONDecodeError:
                        continue
        return results

    def _extract_text(self, data: Any, batch: bool) -> Any:
        """从API响应中提取生成文本"""
        # OpenAI 格式
        if isinstance(data, dict) and "choices" in data:
            choices = data["choices"]
            if batch:
                return [c.get("message", {}).get("content", "") for c in choices]
            elif choices:
                msg = choices[0].get("message", {})
                if "content" in msg:
                    return msg["content"]
                delta = choices[0].get("delta", {})
                return delta.get("content", "")
            return "" if not batch else []

        # rwkv_lightning 原生格式
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "content" in data:
            return data["content"]
        if isinstance(data, str):
            return data

        return "" if not batch else []
