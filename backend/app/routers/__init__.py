"""业务路由汇总：跨模块流水线优先注册，通用资源路由兜底模块资源。"""
from __future__ import annotations

from app.routers import pipeline, resource

ROUTERS = [pipeline, resource]
