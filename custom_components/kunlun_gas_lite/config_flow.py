"""配置流：两步。第 1 步选地区（下拉，自动带出地区码/ouId），第 2 步填户号等。

默认免密（只需户号）；令牌可选，且可在"设置"里随时更换/清除。
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ACCOUNT_ID,
    CONF_BADGE,
    CONF_CLEAR_TOKEN,
    CONF_MDM_CODE,
    CONF_OU_ID,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .coordinator import KunlunGasClient
from .regions import REGIONS

_LOGGER = logging.getLogger(__name__)


def _clean_token(raw: Any) -> str:
    """粘贴令牌时常见换行、空格、Bearer 前缀，统一清掉。"""
    token = "".join(str(raw or "").split())
    if token.lower().startswith("bearer"):
        token = token[6:]
    return token


def _scan_selector():
    return NumberSelector(NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL, max=86400, step=300, mode=NumberSelectorMode.BOX
    ))


def _region_selector():
    options = [SelectOptionDict(value=r["value"], label=r["label"]) for r in REGIONS]
    return SelectSelector(SelectSelectorConfig(
        options=options, mode=SelectSelectorMode.DROPDOWN,
    ))


def _parse_region(value: str) -> tuple[str, int]:
    """value='mdmCode|ouId' -> (mdm_code, ou_id)；非法值返回空。"""
    try:
        mdm, ou = str(value).split("|", 1)
        return mdm, int(ou)
    except (ValueError, TypeError):
        return "", 0


def _detail_schema(defaults: dict[str, Any]) -> vol.Schema:
    mdm = defaults.get(CONF_MDM_CODE, "")
    ou = defaults.get(CONF_OU_ID, 0)
    fields: dict[Any, Any] = {
        vol.Required(CONF_USER_CODE): str,
        vol.Optional(CONF_TOKEN, default=""): str,
        vol.Optional(CONF_ACCOUNT_ID, default=""): str,
        vol.Optional(CONF_BADGE, default=""): str,
        vol.Required(CONF_SCAN_INTERVAL,
                     default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): _scan_selector(),
    }
    fields[
        vol.Required(CONF_MDM_CODE, default=mdm) if mdm else vol.Required(CONF_MDM_CODE)
    ] = str
    fields[
        vol.Required(CONF_OU_ID, default=ou) if ou else vol.Required(CONF_OU_ID)
    ] = int
    return vol.Schema(fields)


def _title(code: str, has_token: bool) -> str:
    return "昆仑燃气 %s（%s）" % (code, "完整" if has_token else "免密")


class KunlunGasConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._region_mdm = ""
        self._region_ou = 0

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return KunlunGasOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """第 1 步：选地区。"""
        if user_input is not None:
            self._region_mdm, self._region_ou = _parse_region(user_input[CONF_REGION])
            return await self.async_step_detail()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_REGION): _region_selector(),
            }),
            description_placeholders={
                "count": str(len(REGIONS)),
            },
        )

    async def async_step_detail(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """第 2 步：地区码/ouId 已按上一步自动填好，填户号等。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            cfg = {
                CONF_USER_CODE: str(user_input[CONF_USER_CODE]).strip(),
                CONF_MDM_CODE: str(user_input[CONF_MDM_CODE]).strip(),
                CONF_TOKEN: _clean_token(user_input.get(CONF_TOKEN)),
                CONF_ACCOUNT_ID: str(user_input.get(CONF_ACCOUNT_ID) or "").strip(),
                CONF_BADGE: str(user_input.get(CONF_BADGE) or "").strip(),
                CONF_OU_ID: int(user_input[CONF_OU_ID]),
            }
            try:
                client = KunlunGasClient(self.hass, cfg)
                balance = await client.async_validate()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("昆仑燃气配置校验失败: %s", err)
                errors["base"] = "cannot_connect"
            else:
                code = cfg[CONF_USER_CODE]
                await self.async_set_unique_id(code)
                self._abort_if_unique_id_configured()
                _LOGGER.info(
                    "昆仑燃气校验通过：%s 余额 %s 元 / 表读数 %s m³（%s模式）",
                    cfg[CONF_MDM_CODE], balance.get("remoteMeterBalance"),
                    balance.get("readingLastTime"),
                    "完整" if client.has_token else "免密",
                )
                return self.async_create_entry(
                    title=_title(code, bool(cfg[CONF_TOKEN])),
                    data={**cfg, CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])},
                )

        return self.async_show_form(
            step_id="detail",
            data_schema=_detail_schema({
                CONF_MDM_CODE: self._region_mdm,
                CONF_OU_ID: self._region_ou,
            }),
            errors=errors,
            description_placeholders={
                "mdm": self._region_mdm,
                "ou": str(self._region_ou),
            },
        )


class KunlunGasOptionsFlow(OptionsFlow):
    """令牌是短命的（实测 ≤3 天），这里允许随时换新或清空回到免密。"""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        entry = self.config_entry
        opts = dict(entry.options or {})
        current_token = opts.get(CONF_TOKEN, entry.data.get(CONF_TOKEN, "")) or ""
        current_scan = int(opts.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ))

        if user_input is not None:
            if user_input.get(CONF_CLEAR_TOKEN):
                new_token = ""
            else:
                # 留空 = 保持原令牌不动，避免只改刷新间隔时把令牌抹掉
                new_token = _clean_token(user_input.get(CONF_TOKEN)) or current_token

            new_options = {
                CONF_TOKEN: new_token,
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
            }
            self.hass.config_entries.async_update_entry(
                entry, title=_title(entry.unique_id or entry.data.get(CONF_USER_CODE, ""),
                                    bool(new_token))
            )
            _LOGGER.info(
                "昆仑燃气设置已更新：%s，刷新间隔 %ss",
                "令牌已清除（免密模式）" if not new_token
                else ("令牌已更新" if new_token != current_token else "令牌未变"),
                new_options[CONF_SCAN_INTERVAL],
            )
            result = self.async_create_entry(title="", data=new_options)
            # 显式调度重载：仅靠 update listener 时，选项变更后传感器列表不会重建
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return result

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_TOKEN, default=""): str,
                vol.Optional(CONF_CLEAR_TOKEN, default=False): bool,
                vol.Required(CONF_SCAN_INTERVAL, default=current_scan): _scan_selector(),
            }),
            description_placeholders={
                "mode": "完整（已存令牌 %d 字符）" % len(current_token)
                        if current_token else "免密",
            },
        )
