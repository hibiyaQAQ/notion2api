# app/providers/notion_provider.py
import json
import time
import logging
import uuid
import re
import cloudscraper
from typing import Dict, Any, AsyncGenerator, List, Optional, Tuple
from datetime import datetime

from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.exceptions import (
    NotionConfigurationError,
    NotionThreadCreationError,
    NotionRequestError,
    NotionResponseParseError,
    NotionAuthenticationError,
    ModelNotSupportedError,
    NotionRateLimitError
)
from app.providers.base_provider import BaseProvider
from app.utils.sse_utils import create_sse_data, create_chat_completion_chunk, DONE_CHUNK

# 设置日志记录器
logger = logging.getLogger(__name__)

class NotionAIProvider(BaseProvider):
    def __init__(self):
        """初始化 Notion AI Provider"""
        # 验证必需的配置
        if not all([settings.NOTION_COOKIE, settings.NOTION_SPACE_ID, settings.NOTION_USER_ID]):
            raise NotionConfigurationError(
                "NOTION_COOKIE, NOTION_SPACE_ID 和 NOTION_USER_ID 必须在 .env 文件中全部设置"
            )

        self.scraper = cloudscraper.create_scraper()
        self.api_endpoints = {
            "runInference": "https://www.notion.so/api/v3/runInferenceTranscript",
            "saveTransactions": "https://www.notion.so/api/v3/saveTransactionsFanout"
        }

        self._warmup_session()

    def _warmup_session(self):
        """预热会话,建立初始连接"""
        try:
            logger.info("正在进行会话预热 (Session Warm-up)...")
            headers = self._prepare_headers()
            headers.pop("Accept", None)
            response = self.scraper.get("https://www.notion.so/", headers=headers, timeout=30)
            response.raise_for_status()
            logger.info("会话预热成功。")
        except Exception as e:
            logger.warning(f"会话预热失败 (非致命错误): {e}")
            # 会话预热失败不应阻止服务启动

    async def _create_thread(self, thread_type: str) -> str:
        """创建 Notion 对话线程"""
        thread_id = str(uuid.uuid4())
        payload = {
            "requestId": str(uuid.uuid4()),
            "transactions": [{
                "id": str(uuid.uuid4()),
                "spaceId": settings.NOTION_SPACE_ID,
                "operations": [{
                    "pointer": {"table": "thread", "id": thread_id, "spaceId": settings.NOTION_SPACE_ID},
                    "path": [],
                    "command": "set",
                    "args": {
                        "id": thread_id, "version": 1, "parent_id": settings.NOTION_SPACE_ID,
                        "parent_table": "space", "space_id": settings.NOTION_SPACE_ID,
                        "created_time": int(time.time() * 1000),
                        "created_by_id": settings.NOTION_USER_ID, "created_by_table": "notion_user",
                        "messages": [], "data": {}, "alive": True, "type": thread_type
                    }
                }]
            }]
        }
        try:
            logger.info(f"正在创建新的对话线程 (type: {thread_type})...")
            response = await run_in_threadpool(
                lambda: self.scraper.post(
                    self.api_endpoints["saveTransactions"],
                    headers=self._prepare_headers(),
                    json=payload,
                    timeout=20
                )
            )

            # 检查HTTP状态码
            if response.status_code == 401:
                raise NotionAuthenticationError("Notion 认证失败,请检查 Cookie 是否有效")
            elif response.status_code == 429:
                raise NotionRateLimitError()
            elif response.status_code >= 500:
                raise NotionRequestError(f"Notion 服务器错误 (HTTP {response.status_code})", response.status_code)

            response.raise_for_status()
            logger.info(f"对话线程创建成功, Thread ID: {thread_id}")
            return thread_id
        except NotionRateLimitError:
            raise
        except NotionAuthenticationError:
            raise
        except NotionRequestError:
            raise
        except Exception as e:
            logger.error(f"创建对话线程失败: {e}", exc_info=True)
            raise NotionThreadCreationError(f"无法创建对话线程: {str(e)}")

    async def chat_completion(self, request_data: Dict[str, Any]):
        """处理聊天完成请求"""
        stream = request_data.get("stream", True)

        # 验证模型
        model_name = request_data.get("model", settings.DEFAULT_MODEL)
        if model_name not in settings.MODEL_MAP:
            raise ModelNotSupportedError(model_name)

        if stream:
            return await self._stream_chat_completion(request_data, model_name)
        else:
            return await self._non_stream_chat_completion(request_data, model_name)

    async def _non_stream_chat_completion(self, request_data: Dict[str, Any], model_name: str) -> JSONResponse:
        """非流式聊天完成"""
        request_id = f"chatcmpl-{uuid.uuid4()}"

        try:
            mapped_model = settings.MODEL_MAP.get(model_name, "anthropic-sonnet-alt")
            thread_type = "markdown-chat" if mapped_model.startswith("vertex-") else "workflow"

            thread_id = await self._create_thread(thread_type)
            payload = self._prepare_payload(request_data, thread_id, mapped_model, thread_type)
            headers = self._prepare_headers()

            logger.info(f"发送非流式请求到 Notion AI (模型: {model_name})")

            # 收集完整响应
            full_content = ""

            def sync_request():
                try:
                    response = self.scraper.post(
                        self.api_endpoints['runInference'],
                        headers=headers,
                        json=payload,
                        stream=True,
                        timeout=settings.API_REQUEST_TIMEOUT
                    )

                    if response.status_code == 401:
                        raise NotionAuthenticationError("Notion 认证失败")
                    elif response.status_code == 429:
                        raise NotionRateLimitError()

                    response.raise_for_status()

                    lines = []
                    for line in response.iter_lines():
                        if line:
                            lines.append(line)
                    return lines
                except Exception as e:
                    raise e

            lines = await run_in_threadpool(sync_request)

            incremental_fragments = []
            final_message = None

            for line in lines:
                parsed_results = self._parse_ndjson_line_to_texts(line)
                for text_type, content in parsed_results:
                    if text_type == 'final':
                        final_message = content
                    elif text_type == 'incremental':
                        incremental_fragments.append(content)

            if final_message:
                full_content = final_message
            else:
                full_content = "".join(incremental_fragments)

            if not full_content:
                logger.warning("Notion 未返回任何内容")
                full_content = ""

            # include_reasoning 控制是否移除思考内容
            include_reasoning = request_data.get("include_reasoning", False)
            remove_thinking = not include_reasoning
            cleaned_content = self._clean_content(full_content, remove_thinking=remove_thinking)

            logger.info(f"非流式请求完成，include_reasoning={include_reasoning}, 清洗后内容长度={len(cleaned_content)}")

            # 返回标准 OpenAI 格式响应
            response_data = {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": cleaned_content
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            }

            return JSONResponse(content=response_data)

        except (NotionAuthenticationError, NotionRateLimitError, NotionThreadCreationError, ModelNotSupportedError):
            raise
        except Exception as e:
            logger.error(f"非流式请求处理失败: {e}", exc_info=True)
            raise NotionRequestError(f"处理请求失败: {str(e)}")

    async def _stream_chat_completion(self, request_data: Dict[str, Any], model_name: str) -> StreamingResponse:
        """流式聊天完成 - 真实增量输出"""

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            request_id = f"chatcmpl-{uuid.uuid4()}"

            # 控制是否返回思考内容
            include_reasoning = request_data.get("include_reasoning", False)

            try:
                mapped_model = settings.MODEL_MAP.get(model_name, "anthropic-sonnet-alt")
                thread_type = "markdown-chat" if mapped_model.startswith("vertex-") else "workflow"

                thread_id = await self._create_thread(thread_type)
                payload = self._prepare_payload(request_data, thread_id, mapped_model, thread_type)
                headers = self._prepare_headers()

                # 立即返回 role chunk
                role_chunk = create_chat_completion_chunk(request_id, model_name, role="assistant")
                yield create_sse_data(role_chunk)

                def sync_stream_iterator():
                    try:
                        logger.info(f"请求 Notion AI URL: {self.api_endpoints['runInference']}")
                        logger.debug(f"请求体: {json.dumps(payload, indent=2, ensure_ascii=False)}")

                        response = self.scraper.post(
                            self.api_endpoints['runInference'],
                            headers=headers,
                            json=payload,
                            stream=True,
                            timeout=settings.API_REQUEST_TIMEOUT
                        )
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if line:
                                yield line
                    except Exception as e:
                        yield e

                sync_gen = sync_stream_iterator()

                # 用于跟踪已发送的内容（避免重复）
                sent_content_length = 0
                accumulated_content = ""
                reasoning_content = ""
                has_sent_reasoning = False

                while True:
                    line = await run_in_threadpool(lambda: next(sync_gen, None))
                    if line is None:
                        break
                    if isinstance(line, Exception):
                        raise line

                    parsed_results = self._parse_ndjson_line_to_texts(line)

                    for text_type, content in parsed_results:
                        logger.debug(f"[流式处理] 收到类型={text_type}, 内容长度={len(content)}")

                        if text_type == 'final':
                            # 最终消息，包含完整内容
                            accumulated_content = content
                            logger.debug(f"[流式处理] 累积内容更新为完整内容，长度={len(accumulated_content)}")
                        elif text_type == 'incremental':
                            # 增量内容
                            accumulated_content += content
                            logger.debug(f"[流式处理] 累积内容增加，新长度={len(accumulated_content)}")
                        elif text_type == 'thinking':
                            # 思考内容
                            reasoning_content += content
                            logger.debug(f"[流式处理] 思考内容累积，长度={len(reasoning_content)}, include_reasoning={include_reasoning}")
                            if include_reasoning and not has_sent_reasoning and reasoning_content:
                                # 发送思考内容（作为单独的消息）
                                reasoning_chunk = {
                                    "id": request_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model_name,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {
                                            "reasoning_content": reasoning_content
                                        },
                                        "finish_reason": None
                                    }]
                                }
                                logger.info(f"[流式处理] 发送思考内容块，长度={len(reasoning_content)}")
                                yield create_sse_data(reasoning_chunk)
                                has_sent_reasoning = True

                    # 实时增量发送新内容
                    if len(accumulated_content) > sent_content_length:
                        # 关键改变：对累积的完整内容进行清洗，然后只发送新增的部分
                        # include_reasoning 控制是否移除思考内容
                        remove_thinking = not include_reasoning
                        cleaned_full_content = self._clean_content(accumulated_content, remove_thinking=remove_thinking)

                        # 计算已发送的清洗后内容长度（通过清洗之前发送的部分）
                        if sent_content_length > 0:
                            cleaned_sent_content = self._clean_content(accumulated_content[:sent_content_length], remove_thinking=remove_thinking)
                            cleaned_sent_length = len(cleaned_sent_content)
                        else:
                            cleaned_sent_length = 0

                        # 提取新的清洗后内容
                        if len(cleaned_full_content) > cleaned_sent_length:
                            cleaned_new_content = cleaned_full_content[cleaned_sent_length:]
                            logger.debug(f"[流式处理] 清洗后新内容长度={len(cleaned_new_content)}, 原始长度={len(accumulated_content) - sent_content_length}, remove_thinking={remove_thinking}")

                            if cleaned_new_content:
                                logger.info(f"[发送给客户端] 内容: {cleaned_new_content[:100]}...")
                                chunk = create_chat_completion_chunk(
                                    request_id,
                                    model_name,
                                    content=cleaned_new_content
                                )
                                yield create_sse_data(chunk)

                        sent_content_length = len(accumulated_content)
                        logger.debug(f"[流式处理] 更新已发送原始内容长度={sent_content_length}")

                # 发送完成标志
                final_chunk = create_chat_completion_chunk(request_id, model_name, finish_reason="stop")
                yield create_sse_data(final_chunk)
                yield DONE_CHUNK

            except Exception as e:
                error_message = f"处理 Notion AI 流时发生意外错误: {str(e)}"
                logger.error(error_message, exc_info=True)
                error_chunk = {"error": {"message": error_message, "type": "internal_server_error"}}
                yield create_sse_data(error_chunk)
                yield DONE_CHUNK

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    def _prepare_headers(self) -> Dict[str, str]:
        cookie_source = (settings.NOTION_COOKIE or "").strip()
        cookie_header = cookie_source if "=" in cookie_source else f"token_v2={cookie_source}"

        return {
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
            "Cookie": cookie_header,
            "x-notion-space-id": settings.NOTION_SPACE_ID,
            "x-notion-active-user-header": settings.NOTION_USER_ID,
            "x-notion-client-version": settings.NOTION_CLIENT_VERSION,
            "notion-audit-log-platform": "web",
            "Origin": "https://www.notion.so",
            "Referer": "https://www.notion.so/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }

    def _normalize_block_id(self, block_id: str) -> str:
        if not block_id: return block_id
        b = block_id.replace("-", "").strip()
        if len(b) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", b):
            return f"{b[0:8]}-{b[8:12]}-{b[12:16]}-{b[16:20]}-{b[20:]}"
        return block_id

    def _prepare_payload(self, request_data: Dict[str, Any], thread_id: str, mapped_model: str, thread_type: str) -> Dict[str, Any]:
        req_block_id = request_data.get("notion_block_id") or settings.NOTION_BLOCK_ID
        normalized_block_id = self._normalize_block_id(req_block_id) if req_block_id else None

        context_value: Dict[str, Any] = {
            "timezone": "Asia/Shanghai",
            "spaceId": settings.NOTION_SPACE_ID,
            "userId": settings.NOTION_USER_ID,
            "userEmail": settings.NOTION_USER_EMAIL,
            "currentDatetime": datetime.now().astimezone().isoformat(),
        }
        if normalized_block_id:
            context_value["blockId"] = normalized_block_id

        config_value: Dict[str, Any]

        if mapped_model.startswith("vertex-"):
            logger.info(f"检测到 Gemini 模型 ({mapped_model})，应用特定的 config 和 context。")
            context_value.update({
                "userName": f" {settings.NOTION_USER_NAME}",
                "spaceName": f"{settings.NOTION_USER_NAME}的 Notion",
                "spaceViewId": "2008eefa-d0dc-80d5-9e67-000623befd8f",
                "surface": "ai_module"
            })
            config_value = {
                "type": thread_type,
                "model": mapped_model,
                "useWebSearch": True,
                "enableAgentAutomations": False, "enableAgentIntegrations": False,
                "enableBackgroundAgents": False, "enableCodegenIntegration": False,
                "enableCustomAgents": False, "enableExperimentalIntegrations": False,
                "enableLinkedDatabases": False, "enableAgentViewVersionHistoryTool": False,
                "searchScopes": [{"type": "everything"}], "enableDatabaseAgents": False,
                "enableAgentComments": False, "enableAgentForms": False,
                "enableAgentMakesFormulas": False, "enableUserSessionContext": False,
                "modelFromUser": True, "isCustomAgent": False
            }
        else:
            context_value.update({
                "userName": settings.NOTION_USER_NAME,
                "surface": "workflows"
            })
            config_value = {
                "type": thread_type,
                "model": mapped_model,
                "useWebSearch": True,
            }

        transcript = [
            {"id": str(uuid.uuid4()), "type": "config", "value": config_value},
            {"id": str(uuid.uuid4()), "type": "context", "value": context_value}
        ]

        for msg in request_data.get("messages", []):
            if msg.get("role") == "user":
                transcript.append({
                    "id": str(uuid.uuid4()),
                    "type": "user",
                    "value": [[msg.get("content")]],
                    "userId": settings.NOTION_USER_ID,
                    "createdAt": datetime.now().astimezone().isoformat()
                })
            elif msg.get("role") == "assistant":
                transcript.append({"id": str(uuid.uuid4()), "type": "agent-inference", "value": [{"type": "text", "content": msg.get("content")}]})

        payload = {
            "traceId": str(uuid.uuid4()),
            "spaceId": settings.NOTION_SPACE_ID,
            "transcript": transcript,
            "threadId": thread_id,
            "createThread": False,
            "isPartialTranscript": True,
            "asPatchResponse": True,
            "generateTitle": True,
            "saveAllThreadOperations": True,
            "threadType": thread_type
        }

        if mapped_model.startswith("vertex-"):
            logger.info("为 Gemini 请求添加 debugOverrides。")
            payload["debugOverrides"] = {
                "emitAgentSearchExtractedResults": True,
                "cachedInferences": {},
                "annotationInferences": {},
                "emitInferences": False
            }

        return payload

    def _clean_content(self, content: str, remove_thinking: bool = True) -> str:
        """完整清洗内容 - 移除特殊标记和思考内容

        Args:
            content: 要清洗的内容
            remove_thinking: 是否移除思考内容（默认为 True）
        """
        if not content:
            return ""

        # 始终移除语言标记
        content = re.sub(r'<lang primary="[^"]*"\s*/>\n*', '', content)

        # 始终移除 XML 思考标签
        content = re.sub(r'<thinking>[\s\S]*?</thinking>\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'<thought>[\s\S]*?</thought>\s*', '', content, flags=re.IGNORECASE)

        # 只有在 remove_thinking=True 时才移除思考内容模式
        if remove_thinking:
            content = re.sub(r'^.*?Chinese whatmodel I am.*?Theyspecifically.*?requested.*?me.*?to.*?reply.*?in.*?Chinese\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?This.*?is.*?a.*?straightforward.*?question.*?about.*?my.*?identity.*?asan.*?AI.*?assistant\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?Idon\'t.*?need.*?to.*?use.*?any.*?tools.*?for.*?this.*?-\s*it\'s.*?asimple.*?informational.*?response.*?aboutwhat.*?I.*?am\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?Sincethe.*?user.*?asked.*?in.*?Chinese.*?and.*?specifically.*?requested.*?a.*?Chinese.*?response.*?I.*?should.*?respond.*?in.*?Chinese\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?What model are you.*?in Chinese and specifically requesting.*?me.*?to.*?reply.*?in.*?Chinese\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?This.*?is.*?a.*?question.*?about.*?my.*?identity.*?not requiring.*?any.*?tool.*?use.*?I.*?should.*?respond.*?directly.*?to.*?the.*?user.*?in.*?Chinese.*?as.*?requested\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?I.*?should.*?identify.*?myself.*?as.*?Notion.*?AI.*?as.*?mentioned.*?in.*?the.*?system.*?prompt.*?\s*', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'^.*?I.*?should.*?not.*?make.*?specific.*?claims.*?about.*?the.*?underlying.*?model.*?architecture.*?since.*?that.*?information.*?is.*?not.*?provided.*?in.*?my.*?context\.\s*', '', content, flags=re.IGNORECASE | re.DOTALL)

        return content.strip()

    def _clean_content_incremental(self, content: str) -> str:
        """清洗增量内容 - 用于流式输出，保守清洗避免破坏未完成的内容"""
        if not content:
            return ""

        original_content = content
        logger.debug(f"[内容清洗] 原始内容: {content[:200]}")

        # 只移除明显的语言标记
        content = re.sub(r'<lang primary="[^"]*"\s*/>\n*', '', content)

        # 对于思考标签，如果是完整的则移除，否则保留（可能还在传输中）
        has_thinking = '<thinking>' in content.lower() or '<thought>' in content.lower()
        has_thinking_end = '</thinking>' in content.lower() or '</thought>' in content.lower()

        logger.debug(f"[内容清洗] 检测到思考标签: has_thinking={has_thinking}, has_thinking_end={has_thinking_end}")

        if '<thinking>' in content.lower() and '</thinking>' in content.lower():
            before_len = len(content)
            content = re.sub(r'<thinking>[\s\S]*?</thinking>\s*', '', content, flags=re.IGNORECASE)
            logger.debug(f"[内容清洗] 移除了 <thinking> 标签，长度 {before_len} -> {len(content)}")
        if '<thought>' in content.lower() and '</thought>' in content.lower():
            before_len = len(content)
            content = re.sub(r'<thought>[\s\S]*?</thought>\s*', '', content, flags=re.IGNORECASE)
            logger.debug(f"[内容清洗] 移除了 <thought> 标签，长度 {before_len} -> {len(content)}")

        if original_content != content:
            logger.debug(f"[内容清洗] 清洗后内容: {content[:200]}")
        else:
            logger.debug(f"[内容清洗] 内容未改变")

        return content

    def _extract_thinking_content(self, content: str) -> str:
        """提取思考内容"""
        thinking_parts = []

        # 提取 <thinking> 标签内容
        thinking_matches = re.findall(r'<thinking>([\s\S]*?)</thinking>', content, flags=re.IGNORECASE)
        thinking_parts.extend(thinking_matches)

        # 提取 <thought> 标签内容
        thought_matches = re.findall(r'<thought>([\s\S]*?)</thought>', content, flags=re.IGNORECASE)
        thinking_parts.extend(thought_matches)

        return '\n'.join(thinking_parts).strip()

    def _parse_ndjson_line_to_texts(self, line: bytes) -> List[Tuple[str, str]]:
        """解析 NDJSON 行，返回 (类型, 内容) 元组列表

        类型可以是:
        - 'final': 完整的最终内容
        - 'incremental': 增量内容片段
        - 'thinking': 思考/推理内容
        """
        results: List[Tuple[str, str]] = []
        try:
            s = line.decode("utf-8", errors="ignore").strip()
            if not s: return results

            data = json.loads(s)

            # 详细调试日志 - 输出完整的原始响应
            logger.debug("="*80)
            logger.debug(f"原始响应类型: {data.get('type')}")
            logger.debug(f"完整原始响应数据:\n{json.dumps(data, ensure_ascii=False, indent=2)}")
            logger.debug("="*80)

            # 格式1: Gemini 返回的 markdown-chat 事件
            if data.get("type") == "markdown-chat":
                content = data.get("value", "")
                if content:
                    logger.debug("从 'markdown-chat' 直接事件中提取到内容。")
                    # 提取思考内容
                    thinking = self._extract_thinking_content(content)
                    if thinking:
                        results.append(('thinking', thinking))
                    results.append(('final', content))

            # 格式2: Claude 和 GPT 返回的补丁流，以及 Gemini 的 patch 格式
            elif data.get("type") == "patch" and "v" in data:
                for operation in data.get("v", []):
                    if not isinstance(operation, dict): continue

                    op_type = operation.get("o")
                    path = operation.get("p", "")
                    value = operation.get("v")

                    # Gemini 的完整内容 patch 格式
                    if op_type == "a" and path.endswith("/s/-") and isinstance(value, dict) and value.get("type") == "markdown-chat":
                        content = value.get("value", "")
                        if content:
                            logger.debug("从 'patch' (Gemini-style) 中提取到完整内容。")
                            # 提取思考内容
                            thinking = self._extract_thinking_content(content)
                            if thinking:
                                results.append(('thinking', thinking))
                            results.append(('final', content))

                    # Gemini 的增量内容 patch 格式
                    elif op_type == "x" and "/s/" in path and path.endswith("/value") and isinstance(value, str):
                        content = value
                        if content:
                            logger.debug(f"从 'patch' (Gemini增量) 中提取到内容片段")
                            # 增量内容也可能包含思考标签
                            thinking = self._extract_thinking_content(content)
                            if thinking:
                                results.append(('thinking', thinking))
                            results.append(('incremental', content))

                    # Claude 和 GPT 的增量内容 patch 格式
                    elif op_type == "x" and "/value/" in path and isinstance(value, str):
                        content = value
                        if content:
                            logger.debug(f"从 'patch' (Claude/GPT增量) 中提取到内容片段")
                            thinking = self._extract_thinking_content(content)
                            if thinking:
                                results.append(('thinking', thinking))
                            results.append(('incremental', content))

                    # Claude 和 GPT 的完整内容 patch 格式
                    elif op_type == "a" and path.endswith("/value/-") and isinstance(value, dict) and value.get("type") == "text":
                        content = value.get("content", "")
                        if content:
                            logger.debug("从 'patch' (Claude/GPT-style) 中提取到完整内容。")
                            thinking = self._extract_thinking_content(content)
                            if thinking:
                                results.append(('thinking', thinking))
                            results.append(('final', content))

            # 格式3: 处理record-map类型的数据
            elif data.get("type") == "record-map" and "recordMap" in data:
                record_map = data["recordMap"]
                if "thread_message" in record_map:
                    for msg_id, msg_data in record_map["thread_message"].items():
                        value_data = msg_data.get("value", {}).get("value", {})
                        step = value_data.get("step", {})
                        if not step: continue

                        content = ""
                        step_type = step.get("type")

                        if step_type == "markdown-chat":
                            content = step.get("value", "")
                        elif step_type == "agent-inference":
                            agent_values = step.get("value", [])
                            if isinstance(agent_values, list):
                                for item in agent_values:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        content = item.get("content", "")
                                        break

                        if content and isinstance(content, str):
                            logger.debug(f"从 record-map (type: {step_type}) 提取到最终内容。")
                            thinking = self._extract_thinking_content(content)
                            if thinking:
                                results.append(('thinking', thinking))
                            results.append(('final', content))
                            break

        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"解析NDJSON行失败: {e} - Line: {line.decode('utf-8', errors='ignore')}")

        # 输出解析结果的详细信息
        if results:
            logger.debug(f"本次解析得到 {len(results)} 个结果:")
            for idx, (text_type, content) in enumerate(results):
                preview = content[:200] if len(content) > 200 else content
                logger.debug(f"  [{idx}] 类型={text_type}, 内容长度={len(content)}, 预览: {preview}")

        return results

    async def get_models(self) -> JSONResponse:
        model_data = {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": int(time.time()), "owned_by": "lzA6"}
                for name in settings.KNOWN_MODELS
            ]
        }
        return JSONResponse(content=model_data)
