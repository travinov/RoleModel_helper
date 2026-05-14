from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParseIssue:
    severity: str
    stage: str
    sheet_name: str
    source_row: Optional[int]
    source_col: Optional[int]
    error_code: str
    message: str
    raw_value: Optional[str] = None


@dataclass
class StructurePath:
    path_order: int
    path_raw: str
    segments: list[str]


@dataclass
class ListEntry:
    name: str
    code: Optional[str]
    raw_line: str
    parse_status: str


@dataclass
class JustificationEntry:
    system_name_raw: str
    access_level: int
    justification_text: Optional[str]
    raw_value: str
    source_col: int


@dataclass
class AccessEntry:
    source_col: int
    system_name_raw: str
    access_level: int
    inline_comment: Optional[str]
    raw_value: str
    source_row: int


@dataclass
class ProfileRecord:
    source_row: int
    profile_code: str
    profile_name: str
    profile_type: Optional[str]
    profile_raw: str
    profile_justification_raw: Optional[str]
    structure_paths: list[StructurePath] = field(default_factory=list)
    departments: list[ListEntry] = field(default_factory=list)
    positions: list[ListEntry] = field(default_factory=list)
    justifications: list[JustificationEntry] = field(default_factory=list)
    accesses: list[AccessEntry] = field(default_factory=list)


@dataclass
class SystemRecord:
    system_name_raw: str
    ci_code: Optional[str]
    source_col_start: int
    source_col_end: int
    reason_col: Optional[int]


@dataclass
class EntitlementRecord:
    source_col: int
    system_name_raw: str
    entitlement_type: str
    entitlement_name: str
    header_raw: str


@dataclass
class ParseResult:
    source_file: str
    sheet_name: str
    model_code: Optional[str]
    model_name: Optional[str]
    rows_read: int
    systems: list[SystemRecord]
    entitlements: list[EntitlementRecord]
    profiles: list[ProfileRecord]
    issues: list[ParseIssue]

    @property
    def errors_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity.upper() == "ERROR")

    @property
    def warnings_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity.upper() == "WARNING")

    @property
    def access_count(self) -> int:
        return sum(len(profile.accesses) for profile in self.profiles)

    @property
    def justifications_count(self) -> int:
        return sum(len(profile.justifications) for profile in self.profiles)

