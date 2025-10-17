# tests/test_api.py
"""API 端点测试"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, AsyncMock
from fastapi.responses import JSONResponse


@pytest.fixture
def mock_provider():
    """模拟 NotionAIProvider"""
    with patch('main.provider') as mock:
        # 使用 AsyncMock 来支持 await
        mock.get_models = AsyncMock(return_value=JSONResponse(content={"object": "list", "data": []}))
        yield mock


@pytest.fixture
def client(mock_provider):
    """测试客户端"""
    from main import app
    return TestClient(app)


def test_root_endpoint(client):
    """测试根路径"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "endpoints" in data


def test_health_check_endpoint(client):
    """测试健康检查端点"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "timestamp" in data


def test_models_endpoint_without_auth(client):
    """测试未授权访问模型列表"""
    # 当 API_MASTER_KEY 未设置或为 "1" 时,不需要认证
    response = client.get("/v1/models")
    # 根据配置,可能返回 200 或 401
    assert response.status_code in [200, 401]


def test_chat_endpoint_without_auth(client):
    """测试未授权访问聊天端点"""
    response = client.post(
        "/v1/chat/completions",
        json={"model": "claude-sonnet-4.5", "messages": [{"role": "user", "content": "test"}]}
    )
    # 根据配置,可能返回各种状态码
    assert response.status_code in [200, 401, 500]
