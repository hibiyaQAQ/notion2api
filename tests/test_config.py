# tests/test_config.py
"""配置模块测试"""
import pytest
import os
from pydantic import ValidationError
from app.core.config import Settings


def test_settings_in_test_environment():
    """测试在测试环境中可以创建Settings（会自动填充默认值）"""
    # 测试环境中应该允许创建 Settings
    settings = Settings(
        NOTION_COOKIE=None,
        NOTION_SPACE_ID="test-space",
        NOTION_USER_ID="test-user"
    )
    assert settings.NOTION_COOKIE == "test-value"  # 自动填充


def test_settings_with_valid_values():
    """测试使用有效值创建Settings"""
    settings = Settings(
        NOTION_COOKIE="test-cookie",
        NOTION_SPACE_ID="test-space",
        NOTION_USER_ID="test-user"
    )
    assert settings.NOTION_COOKIE == "test-cookie"
    assert settings.NOTION_SPACE_ID == "test-space"
    assert settings.NOTION_USER_ID == "test-user"


def test_log_level_validation():
    """测试日志级别验证"""
    with pytest.raises(ValidationError):
        Settings(
            NOTION_COOKIE="test",
            NOTION_SPACE_ID="test",
            NOTION_USER_ID="test",
            LOG_LEVEL="INVALID"
        )


def test_default_model():
    """测试默认模型配置"""
    settings = Settings(
        NOTION_COOKIE="test",
        NOTION_SPACE_ID="test",
        NOTION_USER_ID="test"
    )
    assert settings.DEFAULT_MODEL == "claude-sonnet-4.5"


def test_model_mapping():
    """测试模型映射"""
    settings = Settings(
        NOTION_COOKIE="test",
        NOTION_SPACE_ID="test",
        NOTION_USER_ID="test"
    )
    assert "claude-sonnet-4.5" in settings.MODEL_MAP
    assert settings.MODEL_MAP["claude-sonnet-4.5"] == "anthropic-sonnet-alt"
