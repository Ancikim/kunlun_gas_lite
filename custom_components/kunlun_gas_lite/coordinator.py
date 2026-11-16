"""昆仑燃气数据协调器：免密接口为主，令牌接口为可选增强。"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BASE_URL,
    CONF_ACCOUNT_ID,
    CONF_BADGE,
    CONF_MDM_CODE,
    CONF_OU_ID,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_CODE,
    DAY_WINDOW,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PATH_BALANCE,
    PATH_BILLS,
    PATH_DAYS,
    PATH_DEFAULT_USER_CODE,
    PATH_LADDER,
    PATH_MONTHS,
    build_headers,
)

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=25)


class KunlunGasAuthError(Exception):
    """令牌失效（只影响进阶数据，不影响免密核心）。"""


class KunlunGasClient:
    def __init__(self, hass: HomeAssistant, config: dict):
        self._session = async_get_clientsession(hass)
        self.user_code = str(config.get(CONF_USER_CODE) or "")
        self.mdm_code = str(config.get(CONF_MDM_CODE) or "")
        self.account_id = str(config.get(CONF_ACCOUNT_ID) or "")
        self.badge = str(config.get(CONF_BADGE) or "")
        self.ou_id = int(config.get(CONF_OU_ID) or 0)
        self.token = str(config.get(CONF_TOKEN) or "").strip()

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    async def _request(self, method, path, params=None, body=None, auth=True):
        url = BASE_URL + path
        headers = build_headers(self.token if auth else "", self.mdm_code)
        try:
            async with self._session.request(
                method, url, params=params, json=body,
                headers=headers, timeout=_TIMEOUT,
            ) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    raise KunlunGasAuthError("HTTP %s: %s" % (resp.status, text[:120]))
                try:
                    payload = await resp.json(content_type=None)
                except Exception:
                    raise UpdateFailed("响应非 JSON: %s" % text[:160])
        except KunlunGasAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed("请求失败 %s: %s" % (path, err)) from err

        if not isinstance(payload, dict):
            raise UpdateFailed("%s 返回非对象响应" % path)
        code = payload.get("code")
        if code in (401, 403) or "请重新登录" in str(payload.get("msg", "")):
            raise KunlunGasAuthError(str(payload.get("msg") or payload.get("message")))
        if code != 200:
            raise UpdateFailed("%s 返回 code=%s msg=%s" % (path, code, payload.get("msg")))
        return payload.get("data")

    async def async_validate(self) -> dict:
        """配置流校验：只调免密接口，确认户号与地区码有效。"""
        data = await self._request(
            "GET", PATH_BALANCE,
            params={"userCode": self.user_code, "mdmCode": self.mdm_code},
            auth=False,
        )
        if not isinstance(data, dict) or str(data.get("userCode")) != self.user_code:
            raise UpdateFailed("户号或地区码不正确，接口未返回对应户号")
        return data

    async def async_fetch(self) -> dict:
        # 1) 核心：免密余额接口，永远可用
        balance = await self._request(
            "GET", PATH_BALANCE,
            params={"userCode": self.user_code, "mdmCode": self.mdm_code},
            auth=False,
        )
        if not isinstance(balance, dict):
            raise UpdateFailed("余额接口未返回数据")

        # 用免密响应拼出用户信息（含脱敏的户名/地址/手机号）
        user = {
            "customerCode": str(balance.get("userCode") or self.user_code),
            "name": balance.get("customerName"),
            "address": balance.get("address"),
            "phone": balance.get("phone"),
            "accountId": str(balance.get("accountId") or self.account_id),
            "badgeMnb": self.badge,
            "meterType_dictText": balance.get("meterTypeDesc"),
            "mdmCode": str(balance.get("mdmCode") or self.mdm_code),
        }

        result = {
            "mode": "open",
            "user": user,
            "balance": balance,
            "days": [],
            "months": [],
            "ladder": {},
            "bills": {},
        }
        if not self.has_token:
            return result

        # 2) 进阶：需要令牌。任一环节失效即降级，不影响核心传感器
        try:
            detail = None
            if self.ou_id:
                detail = await self._request(
                    "GET", PATH_DEFAULT_USER_CODE, params={"mdmOuId": self.ou_id}
                )
            if isinstance(detail, dict) and detail.get("customerCode"):
                user.update({
                    "customerCode": str(detail["customerCode"]),
                    "name": detail.get("name") or user.get("name"),
                    "address": detail.get("address") or user.get("address"),
                    "phone": detail.get("phone") or user.get("phone"),
                    "accountId": str(detail.get("accountId") or user.get("accountId")),
                    "badgeMnb": str(detail.get("badgeMnb") or self.badge),
                    "meterType_dictText": detail.get("meterType_dictText") or user.get("meterType_dictText"),
                    "mdmCode": str(detail.get("mdmCode") or self.mdm_code),
                })
                self.mdm_code = user["mdmCode"]

            now = dt_util.now()
            begin_date = (now - timedelta(days=DAY_WINDOW - 1)).strftime("%Y-%m-%d")
            end_date = now.strftime("%Y-%m-%d")
            start_month = "%04d-01" % now.year
            end_month = now.strftime("%Y-%m")
            code = user["customerCode"]
            badge = user["badgeMnb"]
            account = user["accountId"]

            async def _safe(method, path, params=None, body=None):
                try:
                    return await self._request(method, path, params=params, body=body)
                except (KunlunGasAuthError, UpdateFailed) as err:
                    _LOGGER.debug("昆仑燃气进阶接口跳过 %s: %s", path, err)
                    return None

            days = await _safe("POST", PATH_DAYS, body={
                "mdmCode": self.mdm_code, "pageNumber": 1, "pageSize": 99999,
                "badgeNumber": badge, "customerCode": code,
                "beginDate": begin_date, "endDate": end_date,
            })
            months = await _safe("POST", PATH_MONTHS, body={
                "mdmCode": self.mdm_code, "pageNumber": 1, "pageSize": 99999,
                "badgeNumber": badge, "customerCode": code,
                "startMonth": start_month, "endMonth": end_month,
            })
            ladder = await _safe("GET", PATH_LADDER, params={"accountId": account})
            bills = await _safe("POST", PATH_BILLS, body={
                "accountId": account, "mdmCode": self.mdm_code,
                "pageNumber": 1, "pageSize": 10,
            })

            result.update({
                "mode": "full",
                "user": user,
                "days": days if isinstance(days, list) else [],
                "months": months if isinstance(months, list) else [],
                "ladder": ladder if isinstance(ladder, dict) else {},
                "bills": bills if isinstance(bills, dict) else {},
            })
        except KunlunGasAuthError as err:
            _LOGGER.warning("昆仑燃气令牌失效，已自动降级为免密模式：%s", err)
            result["token_expired"] = True
        return result


class KunlunGasCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=max(int(interval), 600)),
        )
        self.entry = entry
        cfg = dict(entry.data)
        # options 显式写入 token 键即以其为准（空串 = 用户主动清除，回到免密）；
        # 旧 entry 的 options 里没有 token 键时，仍沿用 data 中的令牌。
        for key, value in (entry.options or {}).items():
            cfg[key] = value
        self.client = KunlunGasClient(hass, cfg)

    @property
    def has_token(self) -> bool:
        return self.client.has_token

    async def _async_update_data(self):
        return await self.client.async_fetch()
