from __future__ import annotations

import io
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import reportlab
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from app import __version__
from app.calculations import weighted_average
from app.database import utcnow
from app.models import Account
from app.services.dashboard import dashboard_snapshot

REPOSITORY_URL = "https://github.com/OshTekk/IMTegrale"
PASS_URL = "https://pass.imt-atlantique.fr/"  # noqa: S105 - public service URL
COMPETENCES_URL = "https://hub.imt-atlantique.fr/comp2/"
VALID_SEMESTERS = frozenset({"S5", "S6", "S7", "S8", "S9", "S10"})

INK = HexColor("#17262B")
MUTED = HexColor("#627177")
SOFT = HexColor("#879398")
LINE = HexColor("#D7E1DE")
SURFACE = HexColor("#F4F7F6")
BRAND = HexColor("#0B5A58")
BRAND_DARK = HexColor("#073F40")
BRAND_SOFT = HexColor("#DEEFEB")
CORAL = HexColor("#D9654F")
CORAL_SOFT = HexColor("#FAE9E4")
AMBER = HexColor("#9B6926")
AMBER_SOFT = HexColor("#F8EDDC")
RED = HexColor("#A7433A")
RED_SOFT = HexColor("#F8E7E4")


class AcademicReportUnavailable(ValueError):
    pass


class BrandMark(Flowable):
    def __init__(self, size: float = 11 * mm) -> None:
        super().__init__()
        self.width = size
        self.height = size

    def draw(self) -> None:
        drawing = self.canv
        scale = self.width / 40
        drawing.saveState()
        drawing.scale(scale, scale)
        drawing.setFillColor(BRAND_DARK)
        drawing.roundRect(0, 0, 40, 40, 8, stroke=0, fill=1)
        drawing.setStrokeColor(colors.white)
        drawing.setLineWidth(3.2)
        drawing.setLineCap(1)
        path = drawing.beginPath()
        path.moveTo(28, 32)
        path.curveTo(22.3, 32, 20.6, 29.2, 20, 24.7)
        path.lineTo(18.8, 15.3)
        path.curveTo(18.2, 10.8, 16.5, 8, 10.8, 8)
        drawing.drawPath(path, stroke=1, fill=0)
        drawing.setFillColor(CORAL)
        drawing.circle(28, 32, 2.6, stroke=0, fill=1)
        drawing.setFillColor(HexColor("#8BD3C7"))
        drawing.circle(10.8, 8, 2.6, stroke=0, fill=1)
        drawing.restoreState()


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        page_count = len(self._page_states)
        for state in self._page_states:
            self.__dict__.update(state)
            self.setFont("IMT-Regular", 7)
            self.setFillColor(MUTED)
            self.drawRightString(A4[0] - 16 * mm, 11 * mm, f"Page {self._pageNumber} / {page_count}")
            super().showPage()
        super().save()


class AcademicReportDocTemplate(BaseDocTemplate):
    def __init__(self, buffer: io.BytesIO, *, generated_label: str) -> None:
        super().__init__(
            buffer,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title="Relevé académique personnel - IMTégrale",
            author="IMTégrale",
            subject="Synthèse académique personnelle à titre informatif",
            creator=f"IMTégrale {__version__}",
        )
        self.generated_label = generated_label
        self._outline_index = 0
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="academic-report",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=self._decorate_page))

    def _decorate_page(self, page_canvas: canvas.Canvas, _doc: BaseDocTemplate) -> None:
        page_canvas.saveState()
        if page_canvas.getPageNumber() > 1:
            page_canvas.setFont("IMT-Bold", 7.5)
            page_canvas.setFillColor(BRAND_DARK)
            page_canvas.drawString(16 * mm, A4[1] - 10.5 * mm, "IMTégrale")
            page_canvas.setFont("IMT-Regular", 7.5)
            page_canvas.setFillColor(MUTED)
            page_canvas.drawString(33 * mm, A4[1] - 10.5 * mm, "Relevé académique personnel")
            page_canvas.setFont("IMT-Bold", 6.7)
            page_canvas.setFillColor(CORAL)
            page_canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 10.5 * mm, "À TITRE INFORMATIF")
        page_canvas.setStrokeColor(LINE)
        page_canvas.setLineWidth(0.5)
        page_canvas.line(16 * mm, 14.5 * mm, A4[0] - 16 * mm, 14.5 * mm)
        page_canvas.setFont("IMT-Regular", 6.8)
        page_canvas.setFillColor(MUTED)
        page_canvas.drawString(
            16 * mm,
            11 * mm,
            f"Document personnel non officiel · {self.generated_label} · IMTégrale {__version__}",
        )
        page_canvas.restoreState()

    def afterFlowable(self, flowable: Flowable) -> None:  # noqa: N802
        if not isinstance(flowable, Paragraph) or flowable.style.name != "ReportSection":
            return
        self._outline_index += 1
        key = f"section-{self._outline_index}"
        title = flowable.getPlainText()
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, level=0, closed=False)


