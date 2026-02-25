#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хранилище StartaleGM в JSON: EOA-адреса, время до следующего GM, наличие смарт-аккаунта.
Приватные ключи не хранятся; связь адрес ↔ ключ только через keys.txt по индексу.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = PROJECT_ROOT / "startalegm.json"


def _read_data() -> dict[str, Any]:
    if not JSON_PATH.exists():
        return {"accounts": {}}
    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return {"accounts": {}}
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"accounts": {}}


def _write_data(data: dict[str, Any]) -> None:
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def init_db() -> None:
    """Создаёт JSON-файл с пустым списком аккаунтов, если его нет."""
    if not JSON_PATH.exists():
        _write_data({"accounts": {}})


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_account(
    eoa_address: str,
    *,
    next_gm_available_at: Optional[datetime] = None,
    smart_account_created: Optional[bool] = None,
) -> None:
    """Вставляет или обновляет запись по EOA. Переданные None не обновляют поле."""
    init_db()
    data = _read_data()
    accounts = data.setdefault("accounts", {})
    now = _now_utc()
    if eoa_address not in accounts:
        accounts[eoa_address] = {
            "next_gm_available_at": next_gm_available_at.isoformat() if next_gm_available_at else None,
            "smart_account_created": bool(smart_account_created) if smart_account_created is not None else False,
            "updated_at": now,
        }
    else:
        rec = accounts[eoa_address]
        if next_gm_available_at is not None:
            rec["next_gm_available_at"] = next_gm_available_at.isoformat()
        if smart_account_created is not None:
            rec["smart_account_created"] = bool(smart_account_created)
        rec["updated_at"] = now
    _write_data(data)


def get_account_info(eoa_address: str) -> Optional[dict]:
    """Возвращает запись по адресу или None."""
    init_db()
    data = _read_data()
    accounts = data.get("accounts", {})
    if eoa_address not in accounts:
        return None
    rec = accounts[eoa_address].copy()
    rec["eoa_address"] = eoa_address
    rec["smart_account_created"] = bool(rec.get("smart_account_created", False))
    return rec


def get_all_addresses() -> list[str]:
    """Список всех EOA-адресов в хранилище."""
    init_db()
    data = _read_data()
    return list(data.get("accounts", {}).keys())


def get_accounts_due_for_gm(known_addresses: list[str]) -> list[str]:
    """
    Возвращает адреса, для которых пора отправить GM:
    - есть в known_addresses;
    - при этом либо нет в хранилище, либо next_gm_available_at отсутствует/null, либо next_gm_available_at <= now (UTC).
    """
    init_db()
    data = _read_data()
    accounts = data.get("accounts", {})
    now_utc = datetime.now(timezone.utc)
    due = []
    for addr in known_addresses:
        rec = accounts.get(addr)
        if rec is None:
            due.append(addr)
            continue
        next_at_str = rec.get("next_gm_available_at")
        if next_at_str is None:
            due.append(addr)
            continue
        try:
            next_at = datetime.fromisoformat(next_at_str.replace("Z", "+00:00"))
            if next_at <= now_utc:
                due.append(addr)
        except (ValueError, TypeError):
            due.append(addr)
    return due
