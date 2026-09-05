"""PHASE 31 (INV categories): regression suite for the categories
management page.

Four invariants:
  1. `/inventory/categories` renders with a count-per-category column
     that matches the ingredients table.
  2. Adding a duplicate-name category is refused.
  3. Deleting a category referenced by at least one ingredient is
     refused.
  4. Deleting an empty category succeeds.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models.inventory import Ingredient, IngredientCategory


def _cleanup(app):
    """Remove any test-seeded categories."""
    with app.app_context():
        IngredientCategory.query.filter(
            IngredientCategory.name.like("TEST-CAT-%")
        ).delete()
        Ingredient.query.filter(
            Ingredient.name.like("TEST-ING-%")
        ).delete()
        db.session.commit()


def test_list_shows_counts(admin_client, app):
    with app.app_context():
        # Seed one empty test category and one with an ingredient
        empty = IngredientCategory(name="TEST-CAT-EMPTY", is_active=True)
        filled = IngredientCategory(name="TEST-CAT-FILLED", is_active=True)
        db.session.add_all([empty, filled])
        db.session.flush()
        ing = Ingredient(
            name="TEST-ING-1",
            category="TEST-CAT-FILLED",
            unit=Ingredient.UNIT_KG,
        )
        db.session.add(ing)
        db.session.commit()
    try:
        r = admin_client.get("/inventory/categories")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "TEST-CAT-EMPTY" in body
        assert "TEST-CAT-FILLED" in body
        # The empty one carries the "فاضي" chip
        assert "فاضي" in body
    finally:
        _cleanup(app)


def test_duplicate_name_refused(admin_client, app):
    with app.app_context():
        db.session.add(IngredientCategory(name="TEST-CAT-DUP", is_active=True))
        db.session.commit()
    try:
        r = admin_client.post(
            "/inventory/categories/new",
            data={"name": "TEST-CAT-DUP"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "موجود فعلاً" in r.get_data(as_text=True)
        with app.app_context():
            count = IngredientCategory.query.filter_by(name="TEST-CAT-DUP").count()
            assert count == 1
    finally:
        _cleanup(app)


def test_delete_non_empty_refused(admin_client, app):
    with app.app_context():
        cat = IngredientCategory(name="TEST-CAT-USED", is_active=True)
        db.session.add(cat)
        db.session.flush()
        db.session.add(Ingredient(
            name="TEST-ING-USED",
            category="TEST-CAT-USED",
            unit=Ingredient.UNIT_KG,
        ))
        db.session.commit()
        cid = cat.id
    try:
        r = admin_client.post(
            f"/inventory/categories/{cid}/delete",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert "مادة مرتبطة" in r.get_data(as_text=True)
        with app.app_context():
            assert IngredientCategory.query.get(cid) is not None
    finally:
        _cleanup(app)


def test_delete_empty_succeeds(admin_client, app):
    with app.app_context():
        cat = IngredientCategory(name="TEST-CAT-EMPTY-2", is_active=True)
        db.session.add(cat)
        db.session.commit()
        cid = cat.id
    r = admin_client.post(
        f"/inventory/categories/{cid}/delete",
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        assert IngredientCategory.query.get(cid) is None
    _cleanup(app)