def _register_fonts() -> None:
    if "IMT-Regular" in pdfmetrics.getRegisteredFontNames():
        return
    font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    pdfmetrics.registerFont(TTFont("IMT-Regular", font_dir / "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("IMT-Bold", font_dir / "VeraBd.ttf"))
    pdfmetrics.registerFont(TTFont("IMT-Italic", font_dir / "VeraIt.ttf"))
    pdfmetrics.registerFontFamily(
        "IMT",
        normal="IMT-Regular",
        bold="IMT-Bold",
        italic="IMT-Italic",
        boldItalic="IMT-Bold",
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "ReportBrand",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=14,
            leading=16,
            textColor=INK,
        ),
        "brand_detail": ParagraphStyle(
            "ReportBrandDetail",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=6.8,
            leading=9,
            textColor=MUTED,
        ),
        "report_title": ParagraphStyle(
            "ReportTitle",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=15.5,
            leading=18,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
        "eyebrow": ParagraphStyle(
            "ReportEyebrow",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=6.4,
            leading=8,
            textColor=BRAND,
            spaceAfter=2,
        ),
        "student": ParagraphStyle(
            "ReportStudent",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=18,
            leading=21,
            textColor=INK,
        ),
        "identity_detail": ParagraphStyle(
            "ReportIdentityDetail",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
        "notice": ParagraphStyle(
            "ReportNotice",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=7.5,
            leading=10.5,
            textColor=BRAND_DARK,
        ),
        "metric_label": ParagraphStyle(
            "ReportMetricLabel",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=6.1,
            leading=7.5,
            textColor=MUTED,
        ),
        "metric_value": ParagraphStyle(
            "ReportMetricValue",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=17,
            leading=19,
            textColor=INK,
        ),
        "metric_detail": ParagraphStyle(
            "ReportMetricDetail",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=6.3,
            leading=8,
            textColor=SOFT,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName="IMT-Bold",
            fontSize=13,
            leading=16,
            textColor=INK,
            spaceBefore=0,
            spaceAfter=8,
        ),
        "section_detail": ParagraphStyle(
            "ReportSectionDetail",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=7.5,
            leading=10.5,
            textColor=MUTED,
            spaceAfter=8,
        ),
        "table_header": ParagraphStyle(
            "ReportTableHeader",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=6.3,
            leading=8,
            textColor=MUTED,
        ),
        "table": ParagraphStyle(
            "ReportTable",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=7.2,
            leading=9.5,
            textColor=INK,
        ),
        "table_bold": ParagraphStyle(
            "ReportTableBold",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=7.2,
            leading=9.5,
            textColor=INK,
        ),
        "table_muted": ParagraphStyle(
            "ReportTableMuted",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=6.2,
            leading=8,
            textColor=MUTED,
        ),
        "source": ParagraphStyle(
            "ReportSource",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=7.2,
            leading=10,
            textColor=INK,
        ),
        "legal": ParagraphStyle(
            "ReportLegal",
            parent=base["Normal"],
            fontName="IMT-Regular",
            fontSize=6.8,
            leading=9.5,
            textColor=MUTED,
        ),
        "link": ParagraphStyle(
            "ReportLink",
            parent=base["Normal"],
            fontName="IMT-Bold",
            fontSize=7.2,
            leading=10,
            textColor=BRAND,
        ),
    }


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value or "")), style)


