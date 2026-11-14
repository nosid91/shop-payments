"""Payments."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cart_id", sa.Uuid, sa.ForeignKey("carts.id"), nullable=False),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "payment_method_id",
            sa.Uuid,
            sa.ForeignKey("user_payment_methods.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("failure_code", sa.Text),
        sa.Column("provider_charge_id", sa.Text),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_payments_status"),
        sa.CheckConstraint("(status = 'failed') = (failure_code IS NOT NULL)", name="ck_payments_failure_code"),
        sa.CheckConstraint(
            "(status = 'succeeded') = (provider_charge_id IS NOT NULL)",
            name="ck_payments_provider_charge_id",
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_payments_user_idempotency_key"),
    )
    op.create_index(
        index_name="uq_payments_cart_active",
        table_name="payments",
        columns=["cart_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'succeeded')"),
    )


def downgrade() -> None:
    op.drop_table("payments")
