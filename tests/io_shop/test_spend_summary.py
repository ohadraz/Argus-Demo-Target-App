from io_shop.accounts import Account, Purchase
from io_shop.spend_summary import (
    average_spend_per_item_lifetime,
    average_spend_per_item_this_month,
)


def an_account(purchases, total_cents, total_this_month_cents):
    return Account(
        shopper_id="shopper-1",
        purchases=tuple(purchases),
        total_cents=total_cents,
        total_this_month_cents=total_this_month_cents
    )


def test_the_monthly_average_is_correct_for_a_shopper_who_did_buy():
    account = an_account(
        purchases=[
            Purchase(price_cents=1_000, in_current_month=True),
            Purchase(price_cents=3_000, in_current_month=True),
            Purchase(price_cents=9_999, in_current_month=False)
        ],
        total_cents=13_999,
        total_this_month_cents=4_000
    )

    assert average_spend_per_item_this_month(account) == 2_000


def test_the_monthly_average_is_nothing_for_a_shopper_who_bought_nothing():
    """The case that took the account page down when the flag went on.

    A shopper with a history but nothing yet this month divides by zero on the
    old code. There is no average to report, and the honest answer is nothing -
    not a failed page.
    """
    account = an_account(
        purchases=[Purchase(price_cents=9_999, in_current_month=False)],
        total_cents=9_999,
        total_this_month_cents=0
    )

    assert average_spend_per_item_this_month(account) == 0


def test_the_monthly_average_is_nothing_for_a_brand_new_account():
    account = an_account(purchases=[], total_cents=0, total_this_month_cents=0)

    assert average_spend_per_item_this_month(account) == 0


def test_the_lifetime_average_is_correct_and_survives_an_empty_history():
    bought = an_account(
        purchases=[
            Purchase(price_cents=1_000, in_current_month=False),
            Purchase(price_cents=2_001, in_current_month=True)
        ],
        total_cents=3_001,
        total_this_month_cents=2_001
    )
    empty = an_account(purchases=[], total_cents=0, total_this_month_cents=0)

    assert average_spend_per_item_lifetime(bought) == 1_500
    assert average_spend_per_item_lifetime(empty) == 0
</content>