def _rich(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(value, style)


def _format_number(value: float | int | None, *, decimals: int = 2) -> str:
    if value is None:
        return "Non disponible"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".").replace(".", ",")


def _format_datetime(value: datetime | None, timezone: str, *, prefix: str = "") -> str:
    if value is None:
        return "Non disponible"
    try:
        target_timezone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError):
        target_timezone = ZoneInfo("Europe/Paris")
    local = value.astimezone(target_timezone)
    return f"{prefix}{local:%d/%m/%Y à %H:%M}"


def _semester_sort(value: str | None) -> tuple[int, str]:
    if value and value[1:].isdigit():
        return int(value[1:]), value
    return 999, value or ""


def _validated_credits(ues: list[dict[str, Any]]) -> float:
    total = 0.0
    for ue in ues:
        if not ue["validated"]:
            continue
        value = ue["earned_credits_ects"]
        if value is None:
            value = ue["credits_ects"]
        total += float(value or 0)
    return round(total, 2)


def _attempted_credits(ues: list[dict[str, Any]]) -> float:
    return round(sum(float(ue["credits_ects"] or 0) for ue in ues), 2)


def academic_report_snapshot(
    db: Session,
    account: Account,
    *,
    semester: str = "all",
) -> dict[str, Any]:
    if semester != "all" and semester not in VALID_SEMESTERS:
        raise ValueError("Semestre invalide")
    dashboard = dashboard_snapshot(db, account, role="owner", include_simulations=False)
    ues = [ue for ue in dashboard["ues"] if semester == "all" or ue["semester"] == semester]
    if not ues:
        raise AcademicReportUnavailable("Aucune UE n'est disponible pour ce périmètre")
    ues.sort(key=lambda ue: (*_semester_sort(ue["semester"]), ue["code"]))
    ue_codes = {ue["code"] for ue in ues}
    notes = [note for note in dashboard["notes"] if note["ue_code"] in ue_codes]
    notes.sort(key=lambda note: (note["ue_code"], note["detected_at"], note["id"]))
    average, average_credits = weighted_average(ues, "average")
    gpa, gpa_credits = weighted_average(ues, "gpa")

    semester_rows: list[dict[str, Any]] = []
    semester_names = sorted(
        {ue["semester"] for ue in ues if ue["semester"]},
        key=_semester_sort,
    )
    for semester_name in semester_names:
        semester_ues = [ue for ue in ues if ue["semester"] == semester_name]
        semester_average, semester_average_credits = weighted_average(semester_ues, "average")
        semester_gpa, semester_gpa_credits = weighted_average(semester_ues, "gpa")
        semester_rows.append(
            {
                "semester": semester_name,
                "average": semester_average,
                "average_credits": semester_average_credits,
                "gpa": semester_gpa,
                "gpa_credits": semester_gpa_credits,
                "validated_credits": _validated_credits(semester_ues),
                "attempted_credits": _attempted_credits(semester_ues),
                "ue_count": len(semester_ues),
            }
        )

    official_name = (
        " ".join(part for part in (account.official_first_name, account.official_last_name) if part) or None
    )
    return {
        "generated_at": utcnow(),
        "timezone": account.timezone,
        "scope": semester,
        "identity": {
            "official_name": official_name,
            "fallback_name": account.display_name,
            "official": official_name is not None,
            "observed_at": account.official_identity_at,
        },
        "profile": {
            "campus": account.campus,
            "campus_source": account.campus_source,
            "program": account.program,
            "promotion_year": account.promotion_year,
            "academic_source": account.academic_source,
            "observed_at": account.academic_verified_at or account.profile_refreshed_at,
        },
        "freshness": {
            "pass": account.last_sync_at,
            "competences": account.ue_metadata_refreshed_at,
            "profile": account.profile_refreshed_at,
        },
        "summary": {
            "average": average,
            "average_credits": average_credits,
            "gpa": gpa,
            "gpa_credits": gpa_credits,
            "validated_credits": _validated_credits(ues),
            "attempted_credits": _attempted_credits(ues),
            "ue_count": len(ues),
            "note_count": len(notes),
            "missing_ects_count": sum(ue["credits_ects"] is None for ue in ues),
        },
        "semesters": semester_rows,
        "ues": ues,
        "notes": notes,
    }


