"""调用日志业务规则：状态流转、字段校验与筛选口径都收在这里。

数据流：原始请求记录 → 标准化解析（请求时间、返回码、响应大小取自同一条
原始记录）→ 标准化日志记录落库 → 同步写入日志记录契约，指标聚合按返回码
统计异常。不同口径并存时，解析优先级与判定依据都留在「解析依据」里，可从
标准化日志记录复核。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "log"
CONTRACT_MODULE = "logcontract"  # 日志记录契约：标准化日志记录的同步落点
REQUIRED_FIELDS = ["记录编号", "调用服务", "请求时间"]
ALL_FIELDS = ["记录编号", "调用服务", "请求时间", "响应耗时", "返回码", "调用方IP", "请求大小KB", "响应大小KB"]
STATUS_ORDER = ["草稿", "记录中", "已归档", "已清理"]
ACTION_RULES = {"归档记录": "已归档", "清理日志": "已清理", "导出日志": "已归档"}
NEGATIVE_ACTIONS: list[str] = []
ERROR_STATUS_FLOOR = 400  # 返回码 >= 400 计为异常；2xx/3xx 成功请求不进入错误率

_SIZE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*(B|KB|K|MB|M)?$", re.IGNORECASE)


def _parse_status_code(raw: Any, basis: list[str]) -> int | None:
    """解析返回码：整数口径优先，数字字符串按整数解析，判定依据留痕。"""
    if raw is None or str(raw).strip() == "":
        basis.append("返回码：原始记录未提供，无法确认调用成功，按异常计")
        return None
    if isinstance(raw, bool):
        basis.append(f"返回码：原始值{raw!r}不是合法返回码，按异常计")
        return None
    if isinstance(raw, int):
        basis.append(f"返回码：采用整数口径原始值{raw}")
        return raw
    if isinstance(raw, float) and raw.is_integer():
        basis.append(f"返回码：原始值{raw}为数值口径，取整为{int(raw)}")
        return int(raw)
    text = str(raw).strip()
    if re.fullmatch(r"\d+", text):
        basis.append(f"返回码：原始值'{text}'按数字字符串解析为{int(text)}")
        return int(text)
    basis.append(f"返回码：原始值'{text}'无法解析，无法确认调用成功，按异常计")
    return None


def _parse_size_kb(raw: Any, field: str, basis: list[str]) -> float | None:
    """解析大小字段为 KB：数值口径优先，带单位字符串按单位换算，依据留痕。"""
    if raw is None or str(raw).strip() == "":
        basis.append(f"{field}：原始记录未提供，大小字段留空")
        return None
    if isinstance(raw, bool):
        basis.append(f"{field}：原始值{raw!r}不是合法大小，大小字段留空")
        return None
    if isinstance(raw, (int, float)):
        basis.append(f"{field}：采用数值口径原始值{raw}KB")
        return float(raw)
    text = str(raw).strip()
    match = _SIZE_PATTERN.fullmatch(text)
    if match:
        value = float(match.group(1))
        unit = (match.group(2) or "KB").upper()
        if unit in ("MB", "M"):
            value *= 1024
        elif unit == "B":
            value /= 1024
        basis.append(f"{field}：原始值'{text}'按{unit}口径换算为{value:g}KB")
        return value
    basis.append(f"{field}：原始值'{text}'无法解析，大小字段留空")
    return None


def _parse_raw_record(values: dict[str, Any], basis: list[str]) -> dict[str, Any]:
    """从同一条原始请求记录里一起解析请求时间、返回码和响应大小。

    三个字段必须取自同一条原始记录：分开解析会把不同请求的字段拼接到
    同一条标准化日志记录里。
    """
    return {
        "请求时间": str(values.get("请求时间") or "").strip(),
        "返回码": _parse_status_code(values.get("返回码"), basis),
        "请求大小KB": _parse_size_kb(values.get("请求大小KB"), "请求大小KB", basis),
        "响应大小KB": _parse_size_kb(values.get("响应大小KB"), "响应大小KB", basis),
    }


def _judge_abnormal(status_code: int | None, basis: list[str]) -> bool:
    """异常判定：返回码 >= 400 或缺失计为异常，成功请求不计入错误率。"""
    if status_code is None:
        basis.append("异常判定：返回码缺失，无法确认成功，按异常计")
        return True
    if status_code >= ERROR_STATUS_FLOOR:
        basis.append(f"异常判定：返回码{status_code}>={ERROR_STATUS_FLOOR}，计为异常")
        return True
    basis.append(f"异常判定：返回码{status_code}为成功请求，不计入错误率")
    return False


class LogService:
    def __init__(self) -> None:
        self._bootstrap_standardized_records()

    # ------------------------------------------------------------------
    # 标准化解析与落库
    # ------------------------------------------------------------------
    def _bootstrap_standardized_records(self) -> None:
        """启动时把存量日志记录按同一口径补齐标准化字段，并同步日志记录契约。"""
        for row in store.rows(MODULE):
            if row.get("解析版本"):
                continue
            basis = ["存量记录补解析：按当前口径重新解析原始记录"]
            parsed = _parse_raw_record(row, basis)
            row["返回码"] = parsed["返回码"]
            row["请求大小KB"] = parsed["请求大小KB"]
            row["响应大小KB"] = parsed["响应大小KB"]
            row["abnormal"] = _judge_abnormal(parsed["返回码"], basis)
            row["解析版本"] = 1
            row["解析依据"] = basis
            self._sync_contract(row)

    def _sync_contract(self, entry: dict[str, Any]) -> None:
        """把标准化日志记录同步写入日志记录契约，指标聚合按返回码统计异常。"""
        rows = store.rows(CONTRACT_MODULE)
        code = str(entry.get("记录编号") or "")
        contract = next((row for row in rows if row.get("记录编号") == code), None)
        if contract is None:
            contract = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "契约编号": f"LCON-{len(rows) + 1:04d}",
            }
            rows.append(contract)
        contract.update({
            "记录编号": code,
            "返回码": entry.get("返回码"),
            "是否异常": bool(entry.get("abnormal")),
            "请求时间": entry.get("请求时间"),
            "响应大小KB": entry.get("响应大小KB"),
            "解析版本": entry.get("解析版本"),
            "同步说明": f"与标准化日志记录{code}第{entry.get('解析版本')}版解析结果一致",
        })

    def _find_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("记录编号") or "") == code:
                return row
        return None

    @staticmethod
    def _same_parameters(entry: dict[str, Any], values: dict[str, Any], parsed: dict[str, Any]) -> bool:
        """同一参数请求判定：标准化结果与已提供的原始字段全都一致才算重复提交。"""
        if str(entry.get("请求时间") or "") != parsed["请求时间"]:
            return False
        for field in ("返回码", "请求大小KB", "响应大小KB"):
            if entry.get(field) != parsed[field]:
                return False
        for field in ("调用服务", "响应耗时", "调用方IP"):
            incoming = values.get(field)
            if incoming is None or str(incoming).strip() == "":
                continue  # 重复提交未携带的字段不参与比对
            if str(entry.get(field) or "") != str(incoming):
                return False
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        status_code: str | None = None,
        request_time: str | None = None,
        abnormal_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """检索返回码、筛选请求时间并分页定位异常记录；非法页码返回空页。"""
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if status_code is not None and str(status_code).strip() != "":
            code = str(status_code).strip()
            rows = [row for row in rows if str(row.get("返回码")) == code]
        if request_time:
            rows = [row for row in rows if str(request_time) in str(row.get("请求时间", ""))]
        if abnormal_only:
            rows = [row for row in rows if row.get("abnormal")]
        total = len(rows)
        if page < 1 or size < 1:
            return [], total  # 非法页码返回空页，不报错
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ------------------------------------------------------------------
    # 登记：幂等去重 + 迟到更正
    # ------------------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        code = str(values.get("记录编号") or "").strip()
        basis: list[str] = []
        parsed = _parse_raw_record(values, basis)
        existing = self._find_by_code(code)
        if existing is not None:
            if self._same_parameters(existing, values, parsed):
                return existing, [], f"记录编号{code}为重复提交，已保留原处理结果"
            entry = self._apply_correction(existing, values, parsed, basis)
            return entry, [], f"记录编号{code}已存在，按迟到更正保留原请求时间并更新解析结果"
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "记录编号": code,
            "调用服务": values.get("调用服务"),
            "请求时间": parsed["请求时间"],
            "响应耗时": values.get("响应耗时"),
            "返回码": parsed["返回码"],
            "调用方IP": values.get("调用方IP"),
            "请求大小KB": parsed["请求大小KB"],
            "响应大小KB": parsed["响应大小KB"],
            "status": STATUS_ORDER[0],
            "pending": True,
            "解析版本": 1,
        }
        entry["abnormal"] = _judge_abnormal(parsed["返回码"], basis)
        entry["解析依据"] = basis
        rows.append(entry)
        self._sync_contract(entry)
        return entry, [], "日志记录已登记，标准化解析结果已同步日志记录契约"

    def _apply_correction(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        parsed: dict[str, Any],
        basis: list[str],
    ) -> dict[str, Any]:
        """迟到更正：保留原请求时间，整组采用新记录的返回码与大小解析结果。"""
        version = int(entry.get("解析版本", 1)) + 1
        basis.insert(0, f"迟到更正：保留原请求时间{entry.get('请求时间')}，采用第{version}版解析结果")
        for field in ("调用服务", "响应耗时", "调用方IP"):
            incoming = values.get(field)
            if incoming is not None and str(incoming).strip() != "":
                entry[field] = incoming
        entry["返回码"] = parsed["返回码"]
        entry["请求大小KB"] = parsed["请求大小KB"]
        entry["响应大小KB"] = parsed["响应大小KB"]
        entry["abnormal"] = _judge_abnormal(parsed["返回码"], basis)
        entry["解析版本"] = version
        entry["解析依据"] = list(entry.get("解析依据") or []) + basis
        self._sync_contract(entry)
        return entry

    # ------------------------------------------------------------------
    # 动作与契约
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于调用日志可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = entry["abnormal"] or action in NEGATIVE_ACTIONS
        return entry, f"日志记录已{action}"

    def list_contract_entries(self, *, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(CONTRACT_MODULE)
        total = len(rows)
        if page < 1 or size < 1:
            return [], total
        start = (page - 1) * size
        return rows[start:start + size], total

    def contract_stats(self) -> dict[str, Any]:
        """日志记录契约指标聚合：按返回码统计异常，成功请求不计入错误率。"""
        rows = store.rows(CONTRACT_MODULE)
        total = len(rows)
        abnormal = sum(1 for row in rows if row.get("是否异常"))
        by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get("返回码")) if row.get("返回码") is not None else "未解析"
            bucket = by_code.setdefault(label, {"返回码": label, "调用数": 0, "异常数": 0})
            bucket["调用数"] += 1
            if row.get("是否异常"):
                bucket["异常数"] += 1
        return {
            "total": total,
            "abnormal": abnormal,
            "error_rate": round(abnormal / total, 4) if total else 0.0,
            "by_status_code": sorted(by_code.values(), key=lambda item: item["返回码"]),
        }
