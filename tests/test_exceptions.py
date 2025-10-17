# tests/test_exceptions.py
"""异常类测试"""
import pytest
from app.core.exceptions import (
    NotionAPIException,
    NotionAuthenticationError,
    NotionConfigurationError,
    NotionThreadCreationError,
    ModelNotSupportedError,
    NotionRateLimitError
)


def test_notion_authentication_error():
    """测试认证错误"""
    error = NotionAuthenticationError()
    assert error.status_code == 401
    assert error.error_type == "authentication_error"


def test_notion_configuration_error():
    """测试配置错误"""
    error = NotionConfigurationError("配置无效")
    assert error.status_code == 500
    assert error.message == "配置无效"
    assert error.error_type == "configuration_error"


def test_notion_rate_limit_error():
    """测试速率限制错误"""
    error = NotionRateLimitError()
    assert error.status_code == 429
    assert error.error_type == "rate_limit_error"


def test_model_not_supported_error():
    """测试不支持的模型错误"""
    error = ModelNotSupportedError("gpt-99")
    assert error.status_code == 400
    assert "gpt-99" in error.message
    assert error.error_type == "invalid_model"


def test_notion_thread_creation_error():
    """测试线程创建错误"""
    error = NotionThreadCreationError("无法创建线程")
    assert error.status_code == 500
    assert "无法创建线程" in error.message