def _scope_label(scope: str) -> str:
    return "Tous les semestres disponibles" if scope == "all" else f"Semestre {scope}"


def _profile_label(profile: dict[str, Any]) -> str:
    values: list[str] = []
    program = profile["program"]
    campus = profile["campus"]
    if program and program != "unknown":
        values.append(str(program).upper())
    if profile["promotion_year"]:
        values.append(f"promotion {profile['promotion_year']}")
    if campus and campus != "unknown":
        values.append(str(campus).capitalize())
    return " · ".join(values) or "Profil académique non disponible"


def _grade_status(ue: dict[str, Any]) -> tuple[str, colors.Color, colors.Color]:
    if ue["grade"] in {"FX", "F"}:
        return "Rattrapage requis", RED, RED_SOFT
    if ue["grade"] == "E":
        return "Validée après rattrapage", BRAND_DARK, BRAND_SOFT
    if ue["validated"]:
        return "Validée", BRAND_DARK, BRAND_SOFT
    if ue["grade"] is None and ue["average"] is None:
        return "En attente", MUTED, SURFACE
    return "Non validée", RED, RED_SOFT


def _qr_drawing(url: str, size: float = 18 * mm) -> Drawing:
    widget = QrCodeWidget(url)
    x1, y1, x2, y2 = widget.getBounds()
    width = x2 - x1
    height = y2 - y1
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    return drawing


def _header(styles: dict[str, ParagraphStyle]) -> Table:
    brand = Table(
        [
            [
                BrandMark(),
                _rich(
                    "IMTégrale<br/><font name='IMT-Regular' size='6.8' color='#627177'>"
                    "Suivi académique étudiant indépendant</font>",
                    styles["brand"],
                ),
            ]
        ],
        colWidths=[14 * mm, 72 * mm],
        hAlign="LEFT",
    )
    brand.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    title = Table(
        [
            [_rich("RELEVÉ ACADÉMIQUE<br/>PERSONNEL", styles["report_title"])],
            [
                _rich(
                    "<font name='IMT-Bold' size='6.5' color='#D9654F'>À TITRE INFORMATIF</font>",
                    styles["report_title"],
                )
            ],
        ],
        colWidths=[86 * mm],
        hAlign="RIGHT",
    )
    title.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    header = Table([[brand, title]], colWidths=[86 * mm, 86 * mm])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return header


def _identity_block(
    data: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    *,
    include_identity: bool,
) -> Table:
    identity = data["identity"]
    if include_identity:
        student_name = identity["official_name"] or identity["fallback_name"]
        identity_label = (
            "IDENTITÉ SYNCHRONISÉE DEPUIS PASS" if identity["official"] else "IDENTITÉ PASS NON DISPONIBLE"
        )
    else:
        student_name = "Identité masquée"
        identity_label = "VERSION ANONYMISÉE"
    left = [
        _paragraph(identity_label, styles["eyebrow"]),
        _paragraph(student_name, styles["student"]),
        _paragraph(_profile_label(data["profile"]), styles["identity_detail"]),
    ]
    right = [
        _paragraph("PÉRIMÈTRE DU RELEVÉ", styles["eyebrow"]),
        _rich(
            f"<font name='IMT-Bold' size='9.5' color='#17262B'>{escape(_scope_label(data['scope']))}</font>",
            styles["identity_detail"],
        ),
        _paragraph(
            f"Généré le {_format_datetime(data['generated_at'], data['timezone'])}",
            styles["identity_detail"],
        ),
    ]
    table = Table([[left, right]], colWidths=[105 * mm, 67 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBEFORE", (1, 0), (1, 0), 0.5, LINE),
            ]
        )
    )
    return table


