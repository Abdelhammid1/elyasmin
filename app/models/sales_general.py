"""PHASE 36 (SALES-1): general sales invoice models.

Lives in its own file so the milk-specific `sales.py` stays
undisturbed and its imports (Customer + MilkInvoice +
PaymentAllocation) remain the single source of truth for the
dairy pipeline. This module is the closest analogue of
`suppliers.py::PurchaseInvoice` on the sales side.

Three tables:

  sales_invoices                    — the invoice header
  sales_invoice_lines               — mixed cow / inventory / free
                                      line items
  sales_invoice_payment_allocations — mirror of PaymentAllocation
                                      pinned to sales_invoices.id

The auto-posting service `on_sales_invoice` in
`app/services/autoposting.py` posts one atomic JE per invoice
tagged `source_type="SalesInvoice"` — the ledger side. The
allocation table is the display/reporting side that ties a
CustomerPayment to a SalesInvoice (same "the ledger already
knows; this is a UI layer" contract as PaymentAllocation).
"""
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class SalesInvoice(db.Model):
    """A general sales invoice — cow lines, inventory lines, and
    free-text lines can be mixed on the same invoice. Payment can
    be full cash, full credit, or a split.

    STATUS_DRAFT     — user is still editing; no JE posted yet
    STATUS_ISSUED    — locked; auto-posted; further edits go
                       through the delete-and-recreate flow

    PAYMENT_CASH     — cash_amount == grand_total, credit == 0
                       (requires treasury_account_id)
    PAYMENT_CREDIT   — cash == 0, credit == grand_total
    PAYMENT_MIXED    — cash + credit == grand_total, both > 0

    `customer_id` is nullable for walk-in cash sales (a name is
    captured in `walkin_name` instead). The receivable JE line
    only fires when there IS a customer_id (credit portion of a
    walk-in is disallowed by the form).
    """

    __tablename__ = "sales_invoices"

    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"

    PAYMENT_CASH = "cash"
    PAYMENT_CREDIT = "credit"
    PAYMENT_MIXED = "mixed"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"),
        nullable=True, index=True,
    )
    # For walk-in sales when customer_id is NULL. Free text; if
    # the client later wants walk-ins promoted to real Customer
    # rows, that's a one-route follow-up.
    walkin_name = db.Column(db.String(120), nullable=True)
    invoice_number = db.Column(db.String(40), nullable=True, index=True)
    invoice_date = db.Column(db.Date, nullable=False,
                             default=date.today, index=True)
    due_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False,
                       default=STATUS_DRAFT)

    payment_type = db.Column(db.String(10), nullable=False,
                             default=PAYMENT_CASH)
    treasury_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"),
        nullable=True,
    )
    cash_amount = db.Column(db.Numeric(14, 2), nullable=False,
                            default=Decimal("0"))
    credit_amount = db.Column(db.Numeric(14, 2), nullable=False,
                              default=Decimal("0"))
    grand_total = db.Column(db.Numeric(14, 2), nullable=False,
                            default=Decimal("0"))

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False,
                            default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    created_by_id = db.Column(db.Integer,
                              db.ForeignKey("users.id"),
                              nullable=True)

    customer = db.relationship("Customer")
    treasury = db.relationship("TreasuryAccount")
    lines = db.relationship(
        "SalesInvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="SalesInvoiceLine.sort_order",
    )
    allocations = db.relationship(
        "SalesInvoicePaymentAllocation",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    # ---- Display helpers copied from MilkInvoice contract ----

    @property
    def status_label(self) -> str:
        return "مسوّدة" if self.status == self.STATUS_DRAFT else "صادرة"

    @property
    def payment_type_label(self) -> str:
        return {
            self.PAYMENT_CASH: "كاش",
            self.PAYMENT_CREDIT: "آجل",
            self.PAYMENT_MIXED: "كاش + آجل",
        }.get(self.payment_type, self.payment_type)

    @property
    def counterparty_label(self) -> str:
        """Customer name when there is one, walk-in tag otherwise."""
        if self.customer_id and self.customer:
            return self.customer.name
        return self.walkin_name or "عميل نقدي"

    def recompute_total(self) -> None:
        """Sum all line totals into grand_total."""
        self.grand_total = sum(
            (Decimal(str(l.line_total or 0)) for l in self.lines),
            Decimal("0"),
        )

    # ---- Payment status derivation ----

    @property
    def paid_amount(self) -> Decimal:
        """SALES-2 (PHASE 42): sum of allocations, plus a walk-in
        edge case.

        Before the fix, this was `cash_amount + SUM(allocations)`
        — but for a real-customer invoice with a cash portion,
        `create_invoice` already writes a
        `SalesInvoicePaymentAllocation` for that cash amount
        (that's how the "الجزء المدفوع" bar renders). Adding
        `cash_amount` on top then double-counted the cash portion:
        e.g. cash=40k + credit=55k = 95k total showed
        paid=80k and outstanding=15k instead of paid=40k,
        outstanding=55k.

        Cases now:
          * Real customer, any split — the issue-time cash
            portion lives inside `allocations`; later credit
            collections add more allocations. Sum is correct.
          * Walk-in (customer_id is NULL) — `create_invoice`
            does NOT create a CustomerPayment / allocation
            (there's no Customer to link to), so we still need
            to count `cash_amount` here for the paid figure to
            land on the full price of a walk-in cash sale.

        The old data self-corrects: pre-fix invoices with a
        real customer already have the allocation row, so the
        new formula reads `paid = allocation` = exactly what
        was collected. No data-migration script needed.
        """
        alloc = sum(
            (Decimal(str(a.amount or 0)) for a in self.allocations),
            Decimal("0"),
        )
        if self.customer_id is None:
            # Walk-in: cash went in, no CustomerPayment/allocation
            # exists to represent it, so the raw cash_amount is
            # the paid figure.
            alloc += Decimal(str(self.cash_amount or 0))
        return alloc.quantize(Decimal("0.01"))

    @property
    def outstanding_amount(self) -> Decimal:
        total = Decimal(str(self.grand_total or 0))
        result = total - self.paid_amount
        if result < 0:
            result = Decimal("0")
        return result.quantize(Decimal("0.01"))

    @property
    def payment_status(self) -> str:
        """'paid' | 'partial' | 'unpaid'. Draft invoices always
        read as 'unpaid' since nothing is locked in yet."""
        if self.status != self.STATUS_ISSUED:
            return "unpaid"
        total = Decimal(str(self.grand_total or 0))
        if total <= 0:
            return "paid"
        paid = self.paid_amount
        if paid >= total - Decimal("0.005"):
            return "paid"
        if paid > 0:
            return "partial"
        return "unpaid"


class SalesInvoiceLine(db.Model):
    """One line item on a SalesInvoice. Polymorphic on
    `line_kind`:

      KIND_COW       — cow_id set; qty=1; unit_price is the sale
                       price; cow_book_value_snapshot captures
                       cow.current_value at issue time for the
                       JE's 1400 close-out.
      KIND_INVENTORY — ingredient_id set; qty and unit_price
                       typed; inv_avg_cost_snapshot captures the
                       ingredient's avg_cost at issue time for
                       the JE's inventory draw-down (so a later
                       purchase that changes avg_cost can't
                       retroactively skew the cost side).
      KIND_FREE      — description free text; no FK; qty and
                       unit_price typed; credits 4090 عائد أخرى.

    UniqueConstraint on cow_id enforces "a cow can only be sold
    once at the DB level" — even across historical AnimalSale
    rows the app code guards against re-selling a STATUS_SOLD
    cow at request time, but the constraint is the belt-and-
    braces backup.
    """

    __tablename__ = "sales_invoice_lines"

    KIND_COW = "cow"
    KIND_INVENTORY = "inventory"
    KIND_FREE = "free"

    # SALES-3 (PHASE 43): pricing mode for cow lines. per_head is
    # the default and matches every existing row; per_kg says
    # `line_total = weight_kg * price_per_kg`. Only meaningful
    # when line_kind == KIND_COW — for the other kinds the value
    # stays "per_head" and the weight columns are NULL.
    PRICING_PER_HEAD = "per_head"
    PRICING_PER_KG = "per_kg"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("sales_invoices.id"),
        nullable=False, index=True,
    )
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    line_kind = db.Column(db.String(10), nullable=False)

    cow_id = db.Column(
        db.Integer, db.ForeignKey("cows.id"),
        nullable=True,
    )
    ingredient_id = db.Column(
        db.Integer, db.ForeignKey("ingredients.id"),
        nullable=True,
    )
    description = db.Column(db.String(255), nullable=True)

    qty = db.Column(db.Numeric(14, 3), nullable=False,
                    default=Decimal("1"))
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_total = db.Column(db.Numeric(14, 2), nullable=False)

    # SALES-3 (PHASE 43): weight-based pricing for cow lines.
    # NULL for per_head lines and for every non-cow kind.
    pricing_mode = db.Column(
        db.String(10), nullable=False, default=PRICING_PER_HEAD,
        server_default="per_head",
    )
    weight_kg = db.Column(db.Numeric(10, 3), nullable=True)
    price_per_kg = db.Column(db.Numeric(10, 2), nullable=True)

    cow_book_value_snapshot = db.Column(db.Numeric(12, 2),
                                        nullable=True)
    inv_avg_cost_snapshot = db.Column(db.Numeric(12, 4),
                                      nullable=True)

    invoice = db.relationship("SalesInvoice", back_populates="lines")
    cow = db.relationship("Cow")
    ingredient = db.relationship("Ingredient")

    __table_args__ = (
        db.UniqueConstraint("cow_id", name="uq_sil_cow"),
    )

    @property
    def kind_label(self) -> str:
        return {
            self.KIND_COW: "بقرة",
            self.KIND_INVENTORY: "صنف مخزون",
            self.KIND_FREE: "صنف حر",
        }.get(self.line_kind, self.line_kind)

    @property
    def display_name(self) -> str:
        """What to show in the invoice line list. Falls back to
        the free-text description for the KIND_FREE case."""
        if self.line_kind == self.KIND_COW and self.cow:
            return f"بقرة {self.cow.ear_tag}"
        if self.line_kind == self.KIND_INVENTORY and self.ingredient:
            return self.ingredient.name
        return self.description or "—"

    @property
    def pricing_label(self) -> str:
        """SALES-3 (PHASE 43): Arabic label for the pricing mode
        on a cow line. Empty for non-cow lines (irrelevant)."""
        if self.line_kind != self.KIND_COW:
            return ""
        return {
            self.PRICING_PER_HEAD: "بالرأس",
            self.PRICING_PER_KG: "بالوزن",
        }.get(self.pricing_mode, self.pricing_mode)


class SalesInvoicePaymentAllocation(db.Model):
    """PHASE 36 (SALES-1): mirror of PaymentAllocation, pinned
    to sales_invoices.

    Same invariants (enforced by the allocation service in
    `app/services/allocations.py`, NOT the DB):
      SUM(amount for a payment)  <= payment.amount
      SUM(amount for an invoice) <= invoice.grand_total

    v1 only writes here at issue time for the cash portion of an
    invoice (so the "الجزء المدفوع" bar renders correctly).
    Follow-up payments against a credit portion are Deferred —
    see `allocate_sales_invoice_payment` for the flow when it
    ships.
    """

    __tablename__ = "sales_invoice_payment_allocations"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer, db.ForeignKey("customer_payments.id"),
        nullable=False, index=True,
    )
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("sales_invoices.id"),
        nullable=False, index=True,
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    created_by_id = db.Column(db.Integer,
                              db.ForeignKey("users.id"),
                              nullable=True)

    payment = db.relationship("CustomerPayment")
    invoice = db.relationship("SalesInvoice",
                              back_populates="allocations")

    __table_args__ = (
        db.UniqueConstraint("payment_id", "invoice_id",
                            name="uq_sipa_payment_invoice"),
    )
