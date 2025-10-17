# tests/conftest.py
"""pytest 配置文件"""
import os
import sys

# 设置测试环境标志
os.environ['TESTING'] = '1'

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