def _metric_table(data: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    summary = data["summary"]
    metrics = [
        (
            "MOYENNE GÉNÉRALE",
            _format_number(summary["average"]),
            "/ 20",
            f"{_format_number(summary['average_credits'])} ECTS pondérés",
        ),
        (
            "GPA GLOBAL",
            _format_number(summary["gpa"]),
            "/ 4",
            f"{_format_number(summary['gpa_credits'])} ECTS pondérés",
        ),
        (
            "CRÉDITS ECTS",
            _format_number(summary["validated_credits"]),
            "obtenus",
            f"sur {_format_number(summary['attempted_credits'])} alloués",
        ),
        (
            "UNITÉS D'ENSEIGNEMENT",
            _format_number(summary["ue_count"]),
            "UE",
            f"{_format_number(summary['note_count'])} évaluations PASS",
        ),
    ]
    cells = []
    for label, value, suffix, detail in metrics:
        cells.append(
            [
                _paragraph(label, styles["metric_label"]),
                Spacer(1, 2),
                _rich(
                    f"{escape(value)} <font name='IMT-Regular' size='7' color='#627177'>"
                    f"{escape(suffix)}</font>",
                    styles["metric_value"],
                ),
                _paragraph(detail, styles["metric_detail"]),
            ]
        )
    table = Table([cells], colWidths=[43 * mm] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _semester_table(data: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            _paragraph("SEMESTRE", styles["table_header"]),
            _paragraph("MOYENNE", styles["table_header"]),
            _paragraph("GPA", styles["table_header"]),
            _paragraph("ECTS OBTENUS / ALLOUÉS", styles["table_header"]),
            _paragraph("UE", styles["table_header"]),
        ]
    ]
    for item in data["semesters"]:
        rows.append(
            [
                _paragraph(item["semester"], styles["table_bold"]),
                _paragraph(f"{_format_number(item['average'])} / 20", styles["table"]),
                _paragraph(f"{_format_number(item['gpa'])} / 4", styles["table"]),
                _paragraph(
                    f"{_format_number(item['validated_credits'])} / "
                    f"{_format_number(item['attempted_credits'])}",
                    styles["table"],
                ),
                _paragraph(item["ue_count"], styles["table"]),
            ]
        )
    table = Table(rows, colWidths=[29 * mm, 35 * mm, 30 * mm, 58 * mm, 20 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 1), (-1, -1), 6.5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 6.5),
            ]
        )
    )
    return table


