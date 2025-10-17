# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List, Optional
import logging

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding='utf-8',
        extra="ignore"
    )

    APP_NAME: str = "notion-2api"
    APP_VERSION: str = "4.2.0"  # 使用 reasoning_content 独立字段返回思考内容
    DESCRIPTION: str = "一个将 Notion AI 转换为兼容 OpenAI 格式 API 的高性能代理。"

    # 日志配置
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL

    API_MASTER_KEY: Optional[str] = None

    # --- Notion 凭证 ---
    NOTION_COOKIE: Optional[str] = None
    NOTION_SPACE_ID: Optional[str] = None
    NOTION_USER_ID: Optional[str] = None
    NOTION_USER_NAME: Optional[str] = None
    NOTION_USER_EMAIL: Optional[str] = None
    NOTION_BLOCK_ID: Optional[str] = None
    NOTION_CLIENT_VERSION: Optional[str] = "23.13.20251011.2037"

    # 超时配置（秒）
    API_REQUEST_TIMEOUT: int = 300  # 从 180 增加到 300 秒（5分钟）
    STREAM_READ_TIMEOUT: int = 600  # 流式读取总超时（10分钟）
    NGINX_PORT: int = 8088

    # 速率限制配置
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 10  # 每分钟请求数

    # 重试配置
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0  # 秒

    # 【最终修正】更新所有已知的模型列表
    DEFAULT_MODEL: str = "claude-sonnet-4.5"

    KNOWN_MODELS: List[str] = [
        "claude-sonnet-4.5",
        "gpt-5",
        "claude-opus-4.1",
        "gemini-2.5-flash（未修复，不可用）",
        "gemini-2.5-pro（未修复，不可用）",
        "gpt-4.1"
    ]

    # 【最终修正】根据您提供的信息，填充所有模型的真实后台名称
    MODEL_MAP: dict = {
        "claude-sonnet-4.5": "anthropic-sonnet-alt",
        "gpt-5": "openai-turbo",
        "claude-opus-4.1": "anthropic-opus-4.1",
        "gemini-2.5-flash（未修复，不可用）": "vertex-gemini-2.5-flash",
        "gemini-2.5-pro（未修复，不可用）": "vertex-gemini-2.5-pro",
        "gpt-4.1": "openai-gpt-4.1"
    }

    @field_validator('NOTION_COOKIE', 'NOTION_SPACE_ID', 'NOTION_USER_ID', mode='before')
    @classmethod
    def validate_required_fields(cls, v, info):
        """验证必需的 Notion 凭证字段（仅在非测试环境）"""
        import os
        # 在测试环境中跳过验证
        if os.getenv('PYTEST_CURRENT_TEST') or os.getenv('TESTING'):
            return v or "test-value"

        if not v or str(v).strip() == "":
            raise ValueError(f"{info.field_name} 是必需的配置项,请在 .env 文件中设置")
        return v

    @field_validator('LOG_LEVEL')
    @classmethod
    def validate_log_level(cls, v):
        """验证日志级别"""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"LOG_LEVEL 必须是以下值之一: {', '.join(valid_levels)}")
        return v_upper

    def get_log_level(self) -> int:
        """获取日志级别常量"""
        return getattr(logging, self.LOG_LEVEL)

settings = Settings()
