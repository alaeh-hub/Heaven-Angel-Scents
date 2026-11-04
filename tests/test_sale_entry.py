"""Form validation shared by HQ's and the branches' Record Sale pages
(services/sale_entry.py). Pure parsing — no database needed."""
import decimal

import pytest

from heaven_angel.services.sale_entry import SaleFormError, parse_sale_form

KW = dict(qty_label="Quantity", sale_type_prompt="pick a type", product_prompt="pick a product")


def _form(**over):
    base = {"sku": "A1-50ML", "sale_type": "Sale", "payment_method": "Cash",
            "qty_sold": "2", "unit_price": "150.50"}
    base.update(over)
    return base


def test_valid_cash_sale():
    sale = parse_sale_form(_form(), **KW)
    assert (sale.sku, sale.qty, sale.sale_type, sale.payment_method) == ("A1-50ML", 2, "Sale", "Cash")
    assert sale.unit_price == decimal.Decimal("150.50") and sale.buyer_name is None


def test_freebie_is_free_and_forced_to_cash():
    sale = parse_sale_form(_form(sale_type="Freebie", unit_price="999", payment_method="Credit",
                                 buyer_name=""), **KW)
    assert sale.unit_price == 0 and sale.payment_method == "Cash" and sale.buyer_name is None


@pytest.mark.parametrize("price", ["0", "", "-5", "abc"])
def test_a_real_sale_needs_a_positive_price(price):
    with pytest.raises(SaleFormError):
        parse_sale_form(_form(unit_price=price), **KW)


def test_credit_needs_a_buyer_name_and_it_is_trimmed():
    with pytest.raises(SaleFormError, match="credit sale"):
        parse_sale_form(_form(payment_method="Credit", buyer_name="  "), **KW)
    sale = parse_sale_form(_form(payment_method="Credit", buyer_name="  Ana  "), **KW)
    assert sale.buyer_name == "Ana"
    with pytest.raises(SaleFormError, match="too long"):
        parse_sale_form(_form(payment_method="Credit", buyer_name="x" * 121), **KW)


def test_refill_blocked_only_when_not_allowed():
    assert parse_sale_form(_form(sale_type="Refill"), **KW).sale_type == "Refill"
    with pytest.raises(SaleFormError, match="HQ only"):
        parse_sale_form(_form(sale_type="Refill"), allow_refill=False, refill_error="HQ only", **KW)


def test_first_problem_wins_in_a_fixed_order():
    # bad quantity is reported before a bad sale type, which comes before the missing product
    with pytest.raises(SaleFormError, match="Quantity"):
        parse_sale_form(_form(qty_sold="0", sale_type="nope", sku=""), **KW)
    with pytest.raises(SaleFormError, match="pick a type"):
        parse_sale_form(_form(sale_type="nope", sku=""), **KW)
    with pytest.raises(SaleFormError, match="payment method"):
        parse_sale_form(_form(payment_method="Barter", sku=""), **KW)
    with pytest.raises(SaleFormError, match="pick a product"):
        parse_sale_form(_form(sku=""), **KW)
