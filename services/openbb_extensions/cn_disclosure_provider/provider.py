"""OpenBB Provider 注册对象（中国法定披露）。

entry point::

    [project.entry-points."openbb_provider_extension"]
    cn_disclosure = "openbb_cn_disclosure.provider:cn_disclosure_provider"

REST 路由：``GET /api/v1/news/company?provider=cn_disclosure&symbol=600519``

⚠️ entry point 注册未经实机验证（本机 .venv 未装 openbb）。见 docs/data-sources.md。
"""

from __future__ import annotations

from openbb_core.provider.abstract.provider import Provider

from .models.company_news import CnDisclosureCompanyNewsFetcher

cn_disclosure_provider = Provider(
    name="cn_disclosure",
    website="http://www.cninfo.com.cn",
    description=(
        "中国法定披露公告：巨潮资讯（证监会指定信息披露平台）原文。"
        "只返回巨潮/上交所/深交所原文，不返回媒体转载。"
        "公开数据仅限个人研究，不得重新分发。"
    ),
    credentials=None,
    fetcher_dict={
        "CompanyNews": CnDisclosureCompanyNewsFetcher,
    },
    repr_name="China Statutory Disclosure (cninfo)",
)
