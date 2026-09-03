#!/usr/bin/env python
"""PHASE 10 (YAS-BR-3): audit + archive test data.

Zakaria's meeting flagged that test names like 'مارتوش' were still
visible in the system. This script scans the customer / supplier /
worker / ingredient tables for records whose name matches a set of
test patterns, and reports what's linked to each so the operator can
decide safely.

Usage:
    # DRY-RUN (default): print a report, change nothing.
    python scripts/audit_test_data.py

    # Archive rows that are demonstrably safe (no linked activity).
    python scripts/audit_test_data.py --apply

    # Also archive rows with linked activity — dangerous, requires
    # --force alongside --apply.
    python scripts/audit_test_data.py --apply --force

Env:
    DATABASE_URL  — same var the app reads; on production this points at
                    the PG instance. Never runs against a URL you didn't set.
    FLASK_ENV     — set to 'production' when running on live data.

Every "archive" here is a soft delete (is_archived=True). Nothing is
DELETEd from disk — the audit log stays intact.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# ensure the app package can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_PATTERNS = ["%تست%", "%test%", "%demo%", "%مارتوش%", "%example%",
                 "%dummy%", "%sample%"]


def _match_any(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(
        p.strip("%").lower() in n for p in TEST_PATTERNS
    )


def _count_customer_links(customer):
    """How many rows link back to this customer? Any = manual review."""
    from app.models.sales import MilkDelivery, MilkInvoice, CustomerPayment
    return {
        "deliveries": customer.deliveries.count(),
        "invoices":   MilkInvoice.query.filter_by(customer_id=customer.id).count(),
        "payments":   customer.payments.count(),
    }


def _count_supplier_links(supplier):
    from app.models.suppliers import PurchaseInvoice, SupplierPayment
    return {
        "invoices": PurchaseInvoice.query.filter_by(supplier_id=supplier.id).count(),
        "payments": SupplierPayment.query.filter_by(supplier_id=supplier.id).count(),
    }


def _count_worker_links(worker):
    from app.models.labor import Attendance, WorkerPayment
    return {
        "attendance": Attendance.query.filter_by(worker_id=worker.id).count(),
        "payments":   WorkerPayment.query.filter_by(worker_id=worker.id).count(),
    }


def _count_ingredient_links(ing):
    from app.models.inventory import StockMovement
    return {
        "movements": StockMovement.query.filter_by(ingredient_id=ing.id).count(),
    }


def scan(app):
    """Walk every candidate model, return a list of finding dicts."""
    findings = []
    with app.app_context():
        from app.models.sales import Customer
        from app.models.suppliers import Supplier
        from app.models.labor import Worker
        from app.models.inventory import Ingredient

        for c in Customer.query.filter_by(is_archived=False).all():
            if _match_any(c.name):
                links = _count_customer_links(c)
                safe = sum(links.values()) == 0
                findings.append({
                    "model": "Customer", "id": c.id, "name": c.name,
                    "obj": c, "links": links, "safe": safe,
                })

        for s in Supplier.query.filter_by(is_archived=False).all():
            if _match_any(s.name):
                links = _count_supplier_links(s)
                safe = sum(links.values()) == 0
                findings.append({
                    "model": "Supplier", "id": s.id, "name": s.name,
                    "obj": s, "links": links, "safe": safe,
                })

        for w in Worker.query.filter_by(is_archived=False).all():
            if _match_any(w.name):
                links = _count_worker_links(w)
                safe = sum(links.values()) == 0
                findings.append({
                    "model": "Worker", "id": w.id, "name": w.name,
                    "obj": w, "links": links, "safe": safe,
                })

        for ing in Ingredient.query.filter_by(is_archived=False).all():
            if _match_any(ing.name):
                links = _count_ingredient_links(ing)
                safe = sum(links.values()) == 0
                findings.append({
                    "model": "Ingredient", "id": ing.id, "name": ing.name,
                    "obj": ing, "links": links, "safe": safe,
                })

    return findings


def print_report(findings):
    print()
    print(f"=== TEST-DATA AUDIT — {len(findings)} candidate(s) ===")
    if not findings:
        print("  ✓ Nothing matches the test patterns. DB is clean.")
        return
    for f in findings:
        marker = "✓ safe" if f["safe"] else "⚠ manual review"
        links_desc = ", ".join(f"{k}={v}" for k, v in f["links"].items())
        print(f"  [{marker:20s}] {f['model']:12s} #{f['id']:<4} {f['name']!r}")
        print(f"                        links: {links_desc}")


def apply_archives(app, findings, force: bool):
    from app.extensions import db
    archived = []
    with app.app_context():
        for f in findings:
            if not f["safe"] and not force:
                continue
            # Reload the object in this session
            obj = f["obj"].__class__.query.get(f["id"])
            if obj is None:
                continue
            obj.is_archived = True
            archived.append({
                "model": f["model"], "id": f["id"], "name": f["name"],
                "was_safe": f["safe"], "archived_at": datetime.utcnow().isoformat(),
            })
        db.session.commit()

    if archived:
        print()
        print(f"=== ARCHIVED {len(archived)} row(s) ===")
        for a in archived:
            tag = "safe" if a["was_safe"] else "FORCED"
            print(f"  [{tag:8s}] {a['model']:12s} #{a['id']:<4} {a['name']}")
        # Write to a log file the operator can review later
        log_path = os.path.join(os.getcwd(), "test_data_cleanup.log")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n--- cleanup at {datetime.utcnow().isoformat()} ---\n")
            for a in archived:
                fh.write(f"  [{'safe' if a['was_safe'] else 'FORCED'}] "
                         f"{a['model']} #{a['id']} {a['name']}\n")
        print(f"\nLog written to: {log_path}")
    else:
        print()
        print("=== nothing archived ===")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--apply", action="store_true",
                        help="Actually archive the safe rows.")
    parser.add_argument("--force", action="store_true",
                        help="Also archive rows with linked activity. Needs --apply.")
    args = parser.parse_args()

    if args.force and not args.apply:
        parser.error("--force needs --apply")

    print(f"DATABASE_URL = {os.getenv('DATABASE_URL', '(default sqlite)')}")
    print(f"FLASK_ENV    = {os.getenv('FLASK_ENV', 'development')}")

    from app import create_app
    app = create_app(os.getenv("FLASK_ENV", "development"))

    findings = scan(app)
    print_report(findings)

    if args.apply and findings:
        apply_archives(app, findings, force=args.force)
    elif findings:
        print()
        print("Dry-run — pass --apply to archive the safe rows.")


if __name__ == "__main__":
    main()
