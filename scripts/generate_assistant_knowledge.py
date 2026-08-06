#!/usr/bin/env python
"""Generate app/assistant_knowledge.md from the actual code.

Run after any change to a blueprint or form that alters real user-facing
behaviour:

    python scripts/generate_assistant_knowledge.py

The output MUST be reviewed by a human before it goes to production — see the
checklist in DEPLOY.md. The script pulls two things per blueprint:

  * every flash() string — these are the system's real business rules, already
    written in the Arabic the user sees
  * every route docstring

WHY THIS MATTERS FOR SAFETY: the assistant is only ever given this file. The
system prompt telling the model to refuse questions about secrets is helpful but
is not a security boundary — a prompt can be talked around. This file not
containing secrets is the actual control, which is why the script hard-fails
rather than warns when a forbidden string appears in the output.
"""
import ast
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "app" / "assistant_knowledge.md"

# If any of these appears in the generated text, refuse to write the file.
FORBIDDEN_PATTERNS = [
    ".env",
    "SECRET_KEY",
    "DEEPSEEK_API_KEY",
    "password_hash",
    "DATABASE_URL",
    "api_key",
]

BLUEPRINTS_TO_SCAN = [
    "herd", "inventory", "suppliers", "purchases", "feed",
    "medicine", "customers", "milk", "finance", "labor", "accounts",
]

BLUEPRINT_LABELS = {
    "herd": "القطيع والأبقار",
    "inventory": "المخزون",
    "suppliers": "الموردون",
    "purchases": "فواتير الشراء",
    "feed": "العلف والخزانات",
    "medicine": "الأدوية",
    "customers": "العملاء",
    "milk": "اللبن والتوريدات",
    "finance": "المالية والتقارير",
    "labor": "العمالة",
    "accounts": "الخزنة والحسابات",
}


def extract_flash_messages(filepath: Path) -> list[str]:
    """Every flash() string — the app's real rules in the user's own Arabic."""
    content = filepath.read_text(encoding="utf-8")
    raw = re.findall(r'flash\(\s*f?["\']([^"\']+)["\']', content)
    # Drop fragments that are pure interpolation or too short to mean anything
    return [m.strip() for m in raw if len(m.strip()) > 8]


def extract_route_docstrings(filepath: Path) -> dict[str, str]:
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc and not node.name.startswith("_"):
                out[node.name] = " ".join(doc.split())
    return out


def build_section(blueprint_name: str) -> str:
    routes_file = ROOT / "app" / "blueprints" / blueprint_name / "routes.py"
    if not routes_file.exists():
        return ""

    flashes = extract_flash_messages(routes_file)
    docstrings = extract_route_docstrings(routes_file)
    label = BLUEPRINT_LABELS.get(blueprint_name, blueprint_name)

    lines = [f"## {label} ({blueprint_name})\n"]
    if docstrings:
        lines.append("### الوظائف المتاحة")
        for name, doc in docstrings.items():
            lines.append(f"- **{name}**: {doc}")
        lines.append("")
    if flashes:
        lines.append("### رسائل وقواعد فعلية من النظام")
        for f in sorted(set(flashes)):
            lines.append(f"- {f}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    sections = [
        "<!-- تم التوليد تلقائيًا في "
        f"{datetime.now():%Y-%m-%d %H:%M}"
        " بواسطة scripts/generate_assistant_knowledge.py -->",
        "<!-- راجعه يدويًا قبل الاستخدام — القسم الأساسي تحت مكتوب باليد ولا يُولَّد -->",
        "",
        "# معرفة المساعد — نظام مزرعة الياسمين",
        "",
    ]

    # Preserve the hand-written base section across regenerations, so the manual
    # curation the ticket requires is not wiped out every time this runs.
    marker = "<!-- BEGIN MANUAL SECTION -->"
    end_marker = "<!-- END MANUAL SECTION -->"
    manual = ""
    if OUTPUT.exists():
        old = OUTPUT.read_text(encoding="utf-8")
        if marker in old and end_marker in old:
            manual = old[old.index(marker): old.index(end_marker) + len(end_marker)]
    if not manual:
        manual = (
            f"{marker}\n"
            "## أساسيات النظام\n\n"
            "_(اكتب هنا يدويًا من TOPICS في app/blueprints/help/routes.py — "
            "السكريبت مش بيمس القسم ده)_\n"
            f"{end_marker}"
        )
    sections.append(manual)
    sections.append("")

    for bp_name in BLUEPRINTS_TO_SCAN:
        section = build_section(bp_name)
        if section:
            sections.append(section)

    output_text = "\n".join(sections)

    lowered = output_text.lower()
    for forbidden in FORBIDDEN_PATTERNS:
        if forbidden.lower() in lowered:
            print(
                f"توقف: لقيت كلمة ممنوعة '{forbidden}' في الناتج.\n"
                "الملف ده بيتبعت للموديل، فممنوع يكون فيه أي أسرار.\n"
                "راجع الكود يدوي وشيل السطر ده قبل ما تشغل السكريبت تاني.",
                file=sys.stderr,
            )
            return 1

    OUTPUT.write_text(output_text, encoding="utf-8")
    print(f"اتكتب: {OUTPUT}")
    print(f"  الأقسام: {len([s for s in sections if s.startswith('## ')])}")
    print("لازم تراجعه يدويًا قبل ما يتفعل في الإنتاج (شوف DEPLOY.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
