"""昆仑燃气传感器。

免密字段来自 /retail/preservice/gasFeePay/getDebt4Wsyyt 真实响应
（2026-11-16 实测），进阶字段来自需令牌的 /hall/close/ 系列接口。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KunlunGasCoordinator


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_day(d):
    days = d.get("days") or []
    if not days:
        return {}
    return sorted(days, key=lambda x: str(x.get("gasYearMonth", "")), reverse=True)[0]


def _current_month(d):
    months = d.get("months") or []
    if not months:
        return {}
    return sorted(months, key=lambda x: str(x.get("gasYearMonth", "")), reverse=True)[0]


def _stage(d):
    ladder = d.get("ladder") or {}
    acc = _f(ladder.get("accVolume")) or 0.0
    stages = ladder.get("billCalculationLineDtoList") or []
    for s in stages:
        end = _f(s.get("endVolume"))
        if end is None or acc < end:
            return acc, s, stages
    return acc, (stages[-1] if stages else {}), stages


def _user_attrs(d):
    u = d.get("user") or {}
    b = d.get("balance") or {}
    return {
        "户号": u.get("customerCode"),
        "户名": u.get("name"),
        "地址": u.get("address"),
        "绑定手机号": u.get("phone"),
        "账户编号": u.get("accountId"),
        "表号": u.get("badgeMnb"),
        "表类型": u.get("meterType_dictText") or b.get("meterTypeDesc"),
        "地区代码": u.get("mdmCode"),
        "可缴费": b.get("payPermitted"),
        "数据模式": "完整（含令牌）" if d.get("mode") == "full" else "免密",
        "令牌已过期": bool(d.get("token_expired")),
    }


def _fee_attrs(d):
    b = d.get("balance") or {}
    return {k: b.get(k) for k in (
        "beginBalance", "endBalance", "gasVolume", "averagePrice", "gasFee",
        "overdue", "oweAmount", "respDesc", "payPermitted",
        "remoteMeterLastCommunicationTime", "isRemoteTransmit",
    ) if b.get(k) is not None}


def _day_attrs(d):
    return {"明细": [
        {"日期": x.get("gasYearMonth"), "用气量_m3": _f(x.get("gasVolume")),
         "表读数_m3": _f(x.get("gasFeeOrReading")), "上报时间": x.get("dayLastChargeTime")}
        for x in (d.get("days") or [])
    ]}


def _month_attrs(d):
    return {"明细": [
        {"月份": x.get("gasYearMonth"), "用气量_m3": _f(x.get("gasVolume")),
         "累计读数_m3": _f(x.get("gasFeeOrReading"))}
        for x in (d.get("months") or [])
    ]}


def _ladder_attrs(d):
    _, _, stages = _stage(d)
    return {"阶梯": [
        {"名称": s.get("rateItemName"), "起始_m3": _f(s.get("beginVolume")),
         "终止_m3": _f(s.get("endVolume")), "单价_CNY": _f(s.get("price"))}
        for s in stages
    ]}


@dataclass(frozen=True, kw_only=True)
class KunlunSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]
    attrs_fn: Callable[[dict], dict] | None = None
    requires_token: bool = False


def _v(key, default=None):
    return lambda d: (d.get("balance") or {}).get(key, default)


_MONEY = dict(native_unit_of_measurement="CNY", device_class=SensorDeviceClass.MONETARY,
              suggested_display_precision=2)


DESCRIPTIONS: tuple[KunlunSensorDescription, ...] = (
    # ---------- 免密模式（默认，永远可用） ----------
    KunlunSensorDescription(
        key="remote_meter_balance", name="表端余额", icon="mdi:cash", **_MONEY,
        value_fn=lambda d: _f(_v("remoteMeterBalance")(d)), attrs_fn=_user_attrs,
    ),
    KunlunSensorDescription(
        key="owe_amount", name="欠费金额", icon="mdi:alert-circle-outline", **_MONEY,
        value_fn=lambda d: _f(_v("oweAmount")(d)),
    ),
    KunlunSensorDescription(
        key="account_balance", name="账户余额", icon="mdi:wallet-outline", **_MONEY,
        value_fn=lambda d: _f(_v("endBalance")(d)),
    ),
    KunlunSensorDescription(
        key="meter_reading", name="燃气表读数", icon="mdi:meter-gas",
        native_unit_of_measurement="m³", state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda d: _f(_v("readingLastTime")(d)),
    ),
    KunlunSensorDescription(
        key="last_communication", name="表具最后通信", icon="mdi:clock-outline",
        value_fn=lambda d: _v("remoteMeterLastCommunicationTime")(d),
    ),
    KunlunSensorDescription(
        key="latest_gas_fee", name="最近账单气费", icon="mdi:receipt-text-outline", **_MONEY,
        value_fn=lambda d: _f(_v("gasFee")(d)), attrs_fn=_fee_attrs,
    ),
    KunlunSensorDescription(
        key="pay_permitted", name="是否可缴费", icon="mdi:check-decagram",
        value_fn=lambda d: _v("payPermitted")(d),
    ),

    # ---------- 进阶模式（需令牌） ----------
    KunlunSensorDescription(
        key="last_day_usage", name="最近一日用气", icon="mdi:fire", requires_token=True,
        native_unit_of_measurement="m³", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _f(_last_day(d).get("gasVolume")), attrs_fn=_day_attrs,
    ),
    KunlunSensorDescription(
        key="last_day_reading", name="最近一日表读数", icon="mdi:gauge", requires_token=True,
        native_unit_of_measurement="m³", suggested_display_precision=2,
        value_fn=lambda d: _f(_last_day(d).get("gasFeeOrReading")),
    ),
    KunlunSensorDescription(
        key="recent_31d_usage", name="近31日用气", icon="mdi:chart-line", requires_token=True,
        native_unit_of_measurement="m³", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: sum(_f(x.get("gasVolume")) or 0 for x in (d.get("days") or [])),
    ),
    KunlunSensorDescription(
        key="current_month_usage", name="本月用气", icon="mdi:calendar-month", requires_token=True,
        native_unit_of_measurement="m³", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _f(_current_month(d).get("gasVolume")), attrs_fn=_month_attrs,
    ),
    KunlunSensorDescription(
        key="yearly_usage", name="本年累计用气", icon="mdi:chart-bar", requires_token=True,
        native_unit_of_measurement="m³", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _f((d.get("ladder") or {}).get("accVolume")),
    ),
    KunlunSensorDescription(
        key="ladder_stage", name="当前阶梯档位", icon="mdi:stairs", requires_token=True,
        value_fn=lambda d: _stage(d)[1].get("billBillingSort"),
    ),
    KunlunSensorDescription(
        key="ladder_price", name="当前阶梯单价", icon="mdi:currency-cny", requires_token=True,
        native_unit_of_measurement="CNY/m³", suggested_display_precision=2,
        value_fn=lambda d: _f(_stage(d)[1].get("price")), attrs_fn=_ladder_attrs,
    ),
    KunlunSensorDescription(
        key="ladder_remaining", name="距下一阶梯", icon="mdi:arrow-up-bold", requires_token=True,
        native_unit_of_measurement="m³", suggested_display_precision=2,
        value_fn=lambda d: (
            (_f(_stage(d)[1].get("endVolume")) or 0) - _stage(d)[0]
            if _f(_stage(d)[1].get("endVolume")) is not None
            and (_f(_stage(d)[1].get("endVolume")) or 0) < 1e8 else None
        ),
    ),
    KunlunSensorDescription(
        key="bill_count", name="账单数", icon="mdi:file-document-outline", requires_token=True,
        value_fn=lambda d: (d.get("bills") or {}).get("total"),
    ),
)


class KunlunSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = False
    entity_description: KunlunSensorDescription

    def __init__(self, coordinator, description, customer_code, device_info):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = "kunlun_gas_%s_%s" % (customer_code, description.key)
        self.entity_id = "sensor.kunlun_gas_%s_%s" % (customer_code, description.key)
        self._attr_name = description.name
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.entity_description.requires_token:
            # 进阶项：没有令牌、或令牌失效降级时显示为不可用，但实体常驻，
            # 避免 options 变更导致实体反复增删、仪表盘引用丢失。
            data = self.coordinator.data or {}
            return bool(self.coordinator.has_token) and data.get("mode") == "full"
        return True

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (TypeError, KeyError, ValueError):
            return None

    @property
    def extra_state_attributes(self):
        fn = self.entity_description.attrs_fn
        if fn and self.coordinator.data:
            try:
                return fn(self.coordinator.data)
            except Exception:  # noqa: BLE001
                return None
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KunlunGasCoordinator = hass.data[DOMAIN][entry.entry_id]
    user = (coordinator.data or {}).get("user") or {}
    code = str(user.get("customerCode") or entry.unique_id or "unknown")
    device_info = DeviceInfo(
        identifiers={(DOMAIN, code)},
        name="昆仑燃气 %s" % code,
        manufacturer="中石油昆仑燃气",
        model=user.get("meterType_dictText") or "远传燃气表",
        configuration_url="https://bol.grs.petrochina.com.cn/retail/h5/",
    )
    descs = list(DESCRIPTIONS)
    async_add_entities([KunlunSensor(coordinator, desc, code, device_info) for desc in descs])
