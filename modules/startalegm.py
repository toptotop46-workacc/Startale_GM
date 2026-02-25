#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StartaleGM — импорт кошелька в Rabby и открытие https://portal.soneium.org/
Через AdsPower + Playwright (по аналогии с UI-модулями).
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
import requests
from loguru import logger
from web3 import Web3

from modules import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

PORTAL_URL = "https://portal.soneium.org/"
PROFILE_MAPPING_URL = "https://portal.soneium.org/api/profile/mapping"
STARTALE_LOGIN_URL = "https://app.startale.com/log-in"
STARTALE_APP_URL = "https://app.startale.com/"
RABBY_EXTENSION_ID = "acmacodkjbdgmoleebolmdjonilkdbch"
# Блок с живым счётчиком "Next GM available in X h Y m" на странице app.startale.com
NEXT_GM_TEXT_SELECTOR = "div.relative.z-10 p.text-sm.text-zinc-900"
# Пауза после загрузки app.startale.com, чтобы подтянулись данные текущего аккаунта
WAIT_FOR_GM_DATA_SEC = 10
# Если время следующего GM не удалось получить, ставим «доступен через N минут», чтобы не крутить аккаунт каждые 10 с
FALLBACK_GM_COOLDOWN_MINUTES = 60


def load_private_key(key_index: int = 0) -> str:
    """Загружает приватный ключ из keys.txt по индексу."""
    keys_file = PROJECT_ROOT / "keys.txt"
    if not keys_file.exists():
        raise FileNotFoundError(
            f"Файл {keys_file} не найден. Создайте файл и укажите в нём приватные ключи."
        )
    keys = []
    with open(keys_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if re.match(r"^0x[a-fA-F0-9]{64}$", line):
                    keys.append(line)
                elif re.match(r"^[a-fA-F0-9]{64}$", line):
                    keys.append("0x" + line)
    if not keys:
        raise ValueError(f"В файле {keys_file} не найдено действительных приватных ключей")
    if key_index < 0 or key_index >= len(keys):
        raise ValueError(
            f"Индекс ключа {key_index} вне диапазона (доступно: {len(keys)})"
        )
    return keys[key_index]


def load_all_keys() -> list[str]:
    """Загружает все приватные ключи из keys.txt."""
    keys_file = PROJECT_ROOT / "keys.txt"
    if not keys_file.exists():
        raise FileNotFoundError(f"Файл {keys_file} не найден.")
    keys = []
    with open(keys_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if re.match(r"^0x[a-fA-F0-9]{64}$", line):
                    keys.append(line)
                elif re.match(r"^[a-fA-F0-9]{64}$", line):
                    keys.append("0x" + line)
    if not keys:
        raise ValueError(f"В файле {keys_file} не найдено действительных приватных ключей")
    return keys


def get_address_for_key_index(key_index: int) -> str:
    """Возвращает EOA-адрес (checksum) для ключа по индексу в keys.txt."""
    private_key = load_private_key(key_index)
    return Web3.to_checksum_address(Web3().eth.account.from_key(private_key).address)


def get_key_index_for_address(address: str, keys: Optional[list[str]] = None) -> Optional[int]:
    """Возвращает индекс ключа в keys.txt для данного EOA-адреса или None."""
    if keys is None:
        keys = load_all_keys()
    addr_norm = Web3.to_checksum_address(address)
    for i, pk in enumerate(keys):
        try:
            a = Web3.to_checksum_address(Web3().eth.account.from_key(pk).address)
            if a == addr_norm:
                return i
        except Exception:
            continue
    return None


def load_adspower_api_key() -> str:
    """Загружает API ключ AdsPower из adspower_api_key.txt."""
    api_key_file = PROJECT_ROOT / "adspower_api_key.txt"
    if not api_key_file.exists():
        raise FileNotFoundError(
            f"Файл {api_key_file} не найден. Укажите в нём API ключ AdsPower."
        )
    with open(api_key_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines or not lines[0] or lines[0] == "your_adspower_api_key_here":
        raise ValueError(
            f"В файле {api_key_file} укажите реальный API ключ AdsPower."
        )
    return lines[0]


def load_proxies() -> list[dict[str, str]]:
    """Загружает прокси из proxy.txt. Формат строки: host:port или host:port:user:pass."""
    proxy_file = PROJECT_ROOT / "proxy.txt"
    if not proxy_file.exists():
        return []
    result = []
    with open(proxy_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                host, port = parts[0], parts[1]
                user = parts[2] if len(parts) > 3 else ""
                password = parts[3] if len(parts) > 3 else ""
                if user and password:
                    proxy_url = f"http://{user}:{password}@{host}:{port}"
                else:
                    proxy_url = f"http://{host}:{port}"
                result.append({"http": proxy_url, "https": proxy_url})
    return result


def check_smart_account_exists(eoa_address: str) -> bool:
    """Проверяет через API profile/mapping, есть ли смарт-аккаунт. Использует случайный прокси из proxy.txt."""
    proxies_list = load_proxies()
    proxies = random.choice(proxies_list) if proxies_list else None
    url = f"{PROFILE_MAPPING_URL}?eoaAddress={eoa_address}"
    headers = {
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    try:
        r = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        if r.ok:
            data = r.json()
            return "smartAccounts" in data
        return False
    except Exception as e:
        logger.warning("Проверка profile/mapping не удалась: {}", e)
        return False


def _format_next_gm_at(dt: datetime) -> str:
    """Человекочитаемый вывод времени следующего GM (UTC)."""
    return dt.strftime("%d.%m.%Y %H:%M UTC")


async def _get_next_gm_text_from_page(page) -> Optional[str]:
    """
    Читает текст «Next GM available in X h Y m» из основного контента (не из модалки),
    чтобы получить данные именно текущего аккаунта.
    """
    locs = page.locator(NEXT_GM_TEXT_SELECTOR).filter(has_text="Next GM available in")
    n = await locs.count()
    for i in range(n):
        el = locs.nth(i)
        in_dialog = await el.evaluate("""el => !!el.closest('[role="dialog"]')""")
        if not in_dialog:
            return await el.text_content()
    if n > 0:
        return await locs.first.text_content()
    return None


async def _get_next_gm_text_from_modal(page) -> Optional[str]:
    """
    Читает «Next GM available in X h Y m» из модального окна "GM sent!".
    Пробует несколько селекторов: точный p, любой p с текстом, любой элемент с текстом.
    """
    dialog = page.locator('[role="dialog"]').filter(has_text="GM sent!")
    for selector in [
        "p.text-sm.text-zinc-900",
        "p",
    ]:
        try:
            el = dialog.locator(selector).filter(has_text="Next GM available in")
            await el.first.wait_for(state="visible", timeout=3000)
            text = await el.first.text_content()
            if text and "Next GM available in" in text:
                return text
        except Exception:
            continue
    try:
        el = dialog.get_by_text("Next GM available in")
        await el.first.wait_for(state="visible", timeout=3000)
        return await el.first.text_content()
    except Exception:
        return None


def parse_next_gm_available(text: str) -> Optional[datetime]:
    """
    Парсит строку вида "Next GM available in 8 h 30 m" или "1 d 2 h 15 m".
    Возвращает момент доступности следующего GM (UTC) = текущее время + распарсенная длительность.
    """
    if not text or "Next GM available in" not in text:
        return None
    part = text.split("Next GM available in", 1)[-1].strip()
    d = h = m = 0
    for match in re.finditer(r"(\d+)\s*([dhm])", part, re.I):
        val = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "d":
            d = val
        elif unit == "h":
            h = val
        elif unit == "m":
            m = val
    if d == 0 and h == 0 and m == 0:
        return None
    now_utc = datetime.now(timezone.utc)
    return now_utc + timedelta(days=d, hours=h, minutes=m)


def _get_cdp_endpoint(browser_info: dict) -> Optional[str]:
    """Извлекает CDP (Puppeteer) endpoint из ответа AdsPower."""
    ws_data = browser_info.get("ws")
    if isinstance(ws_data, dict):
        cdp = ws_data.get("puppeteer")
        if cdp:
            return cdp
    cdp = (
        browser_info.get("ws_endpoint")
        or browser_info.get("ws_endpoint_driver")
        or browser_info.get("puppeteer")
        or browser_info.get("debugger_address")
    )
    if isinstance(cdp, dict):
        cdp = cdp.get("puppeteer") or cdp.get("ws")
    if isinstance(cdp, str) and cdp.startswith("ws://"):
        return cdp
    for _, value in browser_info.items():
        if isinstance(value, str) and value.startswith("ws://"):
            return value
        if isinstance(value, dict):
            cdp = value.get("puppeteer") or value.get("ws")
            if cdp:
                return cdp
    return None


class StartaleGMBrowser:
    """Создание профиля AdsPower, запуск браузера, импорт кошелька, открытие Portal."""

    def __init__(
        self,
        api_key: str,
        api_port: int = 50325,
        base_url: Optional[str] = None,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url or f"http://local.adspower.net:{api_port}"
        self.timeout = timeout
        self.profile_id: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update(
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        )

    def _make_request(
        self, method: str, endpoint: str, data: Optional[dict] = None
    ) -> dict:
        url = f"{self.base_url}{endpoint}"
        params = {"api_key": self.api_key}
        if method.upper() == "GET":
            r = self.session.get(url, params=params, timeout=self.timeout)
        elif method.upper() == "POST":
            r = self.session.post(url, params=params, json=data, timeout=self.timeout)
        else:
            raise ValueError(f"Метод {method} не поддерживается")
        r.raise_for_status()
        result = r.json()
        if result.get("code") != 0:
            raise ValueError(result.get("msg", "Ошибка API AdsPower"))
        return result

    def create_temp_profile(self, use_proxy: bool = True) -> str:
        """Создаёт временный профиль браузера (по умолчанию со случайным прокси из AdsPower)."""
        name = f"startalegm_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        profile_data = {
            "name": name,
            "group_id": "0",
            "fingerprint_config": {
                "automatic_timezone": "1",
                "language": ["en-US", "en"],
                "webrtc": "disabled",
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        }
        if use_proxy:
            profile_data["proxyid"] = "random"
            logger.info("Профиль создаётся со случайным прокси из AdsPower")
        else:
            profile_data["user_proxy_config"] = {"proxy_soft": "no_proxy"}
        result = self._make_request("POST", "/api/v2/browser-profile/create", profile_data)
        self.profile_id = result.get("data", {}).get("profile_id")
        if not self.profile_id:
            raise ValueError("API не вернул profile_id")
        logger.info(f"Профиль создан: {self.profile_id}")
        return self.profile_id

    def start_browser(self, profile_id: Optional[str] = None) -> dict:
        """Запускает браузер по profile_id."""
        pid = profile_id or self.profile_id
        if not pid:
            raise ValueError("Не указан profile_id")
        result = self._make_request("POST", "/api/v2/browser-profile/start", {"profile_id": pid})
        data = result.get("data", {})
        if not data:
            raise ValueError("API не вернул данные браузера")
        logger.info("Браузер запущен")
        return data

    def stop_browser(self, profile_id: Optional[str] = None) -> None:
        """Останавливает браузер."""
        pid = profile_id or self.profile_id
        if not pid:
            return
        try:
            self._make_request("POST", "/api/v2/browser-profile/stop", {"profile_id": pid})
            logger.info("Браузер остановлен")
        except Exception as e:
            logger.warning(f"Остановка браузера: {e}")

    def delete_profile(self, profile_id: Optional[str] = None) -> None:
        """Удаляет профиль (пробуем profile_id и Profile_id для совместимости с разными версиями API)."""
        pid = profile_id or self.profile_id
        if not pid:
            return
        for key in ("profile_id", "Profile_id"):
            try:
                self._make_request("POST", "/api/v2/browser-profile/delete", {key: [pid]})
                logger.info("Профиль удалён")
                if self.profile_id == pid:
                    self.profile_id = None
                return
            except Exception as e:
                logger.debug(f"Удаление с {key}: {e}")
        logger.warning("Не удалось удалить профиль")

    async def _import_wallet(
        self, cdp_endpoint: str, private_key: str, password: str = "Password123"
    ) -> None:
        """Импортирует кошелёк в Rabby по CDP."""
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("Нет контекстов в браузере")
            context = browser.contexts[0]
            setup_url = f"chrome-extension://{RABBY_EXTENSION_ID}/index.html#/new-user/guide"
            page = None
            for p in context.pages:
                if RABBY_EXTENSION_ID in p.url or ("chrome-extension://" in p.url and "rabby" in p.url.lower()):
                    page = p
                    if "#/new-user/guide" not in p.url:
                        await page.goto(setup_url)
                        await asyncio.sleep(2)
                    break
            if not page:
                page = await context.new_page()
                await page.goto(setup_url)
                await asyncio.sleep(3)

            await page.wait_for_selector('span:has-text("I already have an address")', timeout=30000)
            await page.click('span:has-text("I already have an address")')
            await page.wait_for_selector('div.rabby-ItemWrapper-rabby--mylnj7:has-text("Private Key")', timeout=30000)
            await page.click('div.rabby-ItemWrapper-rabby--mylnj7:has-text("Private Key")')
            await page.wait_for_selector("#privateKey", timeout=30000)
            await page.fill("#privateKey", private_key)
            await page.wait_for_selector('button:has-text("Confirm"):not([disabled])', timeout=30000)
            await page.click('button:has-text("Confirm"):not([disabled])')
            await page.wait_for_selector("#password", timeout=30000)
            await page.fill("#password", password)
            await page.press("#password", "Tab")
            await page.keyboard.type(password)
            await page.wait_for_selector('button:has-text("Confirm"):not([disabled])', timeout=30000)
            await page.click('button:has-text("Confirm"):not([disabled])')
            await page.wait_for_selector("text=Imported Successfully", timeout=30000)
            logger.success("Кошелёк импортирован в Rabby")
            await page.close()
            logger.info("Вкладка импорта кошелька закрыта")
        finally:
            await playwright.stop()

    async def _open_portal(self, cdp_endpoint: str, eoa_address: str) -> None:
        """Открывает https://portal.soneium.org/ в браузере. eoa_address — адрес кошелька для проверки API profile/mapping."""
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("Нет контекстов в браузере")
            context = browser.contexts[0]
            page = None
            for p in context.pages:
                if not p.url.startswith("chrome-extension://"):
                    page = p
                    break
            if not page:
                page = await context.new_page()
            await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            logger.success(f"Открыта страница: {PORTAL_URL}")

            # Ждём модальное окно (role="dialog"), затем кнопку "Continue with Startale App" внутри него
            modal = page.locator('[role="dialog"]')
            await modal.wait_for(state="visible", timeout=30000)
            continue_btn = modal.get_by_role("button", name="Continue with Startale App")
            await continue_btn.wait_for(state="visible", timeout=10000)
            # Клик открывает новое всплывающее окно (Startale App) — ждём его и переключаемся на него
            async with context.expect_page() as popup_info:
                await continue_btn.click()
            popup_page = await popup_info.value
            await popup_page.wait_for_load_state("domcontentloaded", timeout=30000)
            logger.success('В модальном окне нажата кнопка "Continue with Startale App", открыто окно Startale App')

            # Во всплывающем окне ждём и нажимаем "Connect a wallet"
            connect_btn = popup_page.get_by_role("button", name="Connect a wallet")
            await connect_btn.wait_for(state="visible", timeout=30000)
            await connect_btn.click()
            logger.success('Во всплывающем окне нажата кнопка "Connect a wallet"')
            await asyncio.sleep(2)

            # В модальном окне "Log in or sign up" выбираем Rabby (список кошельков)
            rabby_btn = popup_page.get_by_role("button", name="Rabby")
            await rabby_btn.wait_for(state="visible", timeout=30000)
            # Клик по Rabby открывает popup окно расширения кошелька — ждём его
            async with context.expect_page() as wallet_popup_info:
                await rabby_btn.click()
            wallet_popup = await wallet_popup_info.value
            await wallet_popup.wait_for_load_state("domcontentloaded", timeout=15000)
            logger.success('Открыто popup окно кошелька Rabby')

            # В popup кошелька: Connect (после клика этот popup закрывается)
            connect_btn_wallet = wallet_popup.get_by_role("button", name="Connect")
            await connect_btn_wallet.wait_for(state="visible", timeout=30000)
            await connect_btn_wallet.click()
            logger.success('Нажата кнопка Connect в popup кошелька')

            # После Connect открывается новый popup с Sign и Confirm — ждём его
            sign_popup = await context.wait_for_event("page", timeout=30000)
            await sign_popup.wait_for_load_state("domcontentloaded", timeout=15000)
            logger.success('Открыт новый popup кошелька (Sign/Confirm)')

            sign_btn = sign_popup.get_by_role("button", name="Sign")
            await sign_btn.wait_for(state="visible", timeout=30000)
            await sign_btn.click()
            logger.success('Нажата кнопка Sign в popup кошелька')
            await asyncio.sleep(1)

            confirm_btn = sign_popup.get_by_role("button", name="Confirm")
            await confirm_btn.wait_for(state="visible", timeout=30000)
            await confirm_btn.click()
            logger.success('Нажата кнопка Confirm в popup кошелька')
            await asyncio.sleep(1)

            # В окне Startale App (где выбирали Rabby) появляется экран "wants to connect" — ждём Approve и кликаем
            approve_btn = popup_page.get_by_role("button", name="Approve")
            await approve_btn.wait_for(state="visible", timeout=30000)
            await approve_btn.click()
            logger.success('Нажата кнопка Approve в окне Startale App')
            await asyncio.sleep(1)

            # На основной вкладке портала снова ждём "Continue with Startale App", кликаем; если появился popup с Approve — кликаем; иначе повторяем (сайт может глючить)
            approved_second_popup = False
            max_continue_retries = 5
            for attempt in range(max_continue_retries):
                await page.bring_to_front()
                modal_again = page.locator('[role="dialog"]')
                await modal_again.wait_for(state="visible", timeout=30000)
                continue_btn_again = modal_again.get_by_role("button", name="Continue with Startale App")
                await continue_btn_again.wait_for(state="visible", timeout=10000)
                try:
                    async with context.expect_page(timeout=10000) as popup_info:
                        await continue_btn_again.click()
                    logger.success('На основной вкладке портала нажата кнопка "Continue with Startale App"')
                    popup_after_continue = await popup_info.value
                    await popup_after_continue.wait_for_load_state("domcontentloaded", timeout=15000)
                    approve_btn_2 = popup_after_continue.get_by_role("button", name="Approve")
                    await approve_btn_2.wait_for(state="visible", timeout=15000)
                    await approve_btn_2.click()
                    logger.success('В новом popup нажата кнопка Approve')
                    approved_second_popup = True
                    break
                except Exception:
                    logger.info("Новый popup с кнопкой Approve не появился, повторяем клик Continue with Startale App ({}/{})", attempt + 1, max_continue_retries)
                    await asyncio.sleep(2)
            else:
                logger.warning("После {} попыток popup с Approve так и не появился", max_continue_retries)

            # Если был Approve — проверяем API: если в ответе есть поле smartAccounts (даже пустой массив), не делаем Try gasless
            if approved_second_popup:
                await page.bring_to_front()
                mapping_url = f"{PROFILE_MAPPING_URL}?eoaAddress={eoa_address}"
                need_gasless = True
                try:
                    response = await page.request.get(mapping_url)
                    if response.ok:
                        data = await response.json()
                        if "smartAccounts" in data:
                            need_gasless = False
                except Exception as e:
                    logger.warning("Проверка profile/mapping не удалась: {}, выполняем Try gasless", e)
                if need_gasless:
                    logger.info("В ответе API нет smartAccounts, выполняем Try gasless action и Send")
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    welcome_modal = page.locator('[role="dialog"][aria-labelledby="welcome-back-modal-title"]')
                    await welcome_modal.wait_for(state="visible", timeout=30000)
                    try_gasless_btn = page.get_by_role("button", name="Try gasless action")
                    await try_gasless_btn.wait_for(state="visible", timeout=10000)
                    async with context.expect_page(timeout=15000) as tx_popup_info:
                        await try_gasless_btn.click()
                    logger.success('Нажата кнопка "Try gasless action" в модальном окне Welcome back')
                    tx_popup = await tx_popup_info.value
                    await tx_popup.wait_for_load_state("domcontentloaded", timeout=15000)
                    logger.info("Открыт popup подтверждения транзакции, ждём активную кнопку Send")
                    send_btn = tx_popup.get_by_role("button", name="Send")
                    await send_btn.wait_for(state="visible", timeout=30000)
                    await send_btn.click(timeout=60000)
                    logger.success('Нажата кнопка Send в popup подтверждения транзакции')
                    await page.goto(STARTALE_APP_URL, wait_until="domcontentloaded", timeout=60000)
                    logger.success("Открыта страница {}", STARTALE_APP_URL)
                else:
                    logger.info("В ответе API есть smartAccounts, пропускаем Try gasless action")
            await asyncio.sleep(1)

            # Если не на app.startale.com (например, пропустили Try gasless), переходим туда и выполняем GM
            if "app.startale.com" not in page.url:
                await page.goto(STARTALE_APP_URL, wait_until="domcontentloaded", timeout=60000)
                logger.success("Открыта страница {}", STARTALE_APP_URL)
            # На app.startale.com: ждём загрузки данных аккаунта, затем проверяем "Next GM available in"
            if "app.startale.com" in page.url:
                await asyncio.sleep(WAIT_FOR_GM_DATA_SEC)
                next_gm_visible = False
                try:
                    text = await _get_next_gm_text_from_page(page)
                    if text and "Next GM available in" in text:
                        next_gm_visible = True
                        next_at = parse_next_gm_available(text)
                        if next_at:
                            db.upsert_account(eoa_address, next_gm_available_at=next_at)
                            logger.success("Следующий GM доступен: {}", _format_next_gm_at(next_at))
                except Exception:
                    pass
                if not next_gm_visible:
                    try:
                        send_gm_btn = page.get_by_role("button", name="Send GM back")
                        await send_gm_btn.wait_for(state="visible", timeout=15000)
                        await send_gm_btn.click(timeout=10000)
                        logger.success('Нажата кнопка "Send GM back"')
                        await page.locator("h2:has-text('GM sent!')").wait_for(state="visible", timeout=30000)
                        logger.success('Появилось модальное окно "GM sent!"')
                        try:
                            text = await _get_next_gm_text_from_modal(page)
                            next_at = parse_next_gm_available(text or "") if text else None
                            if next_at:
                                db.upsert_account(eoa_address, next_gm_available_at=next_at)
                                logger.success("Следующий GM доступен: {}", _format_next_gm_at(next_at))
                            else:
                                fallback_at = datetime.now(timezone.utc) + timedelta(minutes=FALLBACK_GM_COOLDOWN_MINUTES)
                                db.upsert_account(eoa_address, next_gm_available_at=fallback_at)
                                logger.warning("Время из модалки не распознано, записан fallback: {}", _format_next_gm_at(fallback_at))
                        except Exception:
                            fallback_at = datetime.now(timezone.utc) + timedelta(minutes=FALLBACK_GM_COOLDOWN_MINUTES)
                            db.upsert_account(eoa_address, next_gm_available_at=fallback_at)
                            logger.warning("Не удалось прочитать время из модалки, записан fallback: {}", _format_next_gm_at(fallback_at))
                    except Exception:
                        logger.debug("Кнопка Send GM back не найдена или модалка не появилась")
            await asyncio.sleep(1)
        finally:
            await playwright.stop()

    async def _open_portal_login(self, cdp_endpoint: str, eoa_address: str) -> None:
        """Открывает https://app.startale.com/log-in и подключает кошелёк (Connect a wallet → Rabby → Connect → Sign → Confirm)."""
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("Нет контекстов в браузере")
            context = browser.contexts[0]
            page = None
            for p in context.pages:
                if not p.url.startswith("chrome-extension://"):
                    page = p
                    break
            if not page:
                page = await context.new_page()
            await page.goto(STARTALE_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            logger.success(f"Открыта страница: {STARTALE_LOGIN_URL}")

            connect_btn = page.get_by_role("button", name="Connect a wallet")
            await connect_btn.wait_for(state="visible", timeout=30000)
            await connect_btn.click()
            logger.success('Нажата кнопка "Connect a wallet"')
            await asyncio.sleep(2)

            rabby_btn = page.get_by_role("button", name="Rabby")
            await rabby_btn.wait_for(state="visible", timeout=30000)
            async with context.expect_page() as wallet_popup_info:
                await rabby_btn.click()
            wallet_popup = await wallet_popup_info.value
            await wallet_popup.wait_for_load_state("domcontentloaded", timeout=15000)
            logger.success("Открыто popup окно кошелька Rabby")

            connect_btn_wallet = wallet_popup.get_by_role("button", name="Connect")
            await connect_btn_wallet.wait_for(state="visible", timeout=30000)
            await connect_btn_wallet.click()
            logger.success("Нажата кнопка Connect в popup кошелька")

            sign_popup = await context.wait_for_event("page", timeout=30000)
            await sign_popup.wait_for_load_state("domcontentloaded", timeout=15000)
            logger.success("Открыт popup кошелька (Sign/Confirm)")

            sign_btn = sign_popup.get_by_role("button", name="Sign")
            await sign_btn.wait_for(state="visible", timeout=30000)
            await sign_btn.click()
            logger.success("Нажата кнопка Sign в popup кошелька")
            await asyncio.sleep(1)

            confirm_btn = sign_popup.get_by_role("button", name="Confirm")
            await confirm_btn.wait_for(state="visible", timeout=30000)
            await confirm_btn.click()
            logger.success("Нажата кнопка Confirm в popup кошелька")
            await asyncio.sleep(1)

            approve_btn = page.get_by_role("button", name="Approve")
            try:
                await approve_btn.wait_for(state="visible", timeout=10000)
                await approve_btn.click()
                logger.success("Нажата кнопка Approve на странице log-in")
            except Exception:
                pass
            await asyncio.sleep(1)

            await page.goto(STARTALE_APP_URL, wait_until="domcontentloaded", timeout=60000)
            logger.success("Открыта страница {}", STARTALE_APP_URL)
            await asyncio.sleep(WAIT_FOR_GM_DATA_SEC)
            next_gm_visible = False
            try:
                text = await _get_next_gm_text_from_page(page)
                if text and "Next GM available in" in text:
                    next_gm_visible = True
                    next_at = parse_next_gm_available(text)
                    if next_at:
                        db.upsert_account(eoa_address, next_gm_available_at=next_at)
                        logger.success("Следующий GM доступен: {}", _format_next_gm_at(next_at))
            except Exception:
                pass
            if not next_gm_visible:
                try:
                    send_gm_btn = page.get_by_role("button", name="Send GM back")
                    await send_gm_btn.wait_for(state="visible", timeout=15000)
                    await send_gm_btn.click(timeout=10000)
                    logger.success('Нажата кнопка "Send GM back"')
                    await page.locator("h2:has-text('GM sent!')").wait_for(state="visible", timeout=30000)
                    logger.success('Появилось модальное окно "GM sent!"')
                    try:
                        text = await _get_next_gm_text_from_modal(page)
                        next_at = parse_next_gm_available(text or "") if text else None
                        if next_at:
                            db.upsert_account(eoa_address, next_gm_available_at=next_at)
                            logger.success("Следующий GM доступен: {}", _format_next_gm_at(next_at))
                        else:
                            fallback_at = datetime.now(timezone.utc) + timedelta(minutes=FALLBACK_GM_COOLDOWN_MINUTES)
                            db.upsert_account(eoa_address, next_gm_available_at=fallback_at)
                            logger.warning("Время из модалки не распознано, записан fallback: {}", _format_next_gm_at(fallback_at))
                    except Exception:
                        fallback_at = datetime.now(timezone.utc) + timedelta(minutes=FALLBACK_GM_COOLDOWN_MINUTES)
                        db.upsert_account(eoa_address, next_gm_available_at=fallback_at)
                        logger.warning("Не удалось прочитать время из модалки, записан fallback: {}", _format_next_gm_at(fallback_at))
                except Exception:
                    logger.debug("Кнопка Send GM back не найдена или модалка не появилась")
            await asyncio.sleep(1)
        finally:
            await playwright.stop()

    def run_one(
        self,
        key_index: int = 0,
        wallet_password: str = "Password123",
        use_proxy: bool = True,
        wait_for_user: bool = True,
    ) -> bool:
        """Один цикл: профиль → браузер → импорт кошелька → открытие Portal. При wait_for_user=False не ждёт Enter."""
        try:
            private_key = load_private_key(key_index=key_index)
            address = Web3.to_checksum_address(Web3().eth.account.from_key(private_key).address)
            logger.info(f"Кошелёк: {address}")

            self.create_temp_profile(use_proxy=use_proxy)
            browser_info = self.start_browser(self.profile_id)
            time.sleep(5)

            cdp = _get_cdp_endpoint(browser_info)
            if not cdp:
                raise RuntimeError("Не удалось получить CDP endpoint от AdsPower")

            asyncio.run(
                self._import_wallet(cdp, private_key, password=wallet_password)
            )
            has_smart = check_smart_account_exists(address)
            db.upsert_account(address, smart_account_created=has_smart)
            if has_smart:
                logger.info("Смарт-аккаунт уже создан, переходим на log-in и подключаемся")
                asyncio.run(self._open_portal_login(cdp, address))
            else:
                logger.info("Смарт-аккаунт не создан, выполняем полный flow через портал")
                asyncio.run(self._open_portal(cdp, address))
                db.upsert_account(address, smart_account_created=True)

            if wait_for_user:
                logger.info("Готово. Закройте браузер вручную или нажмите Enter для остановки профиля.")
                input()
            return True
        except KeyboardInterrupt:
            logger.warning("Прервано пользователем (Ctrl+C)")
            raise  # пробрасываем, чтобы мониторинг завершился
        finally:
            if self.profile_id:
                self.stop_browser(self.profile_id)
                self.delete_profile(self.profile_id)


MONITOR_INTERVAL_SEC = 10
SPINNER_CHARS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
SPINNER_INTERVAL = 0.12


def _wait_with_spinner(seconds: float, message: str = "Ожидание следующей проверки") -> None:
    """Ждёт указанное время, показывая спиннер в консоли. Прерывается по Ctrl+C."""
    import sys
    end = time.time() + seconds
    i = 0
    try:
        while time.time() < end:
            left = max(0, int(end - time.time()))
            char = SPINNER_CHARS[i % len(SPINNER_CHARS)]
            sys.stderr.write(f"\r  {char} {message}... ({left} с)   ")
            sys.stderr.flush()
            time.sleep(min(SPINNER_INTERVAL, end - time.time()))
            i += 1
    except KeyboardInterrupt:
        raise
    finally:
        sys.stderr.write("\r" + " " * (len(message) + 30) + "\r")
        sys.stderr.flush()


def run() -> None:
    """Точка входа: запуск мониторинга по БД (GM по расписанию для всех аккаунтов из keys.txt)."""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
    )
    try:
        api_key = load_adspower_api_key()
        all_keys = load_all_keys()
        logger.info(f"Загружено ключей: {len(all_keys)}")
        if not all_keys:
            logger.error("Нет ключей в keys.txt")
            return
        db.init_db()
        manager = StartaleGMBrowser(api_key=api_key)
        run_monitor(manager, all_keys)
    except FileNotFoundError as e:
        logger.error(str(e))
        raise SystemExit(1)
    except ValueError as e:
        logger.error(str(e))
        raise SystemExit(1)


def run_monitor(manager: StartaleGMBrowser, all_keys: list[str]) -> None:
    """Постоянно проверяет БД: для аккаунтов, у которых наступило время GM, запускает run_one."""
    known_addresses = []
    for i in range(len(all_keys)):
        try:
            addr = get_address_for_key_index(i)
            known_addresses.append(addr)
        except Exception:
            pass
    if not known_addresses:
        logger.error("Не удалось получить адреса из ключей")
        return
    logger.info("Мониторинг запущен (интервал {} с). Остановка: Ctrl+C.", MONITOR_INTERVAL_SEC)
    while True:
        current_addr = None  # чтобы в except знать, какой аккаунт отложить при лимите AdsPower
        try:
            due = db.get_accounts_due_for_gm(known_addresses)
            if due:
                addr = due[0]
                current_addr = addr
                key_index = get_key_index_for_address(addr, all_keys)
                if key_index is not None:
                    logger.info("Запуск аккаунта для GM: {} (ключ #{})", addr, key_index + 1)
                    manager.run_one(key_index=key_index, wait_for_user=False)
                else:
                    logger.warning("Адрес {} не найден среди ключей", addr)
            else:
                _wait_with_spinner(MONITOR_INTERVAL_SEC)
        except KeyboardInterrupt:
            logger.warning("Мониторинг остановлен")
            break
        except Exception as e:
            err_msg = str(e)
            if current_addr and ("Exceeding import daily limit" in err_msg or "recovery after" in err_msg.lower()):
                fallback_at = datetime.now(timezone.utc) + timedelta(hours=10)
                db.upsert_account(current_addr, next_gm_available_at=fallback_at)
                logger.warning("Лимит AdsPower (создание профилей). Аккаунт {} отложен на 10 ч.", current_addr)
            else:
                logger.error("Ошибка мониторинга: {}", err_msg)
            time.sleep(MONITOR_INTERVAL_SEC)
