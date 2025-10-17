# main.py
import logging
from contextlib import asynccontextmanager
from typing import Optional
import time
import os

# 禁用 slowapi 自动加载 .env 文件（避免编码问题）
os.environ.setdefault('SLOWAPI_DISABLE_DOTENV', '1')

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.exceptions import (
    NotionAPIException,
    NotionAuthenticationError,
    NotionConfigurationError,
    ModelNotSupportedError
)
from app.providers.notion_provider import NotionAIProvider

# 配置日志
logging.basicConfig(
    level=settings.get_log_level(),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化速率限制器
limiter = Limiter(key_func=get_remote_address)

# 初始化 provider
try:
    provider = NotionAIProvider()
except Exception as e:
    logger.error(f"初始化 NotionAIProvider 失败: {e}", exc_info=True)
    provider = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"应用启动中... {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"日志级别: {settings.LOG_LEVEL}")
    if provider is None:
        logger.error("NotionAIProvider 初始化失败,服务可能无法正常工作")
    else:
        logger.info("服务已配置为 Notion AI 代理模式。")
    logger.info(f"服务将在 http://localhost:{settings.NGINX_PORT} 上可用")
    if settings.RATE_LIMIT_ENABLED:
        logger.info(f"速率限制已启用: {settings.RATE_LIMIT_REQUESTS} 请求/分钟")
    yield
    logger.info("应用关闭。")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan
)

# 注册速率限制器
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 全局异常处理器
@app.exception_handler(NotionAPIException)
async def notion_exception_handler(request: Request, exc: NotionAPIException):
    """处理自定义 Notion API 异常"""
    logger.error(f"Notion API 异常: {exc.message} (类型: {exc.error_type})")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "type": exc.error_type,
                "code": exc.status_code
            }
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理未捕获的异常"""
    logger.error(f"未处理的异常: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "服务器内部错误",
                "type": "internal_server_error",
                "code": 500
            }
        }
    )

async def verify_api_key(authorization: Optional[str] = Header(None)):
    """验证 API Key"""
    if settings.API_MASTER_KEY and settings.API_MASTER_KEY != "1":
        if not authorization or "bearer" not in authorization.lower():
            raise NotionAuthenticationError("需要 Bearer Token 认证")
        token = authorization.split(" ")[-1]
        if token != settings.API_MASTER_KEY:
            raise NotionAuthenticationError("无效的 API Key")

def get_rate_limit():
    """获取速率限制字符串"""
    if settings.RATE_LIMIT_ENABLED:
        return f"{settings.RATE_LIMIT_REQUESTS}/minute"
    return None

@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
@limiter.limit(get_rate_limit() or "1000/minute")  # 如果未启用则设置一个很高的限制
async def chat_completions(request: Request) -> StreamingResponse:
    """聊天完成端点"""
    if provider is None:
        raise NotionConfigurationError("服务未正确初始化,请检查配置")

    try:
        request_data = await request.json()
        logger.debug(f"收到聊天请求: {request_data.get('model', 'unknown')}")
        return await provider.chat_completion(request_data)
    except NotionAPIException:
        # 直接重新抛出自定义异常,由全局处理器处理
        raise
    except Exception as e:
        logger.error(f"处理聊天请求时发生错误: {e}", exc_info=True)
        raise NotionAPIException(f"处理请求时发生错误: {str(e)}")

@app.get("/v1/models", dependencies=[Depends(verify_api_key)], response_class=JSONResponse)
async def list_models():
    """列出可用模型"""
    if provider is None:
        raise NotionConfigurationError("服务未正确初始化,请检查配置")
    return await provider.get_models()

@app.get("/health")
async def health_check():
    """健康检查端点"""
    health_status = {
        "status": "healthy" if provider is not None else "unhealthy",
        "version": settings.APP_VERSION,
        "timestamp": int(time.time())
    }

    if provider is None:
        health_status["error"] = "Provider 未初始化"
        return JSONResponse(content=health_status, status_code=503)

    return JSONResponse(content=health_status)

@app.get("/", summary="根路径")
def root():
    """根路径信息"""
    return {
        "message": f"欢迎来到 {settings.APP_NAME} v{settings.APP_VERSION}",
        "status": "运行中",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "models": "/v1/models",
            "health": "/health"
        }
    }
