from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class TokenPair(BaseModel):
    access: str
    refresh: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class RefreshBody(BaseModel):
    refresh: str


class RegisterBody(BaseModel):
    invite_token: str
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = ""
    consent: bool = True

    @field_validator("password")
    @classmethod
    def password_has_letter_and_digit(cls, v: str) -> str:
        has_letter = any(ch.isalpha() for ch in v)
        has_digit = any(ch.isdigit() for ch in v)
        if not (has_letter and has_digit):
            raise ValueError("密码须同时包含字母和数字")
        return v


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def password_has_letter_and_digit(cls, v: str) -> str:
        has_letter = any(ch.isalpha() for ch in v)
        has_digit = any(ch.isdigit() for ch in v)
        if not (has_letter and has_digit):
            raise ValueError("密码须同时包含字母和数字")
        return v


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(teacher|student)$")


class ApplyOrgBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = ""
    org_name: str = Field(min_length=2, max_length=200)
    kind: str = "other"
    contact_name: str = ""
    contact_phone: str = ""
    apply_note: str = ""
    consent: bool = True

    @field_validator("password")
    @classmethod
    def password_has_letter_and_digit(cls, v: str) -> str:
        has_letter = any(ch.isalpha() for ch in v)
        has_digit = any(ch.isdigit() for ch in v)
        if not (has_letter and has_digit):
            raise ValueError("密码须同时包含字母和数字")
        return v

    @field_validator("kind")
    @classmethod
    def kind_ok(cls, v: str) -> str:
        allowed = {"university", "hospital", "company", "independent", "other"}
        key = (v or "other").strip().lower()
        return key if key in allowed else "other"


class OrgResubmitBody(BaseModel):
    apply_note: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None


class OrgReviewBody(BaseModel):
    action: str = Field(pattern="^(approve|reject|suspend|resume)$")
    note: str = ""


class LabCreate(BaseModel):
    name: str
    code: str
    description: str = ""
    org_id: str | None = None


class LabPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    settings: dict | None = None


class MemberAdd(BaseModel):
    user_id: str
    role: str = Field(pattern="^(teacher|student)$")


class MemberPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(teacher|student)$")
    title: str | None = Field(default=None, max_length=80)
    direction_id: str | None = None
    direction_ids: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(active|on_leave|graduated)$")


class DirectionCreate(BaseModel):
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    owner_user_id: str | None = None
    sort: int | None = None


class DirectionPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    status: str | None = None
    append_note: str | None = Field(default=None, max_length=20000)
    parent_id: str | None = None
    owner_user_id: str | None = None
    sort: int | None = None
    route: list[dict] | None = None