def _provenance_block(data: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    timezone = data["timezone"]
    pass_date = _format_datetime(data["freshness"]["pass"], timezone)
    competences_date = _format_datetime(data["freshness"]["competences"], timezone)
    source_rows = [
        [
            _rich(f"<link href='{PASS_URL}' color='#0B5A58'><b>PASS</b></link>", styles["source"]),
            _paragraph(f"Évaluations synchronisées le {pass_date}", styles["source"]),
        ],
        [
            _rich(
                f"<link href='{COMPETENCES_URL}' color='#0B5A58'><b>COMPETENCES</b></link>", styles["source"]
            ),
            _paragraph(
                f"Intitulés, semestres, grades disponibles et ECTS synchronisés le {competences_date}",
                styles["source"],
            ),
        ],
        [
            _paragraph("IMTégrale", styles["table_bold"]),
            _paragraph(
                "Moyennes, GPA, regroupements et mise en page calculés depuis ces données.",
                styles["source"],
            ),
        ],
    ]
    source_table = Table(source_rows, colWidths=[31 * mm, 105 * mm])
    source_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.35, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    repo_link = _rich(
        f"<link href='{REPOSITORY_URL}' color='#0B5A58'><b>"
        "Consulter le code source sur GitHub</b></link><br/>"
        "<font name='IMT-Regular' size='6.4' color='#627177'>"
        "Le dépôt documente l'import et les calculs. Il rend le fonctionnement "
        "auditable sans certifier ce PDF.</font>",
        styles["link"],
    )
    left = [
        _paragraph("PROVENANCE ET TRANSPARENCE", styles["eyebrow"]),
        source_table,
        Spacer(1, 3),
        repo_link,
    ]
    block = Table([[left, _qr_drawing(REPOSITORY_URL)]], colWidths=[145 * mm, 27 * mm])
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BRAND_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, HexColor("#BFD8D1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return block


def _legal_block(styles: dict[str, ParagraphStyle]) -> Table:
    text = (
        "<b>Document personnel non officiel.</b> Ce relevé est fourni à titre informatif afin de faciliter "
        "la consultation et la présentation du parcours académique, notamment dans le cadre "
        "d'une candidature ou d'un entretien. Les évaluations PASS ainsi que les grades et "
        "crédits disponibles dans COMPETENCES proviennent des services académiques de "
        "l'établissement. Les moyennes, GPA et synthèses sont calculés par IMTégrale. "
        "Ce document n'est ni édité, ni certifié, ni validé par IMT Atlantique et ne "
        "remplace pas un relevé officiel."
    )
    table = Table([[_rich(text, styles["legal"])]], colWidths=[172 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _ue_table(data: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            _paragraph("UNITÉ D'ENSEIGNEMENT", styles["table_header"]),
            _paragraph("SEM.", styles["table_header"]),
            _paragraph("MOY.", styles["table_header"]),
            _paragraph("GRADE", styles["table_header"]),
            _paragraph("GPA", styles["table_header"]),
            _paragraph("ECTS", styles["table_header"]),
            _paragraph("ÉTAT", styles["table_header"]),
        ]
    ]
    row_styles: list[tuple[str, tuple[int, int], tuple[int, int], Any]] = []
    for index, ue in enumerate(data["ues"], start=1):
        title = ue["title"] or "Intitulé non disponible"
        code_detail = ue["official_code"] or ue["code"]
        ue_cell = _rich(
            f"<b>{escape(title)}</b><br/>"
            "<font name='IMT-Regular' size='6.1' color='#627177'>"
            f"{escape(code_detail)}</font>",
            styles["table"],
        )
        grade_source = "COMPETENCES" if ue["grade_source"] == "competences" else "CALCUL PASS"
        grade_cell = _rich(
            f"<b>{escape(ue['grade'] or '-')}</b><br/>"
            "<font name='IMT-Regular' size='5.3' color='#879398'>"
            f"{grade_source}</font>",
            styles["table"],
        )
        ects = (
            f"{_format_number(ue['earned_credits_ects'])} / {_format_number(ue['credits_ects'])}"
            if ue["earned_credits_ects"] is not None
            else _format_number(ue["credits_ects"])
        )
        status_label, status_color, status_background = _grade_status(ue)
        rows.append(
            [
                ue_cell,
                _paragraph(ue["semester"] or "-", styles["table"]),
                _paragraph(
                    f"{_format_number(ue['average'])} / 20" if ue["average"] is not None else "-",
                    styles["table"],
                ),
                grade_cell,
                _paragraph(_format_number(ue["gpa"]) if ue["gpa"] is not None else "-", styles["table"]),
                _paragraph(ects, styles["table"]),
                _paragraph(status_label, styles["table_muted"]),
            ]
        )
        row_styles.extend(
            [
                ("TEXTCOLOR", (6, index), (6, index), status_color),
                ("BACKGROUND", (6, index), (6, index), status_background),
            ]
        )
    table = Table(
        rows,
        colWidths=[63 * mm, 12 * mm, 20 * mm, 19 * mm, 13 * mm, 20 * mm, 25 * mm],
        repeatRows=1,
        splitByRow=1,
    )
    commands: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
    ]
    commands.extend(row_styles)
    table.setStyle(TableStyle(commands))
    return table


def _assessment_table(data: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            _paragraph("UE", styles["table_header"]),
            _paragraph("ÉVALUATION PASS", styles["table_header"]),
            _paragraph("NOTE", styles["table_header"]),
            _paragraph("COEFF.", styles["table_header"]),
            _paragraph("TYPE", styles["table_header"]),
            _paragraph("DÉTECTÉE", styles["table_header"]),
        ]
    ]
    notes_by_ue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in data["notes"]:
        notes_by_ue[note["ue_code"]].append(note)
    for ue in data["ues"]:
        for note in notes_by_ue.get(ue["code"], []):
            rows.append(
                [
                    _rich(
                        f"<b>{escape(ue['code'])}</b><br/>"
                        "<font name='IMT-Regular' size='5.7' color='#627177'>"
                        f"{escape(str(ue['semester'] or '-'))}</font>",
                        styles["table"],
                    ),
                    _paragraph(note["label"], styles["table"]),
                    _paragraph(f"{_format_number(note['score'])} / 20", styles["table_bold"]),
                    _paragraph(_format_number(note["coefficient"]), styles["table"]),
                    _paragraph("Rattrapage" if note["is_resit"] else "Classique", styles["table"]),
                    _paragraph(
                        _format_datetime(note["detected_at"], data["timezone"]).split(" à ", 1)[0],
                        styles["table"],
                    ),
                ]
            )
    table = Table(
        rows,
        colWidths=[23 * mm, 76 * mm, 22 * mm, 16 * mm, 19 * mm, 16 * mm],
        repeatRows=1,
        splitByRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (2, 1), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 1), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ]
        )
    )
    return table


