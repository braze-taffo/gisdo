"""脚本产出报告的类型化读取器。

脚本都把报告以 JSON 形式打到 stdout（部分还落盘为 extraction_manifest.json /
validation_report.json）。这里提供 dataclass 视图，避免上层裸按字符串键取值。
原始 dict 仍保留在 ``raw``。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractionManifest:
    raw: dict
    output_dir: str = ""
    all_verified: bool = False
    copied_count: int = 0
    uncopied_count: int = 0
    copied: list[dict] = field(default_factory=list)
    uncopied: list[dict] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> ExtractionManifest:
        copied = list(data.get("copied", []) or [])
        uncopied = list(data.get("uncopied", []) or [])
        return cls(
            raw=data,
            output_dir=str(data.get("output_dir", "")),
            all_verified=bool(data.get("all_verified", False)),
            copied_count=len(copied),
            uncopied_count=len(uncopied),
            copied=copied,
            uncopied=uncopied,
        )


@dataclass
class PackageReport:
    raw: dict
    output_ppkx: str = ""
    output_bytes: int = 0
    maps_count: int = 0
    layouts_count: int = 0
    layers_count: int = 0
    broken_count: int = 0
    exists: bool = False
    messages: str = ""

    @classmethod
    def from_json(cls, data: dict) -> PackageReport:
        return cls(
            raw=data,
            output_ppkx=str(data.get("output_ppkx", "")),
            output_bytes=int(data.get("output_bytes", 0) or 0),
            maps_count=int(data.get("maps_count", 0) or 0),
            layouts_count=int(data.get("layouts_count", 0) or 0),
            layers_count=int(data.get("layers_count", 0) or 0),
            broken_count=int(data.get("broken_count", 0) or 0),
            exists=bool(data.get("output_exists", False)),
            messages=str(data.get("messages", "") or ""),
        )


@dataclass
class ValidationReport:
    raw: dict
    passed: bool = False
    package: str = ""
    output_dir: str = ""
    extracted_aprx: str = ""
    extracted_file_count: int = 0
    extracted_bytes: int = 0
    broken_count: int = 0
    layout_signatures_match: bool | None = None
    messages: str = ""

    @classmethod
    def from_json(cls, data: dict) -> ValidationReport:
        return cls(
            raw=data,
            passed=bool(data.get("passed", False)),
            package=str(data.get("package", "")),
            output_dir=str(data.get("output_dir", "")),
            extracted_aprx=str(data.get("extracted_aprx", "")),
            extracted_file_count=int(data.get("extracted_file_count", 0) or 0),
            extracted_bytes=int(data.get("extracted_bytes", 0) or 0),
            broken_count=int(data.get("broken_count", 0) or 0),
            layout_signatures_match=data.get("layout_signatures_match"),
            messages=str(data.get("messages", "") or ""),
        )


@dataclass
class VerifyReport:
    raw: dict
    passed: bool = False
    path: str = ""
    width: int = 0
    height: int = 0
    non_white_pixels: int = 0
    non_white_ratio: float = 0.0
    bytes: int = 0

    @classmethod
    def from_json(cls, data: dict) -> VerifyReport:
        return cls(
            raw=data,
            passed=bool(data.get("passed", False)),
            path=str(data.get("path", "")),
            width=int(data.get("width", 0) or 0),
            height=int(data.get("height", 0) or 0),
            non_white_pixels=int(data.get("non_white_pixels", 0) or 0),
            non_white_ratio=float(data.get("non_white_ratio", 0.0) or 0.0),
            bytes=int(data.get("bytes", 0) or 0),
        )


@dataclass
class RenderReport:
    raw: dict
    output_png: str = ""
    output_pdf: str | None = None
    passed: bool = False
    feature_count: int = 0
    part_count: int = 0
    breaks: list[float] = field(default_factory=list)
    value_min: float = 0.0
    value_max: float = 0.0

    @classmethod
    def from_json(cls, data: dict) -> RenderReport:
        return cls(
            raw=data,
            output_png=str(data.get("output_png", "")),
            output_pdf=data.get("output_pdf"),
            passed=bool(data.get("passed", False)),
            feature_count=int(data.get("feature_count", 0) or 0),
            part_count=int(data.get("part_count", 0) or 0),
            breaks=[float(b) for b in (data.get("breaks", []) or [])],
            value_min=float(data.get("value_min", 0.0) or 0.0),
            value_max=float(data.get("value_max", 0.0) or 0.0),
        )
