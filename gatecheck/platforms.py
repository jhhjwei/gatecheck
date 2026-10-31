"""Built-in platform word lists for G009.

The common rules are a copy of the author's own pre-publish checker
(`tools/preflight.py` in the content-ops repo). Keep the two in sync when a
platform changes its rules. No account identifiers belong in this file.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

Pattern = Tuple[str, str]

COMMON_HIGH: List[Pattern] = [
    (r"换脸|人脸替换|AI\s*换脸", "人脸合成/换脸类题材，平台高风险"),
    (r"复活|让逝者|逝者说话|数字永生|数字复活", "数字复活/逝者模拟类题材，平台高风险"),
    (r"代登录|代实名|代发布|代运营|代刷|刷单|养号|批量注册", "代操作/刷量类服务"),
    (r"破解|绿色版|激活码|去水印|外挂|私服", "破解/外挂类工具"),
    (r"保证上热门|包上热门|包过|必过|月入过万|稳赚不赔", "绝对化承诺"),
    (r"微信(号)?\s*[:：]|加\s*微信|V\s*[:：]\s*[A-Za-z]|QQ\s*[:：]\s*\d", "站外联系方式"),
    (r"身份证|银行卡|实名信息|租号|买卖账号", "证件/账号交易"),
    (r"爬虫|爬取|抓取用户|采集手机号", "数据抓取类"),
]

COMMON_MEDIUM: List[Pattern] = [
    (r"AI\s*生成|AI\s*重绘|AI\s*绘画|AI\s*数字人|虚拟人", "AI 标注词：商品页易被误判，改放交付文件元数据"),
]

PLATFORM_RULES: Dict[str, Dict[str, object]] = {
    "xianyu": {
        "high": COMMON_HIGH
        + [
            (r"老照片.{0,6}动起来|照片.{0,4}复活|亲人.{0,4}回来", "老照片动态化易被判数字复活类"),
            (r"接单|代做|代剪", "服务类目对'代做'敏感，改写为'定制/协助'"),
        ],
        "medium": COMMON_MEDIUM
        + [(r"不是|不用|不做|不接|禁止|杜绝", "出现否定句：机审不看语义，只匹配词")],
        "max_len": 1500,
        "first_line_max": 30,
    },
    "xhs": {
        "high": COMMON_HIGH
        + [
            (r"最(好|便宜|有效|快)|第一|唯一|100%|百分百", "绝对化用语"),
            (r"包治|根治|疗效|药到病除", "医疗功效宣称"),
            (r"私信领|评论区扣|扣1|加我", "诱导互动/站外引流"),
        ],
        "medium": COMMON_MEDIUM,
        "max_len": 1000,
        "first_line_max": 20,
    },
    "csdn": {
        "high": COMMON_HIGH,
        "medium": COMMON_MEDIUM
        + [(r"代做|接单|报价|私聊下单", "站内硬广，改为站内私信/主页 CTA")],
        "max_len": 60000,
        "first_line_max": 80,
    },
    "kuaishou": {
        "high": COMMON_HIGH + [(r"影视原片|搬运|混剪", "影视搬运风险")],
        "medium": COMMON_MEDIUM,
        "max_len": 2000,
        "first_line_max": 60,
    },
    "toutiao": {
        "high": COMMON_HIGH
        + [(r"震惊|速看|必看|重磅|炸裂|加\s*微信", "标题党/站外导流禁用词")],
        "medium": COMMON_MEDIUM + [(r"私信", "站内私信可留，但正文出现易被判导流")],
        "max_len": 2000,
        "first_line_max": 22,
    },
    "baijiahao": {
        "high": COMMON_HIGH + [(r"震惊|速看|必看|重磅|炸裂", "标题党禁用词")],
        "medium": COMMON_MEDIUM,
        "max_len": 5000,
        "first_line_max": 40,
    },
    "douyin": {
        "high": COMMON_HIGH + [(r"影视原片|搬运|混剪|明星同款", "影视/肖像搬运风险")],
        "medium": COMMON_MEDIUM,
        "max_len": 2000,
        "first_line_max": 55,
    },
    "wechat": {
        "high": COMMON_HIGH,
        "medium": COMMON_MEDIUM,
        "max_len": 20000,
        "first_line_max": 64,
    },
}

DEFAULT_RULES: Dict[str, object] = {
    "high": COMMON_HIGH,
    "medium": COMMON_MEDIUM,
    "max_len": 60000,
    "first_line_max": 80,
}



def rules_for(platform: str) -> Dict[str, object]:
    return PLATFORM_RULES.get(platform, DEFAULT_RULES)


def known_platforms() -> List[str]:
    return sorted(PLATFORM_RULES)
