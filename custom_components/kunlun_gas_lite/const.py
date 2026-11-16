"""昆仑燃气（小程序轻量版）常量。

2026-11-16 实测：/retail/preservice/gasFeePay/getDebt4Wsyyt 无需任何令牌即可
返回实时数据（表端余额、欠费、表读数、最后通信时间等），因此集成默认
走「免密模式」；只有填了 Authorization 令牌才会额外调用 /hall/close/ 系列
接口获取账单、日用气、月用气、阶梯气价等进阶数据。令牌过期会自动降级，
不影响免密传感器。
"""
from datetime import timedelta

DOMAIN = "kunlun_gas_lite"

BASE_URL = "https://bol.grs.petrochina.com.cn/retail"

# 免密接口（核心，永远可用）
PATH_BALANCE = "/preservice/gasFeePay/getDebt4Wsyyt"

# 需要令牌的进阶接口（可选）
PATH_DEFAULT_USER_CODE = "/hall/close/userCodeBind/getDefaultUserCode"
PATH_DAYS = "/hall/close/smartMeterGasRecords/smartMeterGasDaysRecords"
PATH_MONTHS = "/hall/close/smartMeterGasRecords/smartMeterGasMonthsRecords"
PATH_LADDER = "/hall/close/smartMeterGasRecords/getCalculateBillLine"
PATH_BILLS = "/hall/close/recharge/getBillList"

CONF_USER_CODE = "user_code"
CONF_REGION = "region"
CONF_MDM_CODE = "mdm_code"
CONF_ACCOUNT_ID = "account_id"
CONF_BADGE = "badge_number"
CONF_TOKEN = "token"
CONF_CLEAR_TOKEN = "clear_token"
CONF_OU_ID = "ou_id"
CONF_SCAN_INTERVAL = "scan_interval"

# 不内置任何账户/地区默认值：全部由用户在配置流程中自行填写
DEFAULT_USER_CODE = ""
DEFAULT_MDM_CODE = ""
DEFAULT_ACCOUNT_ID = ""
DEFAULT_BADGE = ""
DEFAULT_OU_ID = 0

DEFAULT_SCAN_INTERVAL = 3600
MIN_SCAN_INTERVAL = 1800
DAY_WINDOW = 31

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) "
    "NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF "
    "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541f38) XWEB/25819 "
    "miniProgram/wx65cb710bcf71a067"
)


def build_headers(token: str = "", mdm_code: str = DEFAULT_MDM_CODE) -> dict:
    """还原小程序 H5 请求头。token 为空时即为免密模式。"""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://bol.grs.petrochina.com.cn/retail/h5/",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Cookie": "X-Mdm-Code=" + mdm_code,
    }
    if token:
        token = token.strip()
        headers["Authorization"] = (
            token if token.lower().startswith("bearer ") else "Bearer " + token
        )
    return headers
