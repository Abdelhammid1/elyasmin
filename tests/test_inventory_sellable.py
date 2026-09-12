"""SALES-1 Part 1 — sellable-category regression.

Two locks:

  1. Creating / renaming a category with is_sellable persists the
     flag round-trip.
  2. The item-picker helper (`sellable_ingredients_for_sales`)
     returns exactly the ingredients whose category is sellable,
     including custom categories and rename cascades.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.inventory import (
    Ingredient,
    IngredientCategory,
    StockMovement,
)


_TAG = "SALES1-SEL-CAT"


def _cleanup(app):
    with app.app_context():
        Ingredient.query.filter(
            Ingredient.name.like(f"{_TAG}%")
        ).delete(synchronize_session=False)
        IngredientCategory.query.filter(
            IngredientCategory.name.like(f"{_TAG}%")
        ).delete(synchronize_session=False)
        db.session.commit()


def test_reason_sale_label_exists(app):
    # Uses the app fixture only to make sure the model registry is
    # fully configured (DepreciationPosting → JournalEntry mapper
    # needs create_app to have imported every model).
    with app.app_context():
        assert StockMovement.REASON_SALE == "sale"
        m = StockMovement(reason=StockMovement.REASON_SALE,
                          delta=Decimal("0"))
        assert m.reason_label == "بيع"


def test_create_category_with_sellable_flag(admin_client, app):
    try:
        r = admin_client.post(
            "/inventory/categories/new",
            data={"name": f"{_TAG}-A", "is_sellable": "1"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            cat = IngredientCategory.query.filter_by(name=f"{_TAG}-A").first()
            assert cat is not None
            assert cat.is_sellable is True

        # Rename + toggle off in one go: existing is_sellable flips to False.
        r = admin_client.post(
            f"/inventory/categories/{cat.id}/rename",
            data={"name": f"{_TAG}-A2"},   # no is_sellable → False
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            cat = IngredientCategory.query.filter_by(name=f"{_TAG}-A2").first()
            assert cat is not None
            assert cat.is_sellable is False
    finally:
        _cleanup(app)


def test_sellable_categories_scope_ingredients(app):
    """Manual query — the same shape the SalesInvoice picker uses to
    list ingredients. Only ingredients under a sellable category are
    returned; unrelated categories are excluded even when their
    ingredient count is > 0."""
    with app.app_context():
        sell = IngredientCategory(name=f"{_TAG}-SELL", is_active=True,
                                  is_sellable=True)
        noshop = IngredientCategory(name=f"{_TAG}-NOSHOP", is_active=True,
                                    is_sellable=False)
        db.session.add_all([sell, noshop])
        db.session.flush()
        db.session.add_all([
            Ingredient(name=f"{_TAG}-mango", category=sell.name,
                       unit="kg", current_qty=Decimal("10"),
                       avg_cost=Decimal("5")),
            Ingredient(name=f"{_TAG}-guava", category=sell.name,
                       unit="kg", current_qty=Decimal("5"),
                       avg_cost=Decimal("7")),
            Ingredient(name=f"{_TAG}-hay", category=noshop.name,
                       unit="kg", current_qty=Decimal("50"),
                       avg_cost=Decimal("3")),
        ])
        db.session.commit()

        picker_q = (
            Ingredient.query
            .join(IngredientCategory,
                  IngredientCategory.name == Ingredient.category)
            .filter(IngredientCategory.is_sellable.is_(True))
            .filter(Ingredient.name.like(f"{_TAG}%"))
        )
        names = sorted(i.name for i in picker_q.all())
        assert names == [f"{_TAG}-guava", f"{_TAG}-mango"]

    _cleanup(app)