def _filename(data: dict[str, Any], include_identity: bool) -> str:
    identity = data["identity"]
    name = identity["official_name"] or identity["fallback_name"] if include_identity else "anonyme"
    normalized = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower() or "etudiant"
    date = data["generated_at"].strftime("%Y-%m-%d")
    return f"releve-academique-{slug}-{date}.pdf"


def build_academic_report(
    db: Session,
    account: Account,
    *,
    semester: str = "all",
    include_assessments: bool = True,
    include_identity: bool = True,
) -> tuple[bytes, str]:
    _register_fonts()
    data = academic_report_snapshot(db, account, semester=semester)
    styles = _styles()
    generated_label = f"Généré le {_format_datetime(data['generated_at'], data['timezone'])}"
    buffer = io.BytesIO()
    document = AcademicReportDocTemplate(buffer, generated_label=generated_label)
    story: list[Flowable] = [
        _header(styles),
        Spacer(1, 8 * mm),
        _identity_block(data, styles, include_identity=include_identity),
        Spacer(1, 4 * mm),
        Table(
            [
                [
                    _rich(
                        "<b>Données institutionnelles synchronisées.</b> Les valeurs calculées "
                        "par IMTégrale sont identifiées dans le détail du relevé.",
                        styles["notice"],
                    )
                ]
            ],
            colWidths=[172 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), BRAND_SOFT),
                    ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#BFD8D1")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        ),
        Spacer(1, 5 * mm),
        _paragraph("Synthèse", styles["section"]),
        _metric_table(data, styles),
        Spacer(1, 5 * mm),
        _paragraph("Résultats par semestre", styles["section"]),
        _semester_table(data, styles),
        Spacer(1, 5 * mm),
        _provenance_block(data, styles),
        Spacer(1, 3 * mm),
        _legal_block(styles),
        PageBreak(),
        _paragraph("Unités d'enseignement", styles["section"]),
        _paragraph(
            "Les moyennes sont calculées à partir des évaluations PASS. Les grades marqués "
            "COMPETENCES sont importés tels quels ; un grade de calcul PASS est déduit de la "
            "moyenne lorsque le grade institutionnel n'est pas disponible.",
            styles["section_detail"],
        ),
        _ue_table(data, styles),
        Spacer(1, 4 * mm),
        KeepTogether(
            [
                HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=0, spaceAfter=6),
                _rich(
                    "<b>Lecture :</b> E correspond à une UE validée après rattrapage. FX et F "
                    "signalent une UE non validée et contribuent temporairement au GPA avec "
                    "0 point. Les ECTS affichent les crédits obtenus puis les crédits alloués "
                    "lorsqu'ils sont tous deux disponibles.",
                    styles["legal"],
                ),
            ]
        ),
    ]
    if include_assessments and data["notes"]:
        story.extend(
            [
                PageBreak(),
                _paragraph("Annexe - Évaluations PASS", styles["section"]),
                _paragraph(
                    "Cette annexe reprend les évaluations brutes synchronisées depuis PASS. "
                    "Pour une UE avec rattrapage, la dernière note de rattrapage est utilisée "
                    "comme moyenne de l'UE ; sinon les évaluations sont pondérées par leurs "
                    "coefficients.",
                    styles["section_detail"],
                ),
                _assessment_table(data, styles),
            ]
        )
    document.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue(), _filename(data, include_identity)
