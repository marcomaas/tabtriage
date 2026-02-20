"""Pydantic models for TabTriage API."""

from pydantic import BaseModel
from typing import Optional


class TabData(BaseModel):
    url: str
    title: str
    content: Optional[str] = None
    favicon: Optional[str] = None


class CaptureRequest(BaseModel):
    window_title: Optional[str] = None
    tabs: list[TabData]


class TriageRequest(BaseModel):
    tab_id: int
    category: str  # read-later | reference | actionable | archive | dismiss
    project_id: Optional[str] = None
    user_note: Optional[str] = None
    tags: Optional[list[str]] = None
    notion_target: Optional[str] = None  # links | parken | project


class SessionOut(BaseModel):
    id: int
    window_title: Optional[str]
    captured_at: str
    status: str
    tab_count: int


class TabOut(BaseModel):
    id: int
    session_id: int
    url: str
    title: str
    favicon: Optional[str]
    summary: Optional[str]
    suggested_category: Optional[str]
    category: Optional[str]
    project_id: Optional[str]
    user_note: Optional[str]
    tags: Optional[list[str]]
    captured_at: str
    triaged_at: Optional[str]


class TabDetail(TabOut):
    content: Optional[str]


class SearchResult(BaseModel):
    id: int
    title: str
    url: str
    summary: Optional[str]
    snippet: str
    session_id: int