class CredentialUpsert(BaseModel):
    provider: str = "openai"
    base_url: str = ""
    api_key: str | None = None
    model_chat: str = ""
    model_vision: str = ""
    model_embed: str = ""
    model_rerank: str = ""
    kind: str = "cloud"

    @field_validator("kind")
    @classmethod
    def kind_ok(cls, v: str) -> str:
        s = (v or "cloud").strip() or "cloud"
        if s not in {"cloud", "onprem"}:
            raise ValueError("kind 须为 cloud 或 onprem")
        return s

    @field_validator("base_url")
    @classmethod
    def base_url_strip(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def base_url_http(self):
        if not self.base_url:
            return self
        from fengsuan.app.core.netguard import assert_llm_base_url

        try:
            self.base_url = assert_llm_base_url(self.base_url, kind=self.kind)
        except Exception as exc:
            raise ValueError(getattr(exc, "detail", None) or "base_url 须为 http(s)") from exc
        return self


class LiteratureSearchBody(BaseModel):
    q: str
    sources: list[str] = Field(default_factory=lambda: ["arxiv"])
    year: int | None = None
    max_per_source: int = 8
    language: str | None = None
    direction_id: str | None = None


class LiteratureManual(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    doi: str | None = None
    url: str | None = None
    venue: str | None = None
    publish_year: int | None = None
    arxiv_id: str | None = None
    pubmed_id: str | None = None
    cnki_id: str | None = None
    direction_ids: list[str] = Field(default_factory=list)
    mark_read: bool = False
    # 检索源给的作者编号（OpenAlex / Semantic Scholar / ORCID），用来分清同名作者
    author_details: list[dict] = Field(default_factory=list)
    openalex_id: str | None = None
    citation_count: int | None = None
    fetch_oa_pdf: bool = False
    oa_pdf_url: str | None = None


class ImportDoiBody(BaseModel):
    lab_id: str
    doi: str
    mark_read: bool = False
    direction_ids: list[str] = Field(default_factory=list)


class ImportBibtexBody(BaseModel):
    lab_id: str
    bibtex: str
    mark_read: bool = False
    direction_ids: list[str] = Field(default_factory=list)


class ImportCsvBody(BaseModel):
    lab_id: str
    csv_text: str
    mark_read: bool = False
    direction_ids: list[str] = Field(default_factory=list)


class ImportCaptureBody(BaseModel):
    lab_id: str
    page_url: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    doi: str | None = None
    venue: str | None = None
    publish_year: int | None = None
    site_adapter: str = "generic"
    raw_meta: dict = Field(default_factory=dict)
    page_html: str = Field(default="", max_length=400000)
    mark_read: bool = False
    pdf_base64: str | None = None
    pdf_filename: str = "capture.pdf"
    pdf_fetch_failed: bool = False
    direction_ids: list[str] = Field(default_factory=list)


class MarkReadBody(BaseModel):
    status: str = Field(pattern="^(want_read|reading|finished|dropped)$")
    progress_pct: float | None = Field(default=None, ge=0, le=100)


class PinCoreBody(BaseModel):
    direction_id: str
    is_core: bool = True


class ReadingProgressBody(BaseModel):
    page_no: int | None = Field(default=None, ge=1, le=20000)
    page_count: int | None = Field(default=None, ge=1, le=20000)
    progress_pct: float | None = Field(default=None, ge=0, le=100)


class PageTranslationBody(BaseModel):
    page_no: int = Field(ge=1, le=20000)
    lang: str = Field(default="zh", max_length=16)
    text: str = Field(default="", max_length=40000)


class NoteAnchor(BaseModel):
    """锚定高亮：页码 + 归一化矩形 + 原文子串。原文子串用于事后校验锚点还对不对。"""

    page_no: int | None = Field(default=None, ge=1, le=20000)
    quote: str = Field(default="", max_length=400)
    rects: list[dict[str, float]] = Field(default_factory=list, max_length=40)


class NoteCreate(BaseModel):
    content_md: str = Field(min_length=1, max_length=20000)
    page_no: int | None = None
    anchor: NoteAnchor | None = None


class NotePatch(BaseModel):
    content_md: str | None = Field(default=None, min_length=1, max_length=20000)
    page_no: int | None = None
    anchor: NoteAnchor | None = None


class ManuscriptCreate(BaseModel):
    title: str
    kind: str = Field(default="other", pattern="^(conference|journal|survey|proposal|thesis|other)$")
    content_md: str = ""
    direction_ids: list[str] = Field(default_factory=list)


class ManuscriptPatch(BaseModel):
    title: str | None = None
    kind: str | None = Field(default=None, pattern="^(conference|journal|survey|proposal|thesis|other)$")
    status: str | None = Field(default=None, pattern="^(draft|internal_review|submitted|revision|published|rejected)$")
    content_md: str | None = None
    expected_updated_at: str | None = None
    venue: str | None = None
    overleaf_git_url: str | None = None


class CommentCreate(BaseModel):
    body: str
    anchor_json: dict = Field(default_factory=dict)


class WorkLogCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class GlossaryCreate(BaseModel):
    src: str
    tgt: str = ""
    lang_pair: str = "en-zh"
    do_not_translate: bool = False
    domain: str = ""


class ConfirmToolCall(BaseModel):
    tool: str = Field(default="", max_length=64)
    args: dict = Field(default_factory=dict)


class AskHistoryTurn(BaseModel):
    q: str = Field(default="", max_length=2000)
    a: str = Field(default="", max_length=4000)


class AskContext(BaseModel):
    kind: Literal["direction", "paper", "member"] = "direction"
    id: str = Field(default="", max_length=64)


class KbQueryBody(BaseModel):
    q: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    direction_id: str | None = None
    # kb=原有单轮检索问答；lab / onboarding 走 Agent 运行时，可多轮追问。
    scope: Literal["kb", "lab", "onboarding"] = "kb"
    session_id: str | None = Field(default=None, max_length=64)
    confirm: bool = False
    confirm_tools: list[ConfirmToolCall] = Field(default_factory=list)
    history: list[AskHistoryTurn] = Field(default_factory=list)
    stream: bool = False
    context: AskContext | None = None

    @field_validator("confirm_tools")
    @classmethod
    def _cap_confirm_tools(cls, v):
        return (v or [])[:3]

    @field_validator("history")
    @classmethod
    def _cap_history(cls, v):
        return (v or [])[-2:]


class MemberProfilePatch(BaseModel):
    bio: str | None = Field(default=None, max_length=2000)
    interests: list[str] | None = None
    skills: list[str] | None = None
    advisor_user_id: str | None = None
    enrolled_at: str | None = None
    expected_graduate_at: str | None = None
    homepage: str | None = Field(default=None, max_length=300)
    name_en: str | None = Field(default=None, max_length=200)
    aliases: list[str] | None = None
    affiliation: str | None = Field(default=None, max_length=300)
    past_affiliations: list[dict] | None = None
    education: list[dict] | None = None
    links: dict | None = None
    works: list[dict] | None = None
    ai_note: str | None = Field(default=None, max_length=1000)
    sources: list[dict] | None = None
    files: list[str] | None = None

    @field_validator("interests", "skills")
    @classmethod
    def _cap_list(cls, v):
        if v is None:
            return v
        items = [str(x).strip() for x in v if str(x).strip()]
        if len(items) > 20:
            raise ValueError("最多 20 条")
        if any(len(x) > 60 for x in items):
            raise ValueError("单条不超过 60 字")
        return items


class KbFeedbackBody(BaseModel):
    chunk_id: str
    useful: bool = True
    q: str = ""


class LanguageBody(BaseModel):
    text: str = Field(default="", max_length=20000)
    lab_id: str | None = None
    target: str = "zh"
    polish_level: str = "academic"
    expand_goal: str = "related_work"
    lang_pair: str = "en-zh"
    manuscript_id: str | None = None
    write_mode: str = ""
    selection: str = ""
    heading: str = ""
    expected_updated_at: str | None = None
    venue_style_notes: str = ""
    note_id: str | None = None
    literature_item_id: str | None = None
    literature_item_ids: list[str] = Field(default_factory=list)
    direction_id: str | None = None
    write_note: bool = False
    page_no: int | None = Field(default=None, ge=1, le=20000)
    stream: bool = False
    context: str = Field(default="", max_length=4000)


class OutlineBody(BaseModel):
    title: str = ""
    kind: str = "survey"
    lab_id: str | None = None
    direction_id: str | None = None
    literature_item_ids: list[str] = Field(default_factory=list)
    manuscript_id: str | None = None
    write_mode: str = ""
    selection: str = ""
    heading: str = ""
    expected_updated_at: str | None = None


class WriteSectionBody(BaseModel):
    heading: str
    notes: str = ""
    lab_id: str | None = None
    literature_item_ids: list[str] = Field(default_factory=list)
    manuscript_id: str | None = None
    write_mode: str = ""
    selection: str = ""
    expected_updated_at: str | None = None
    rewrite: bool = False
    prior_draft: str = Field(default="", max_length=20000)
    direction_id: str | None = None


class WritebackBody(BaseModel):
    manuscript_id: str
    text: str = Field(min_length=1, max_length=200000)
    write_mode: str = Field(pattern="^(replace_selection|append_heading|append)$")
    selection: str = ""
    heading: str = ""
    expected_updated_at: str | None = None
    literature_item_ids: list[str] = Field(default_factory=list)


class AnalyzeBody(BaseModel):
    analysis_type: str = "method_card"


class TutorBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    page_no: int | None = Field(default=None, ge=1, le=20000)
    write_note: bool = False
    session_id: str | None = Field(default=None, max_length=64)
    stream: bool = False
    excerpt: str | None = Field(default=None, max_length=4000)
    context: str | None = Field(default=None, max_length=4000)
    # paper=单篇精读（默认），lab=跨篇提问。两者走同一个 Agent 运行时，只是工具域不同。
    scope: Literal["paper", "lab"] = "paper"
    confirm: bool = False
    confirm_tools: list[ConfirmToolCall] = Field(default_factory=list)

    @field_validator("confirm_tools")
    @classmethod
    def _cap_tutor_confirm_tools(cls, v):
        return (v or [])[:3]


class CompareBody(BaseModel):
    item_ids: list[str]
    lab_id: str | None = None


class ResearchRunCreate(BaseModel):
    question: str
    dataset_path: str = ""
    dataset_id: str | None = None
    literature_item_ids: list[str] = Field(default_factory=list)
    domain: str = Field(default="general", pattern="^(general|medical|management|materials)$")
    language: str = "chs"
    interrupt_after: str = ""


class RunResumeBody(BaseModel):
    from_subgraph: str = ""
    refine_suggestion: str = ""


class OverleafSyncBody(BaseModel):
    git_url: str | None = None
    token: str | None = None


class ReproduceRunCreate(BaseModel):
    literature_item_id: str | None = None
    dataset_path: str = ""
    dataset_id: str | None = None
    claimed_metrics: dict = Field(default_factory=dict)


class DatasetCreate(BaseModel):
    name: str
    description: str = ""
    source_note: str = ""
    license: str = ""
    local_path: str = ""
    literature_item_ids: list[str] = Field(default_factory=list)


class DatasetPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    source_note: str | None = None
    license: str | None = None
    local_path: str | None = None
    literature_item_ids: list[str] | None = None


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    assignee_id: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(todo|doing|done)$")
    assignee_id: str | None = None
    priority: str | None = Field(default=None, pattern="^(low|normal|high)$")


class MeetingCreate(BaseModel):
    title: str
    notes_raw: str = ""


class MinutesBody(BaseModel):
    notes_raw: str = ""
    assign_to: list[str] = Field(default_factory=list)


class MilestoneCreate(BaseModel):
    kind: str = Field(default="proposal", pattern="^(proposal|midterm|defense)$")
    title: str = ""
    notes: str = ""
    manuscript_id: str | None = None
    status: str = Field(default="planned", pattern="^(planned|in_progress|done)$")
    due_at: str | None = None
    owner_id: str | None = None


class MilestonePatch(BaseModel):
    kind: str | None = Field(default=None, pattern="^(proposal|midterm|defense)$")
    title: str | None = None
    notes: str | None = None
    manuscript_id: str | None = None
    status: str | None = Field(default=None, pattern="^(planned|in_progress|done)$")
    due_at: str | None = None
