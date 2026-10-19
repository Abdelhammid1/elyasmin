"""VET-1: vet records baseline — vaccinations, diseases, treatments.

The client's other program hasn't been shown yet, so this is the
skeleton: editable lists of vaccines and diseases, and one record per
cow per entry. A medicine used from stock goes through the normal
medicine dispense (stock, lots, ledger) and is linked here.
"""
from datetime import date, datetime

from app.extensions import db


class VetCatalogItem(db.Model):
    __tablename__ = "vet_catalog_items"
    __table_args__ = (db.UniqueConstraint("kind", "name", name="uq_vet_catalog_kind_name"),)

    KIND_VACCINE = "vaccine"
    KIND_DISEASE = "disease"
    KIND_LABELS = {KIND_VACCINE: "تحصين", KIND_DISEASE: "مرض"}

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # Vaccines: days to the next dose. NULL = one-off.
    repeat_days = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def kind_label(self) -> str:
        return self.KIND_LABELS.get(self.kind, self.kind)


def vet_items(kind: str, include_id: int | None = None) -> list[VetCatalogItem]:
    """Active items of a kind (plus `include_id`, so editing a record
    whose item was disabled since still validates)."""
    q = VetCatalogItem.query.filter(VetCatalogItem.kind == kind)
    if include_id:
        q = q.filter(db.or_(VetCatalogItem.is_active.is_(True), VetCatalogItem.id == include_id))
    else:
        q = q.filter(VetCatalogItem.is_active.is_(True))
    return q.order_by(VetCatalogItem.name).all()


class VetRecord(db.Model):
    __tablename__ = "vet_records"

    KIND_VACCINATION = "vaccination"
    KIND_DISEASE = "disease"
    KIND_TREATMENT = "treatment"
    KIND_LABELS = {
        KIND_VACCINATION: "تحصين",
        KIND_DISEASE: "تشخيص مرض",
        KIND_TREATMENT: "علاج",
    }
    # Which catalog list a record kind picks from.
    CATALOG_KIND = {
        KIND_VACCINATION: VetCatalogItem.KIND_VACCINE,
        KIND_DISEASE: VetCatalogItem.KIND_DISEASE,
        KIND_TREATMENT: VetCatalogItem.KIND_DISEASE,   # the disease treated
    }

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("vet_catalog_items.id"), nullable=True, index=True)
    record_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    # Vaccinations only: when the next dose is due.
    next_due = db.Column(db.Date, nullable=True, index=True)
    dispense_id = db.Column(db.Integer, db.ForeignKey("medicine_dispenses.id"),
                            nullable=True, index=True)
    # Shared by every record of one group vaccination.
    batch = db.Column(db.String(32), nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    cow = db.relationship("Cow")
    item = db.relationship("VetCatalogItem")
    dispense = db.relationship("MedicineDispense")

    @property
    def kind_label(self) -> str:
        return self.KIND_LABELS.get(self.kind, self.kind)
