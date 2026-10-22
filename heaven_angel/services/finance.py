"""Shared money definitions for the dashboard, Reports and the AI tools, so
every page reads profit the same way.

Cost of goods SOLD, not produced: a production batch is an investment in
stock, not an expense of the month it was made. Profit for a period is its
revenue minus what the goods that were actually sold or shipped cost to
make. Unit cost per SKU is the weighted average over every logged
production batch (SUM(total_cogs) / SUM(qty_produced)); a SKU that has never
been produced has no cost on record and counts as 0. Bulk is costed per mL,
the same unit its sales are in. Freebies count as cost with no revenue, and
Closed package orders count their contents (partner_inquiry_items).

"Cost of goods produced" (SUM of cogs_logs) is still shown as capital — what
has gone into making stock — but it is not what profit subtracts.
"""
from heaven_angel.db import query

# One row per SKU: its average production cost per unit.
UNIT_COST_SQL = """(SELECT sku, SUM(total_cogs) / NULLIF(SUM(qty_produced), 0) AS unit_cost
                    FROM cogs_logs GROUP BY sku)"""

# A package order is dated by when it was closed, falling back to when the
# inquiry came in for rows that predate partner_inquiries.closed_at.
PACKAGE_DATE_SQL = "COALESCE(pi.closed_at, pi.created_at)"

SALES_COGS_SQL = f"""SELECT COALESCE(SUM(s.qty_sold * COALESCE(uc.unit_cost, 0)), 0) AS v
                     FROM sales s LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = s.sku"""

PACKAGE_COGS_SQL = f"""SELECT COALESCE(SUM(pii.qty * COALESCE(uc.unit_cost, 0)), 0) AS v
                       FROM partner_inquiry_items pii
                       JOIN partner_inquiries pi ON pi.inquiry_id = pii.inquiry_id
                       LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = pii.sku
                       WHERE pi.status = 'Closed'"""


def cogs_sold(sales_where="1=1", package_where="1=1", params=()):
    """Cost of goods sold = cost of register sales (alias s) + cost of Closed
    package orders (alias pi), each filtered by its own WHERE fragment. The
    same params feed both fragments, in that order."""
    sales = query(f"{SALES_COGS_SQL} WHERE {sales_where}", tuple(params), fetchone=True)["v"]
    packages = query(f"{PACKAGE_COGS_SQL} AND {package_where}", tuple(params), fetchone=True)["v"]
    return float(sales) + float(packages)
