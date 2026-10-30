"""产品常量。"""

PRODUCT_NAME = "蜂算科研"
ENGINE_CREDIT = "powered by OpenLens"
PRODUCT_VERSION = "0.2.0"  # 覆盖 H5.8–H5.174 与组织入驻 §64–§66，不另起版本号

LAB_JOIN_CONSENT = (
    "本实验室启用全组都能看见：成员可查看彼此的在读文献、科研进度与手稿正文。"
    "手稿仅作者可以修改。管理员与导师可查看全部记录。继续即表示同意。"
)

GUIDE_ITEMS = [
    {"key": "invite", "label": "邀请至少一名学生", "path": "/members"},
    {"key": "lab_key", "label": "配置实验室兜底 API Key（若成员未填个人 Key）", "path": "/settings/llm"},
    {"key": "import_lit", "label": "导入 BibTeX 或上传 PDF", "path": "/literature"},
    {"key": "pick_direction", "label": "选定研究方向", "path": "/directions"},
    {"key": "open_direction_papers", "label": "打开本方向文献", "path": "/literature"},
    {"key": "mark_read", "label": "至少一篇标为在读", "path": "/literature"},
    {"key": "manuscript", "label": "创建一篇手稿", "path": "/manuscripts"},
    {"key": "cockpit", "label": "打开进度看到学生卡片", "path": "/cockpit"},
]
