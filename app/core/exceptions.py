# app/core/exceptions.py
"""自定义异常类定义"""

class NotionAPIException(Exception):
    """Notion API 基础异常类"""
    def __init__(self, message: str, status_code: int = 500, error_type: str = "internal_error"):
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(self.message)


class NotionAuthenticationError(NotionAPIException):
    """Notion 认证失败异常"""
    def __init__(self, message: str = "Notion 认证失败,请检查您的凭证配置"):
        super().__init__(message, status_code=401, error_type="authentication_error")


class NotionConfigurationError(NotionAPIException):
    """Notion 配置错误异常"""
    def __init__(self, message: str = "Notion 配置不完整或无效"):
        super().__init__(message, status_code=500, error_type="configuration_error")


class NotionThreadCreationError(NotionAPIException):
    """Notion 线程创建失败异常"""
    def __init__(self, message: str = "无法创建 Notion 对话线程"):
        super().__init__(message, status_code=500, error_type="thread_creation_error")


class NotionRequestError(NotionAPIException):
    """Notion 请求失败异常"""
    def __init__(self, message: str = "Notion API 请求失败", status_code: int = 500):
        super().__init__(message, status_code=status_code, error_type="request_error")


class NotionResponseParseError(NotionAPIException):
    """Notion 响应解析失败异常"""
    def __init__(self, message: str = "无法解析 Notion API 响应"):
        super().__init__(message, status_code=500, error_type="parse_error")


class NotionRateLimitError(NotionAPIException):
    """Notion 速率限制异常"""
    def __init__(self, message: str = "请求频率过高,请稍后重试"):
        super().__init__(message, status_code=429, error_type="rate_limit_error")


class ModelNotSupportedError(NotionAPIException):
    """不支持的模型异常"""
    def __init__(self, model: str):
        message = f"不支持的模型: {model}"
        super().__init__(message, status_code=400, error_type="invalid_model")
