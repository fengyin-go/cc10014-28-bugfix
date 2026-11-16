"""调用日志接口：维护日志记录，覆盖归档记录、清理日志、导出日志等动作。

数据流：登记 → 标准化解析（请求时间、返回码、响应大小同条解析）→ 标准化
日志记录落库 → 同步日志记录契约。清单支持检索返回码、筛选请求时间并分页
定位异常记录，非法页码返回空页。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.log import LogService

router = APIRouter(prefix="/api/log", tags=["调用日志"])

service = LogService()

LIST_FIELDS = ["记录编号", "调用服务", "请求时间", "响应耗时", "返回码", "调用方IP", "请求大小KB", "响应大小KB"]
STATUSES = ["草稿", "记录中", "已归档", "已清理"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="草稿、记录中、已归档、已清理"),
    status_code: str | None = Query(default=None, alias="返回码", description="按返回码精确检索"),
    request_time: str | None = Query(default=None, alias="请求时间", description="按请求时间筛选"),
    abnormal: bool = Query(default=False, description="仅看异常记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """检索返回码、筛选请求时间并分页定位异常记录；非法页码返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        status_code=status_code,
        request_time=request_time,
        abnormal_only=abnormal,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条日志记录：完整解析返回码、调用方地址与响应大小后落库。

    重复提交同一参数请求只保留一份处理结果；同一记录编号迟到更正时保留
    原请求时间并采用新解析结果。缺字段时说明原因而不是静默丢弃。
    """
    entry, missing, message = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出调用日志清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "log", "total": total, "items": items}


@router.get("/contract", response_model=PageResult[dict])
def list_contract_entries(page: int = 1, size: int = 20) -> PageResult[dict]:
    """日志记录契约清单：标准化日志记录的同步落点，可逐条复核。"""
    items, total = service.list_contract_entries(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/contract/stats")
def contract_stats() -> dict[str, Any]:
    """日志记录契约指标聚合：按返回码统计异常，成功请求不计入错误率。"""
    return service.contract_stats()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条日志记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日志记录 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条日志记录执行归档记录、清理日志、导出日志；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
