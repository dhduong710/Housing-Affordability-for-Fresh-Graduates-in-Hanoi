"""Simulate the time needed to buy an apartment with savings."""


def calculate_months_to_buy(
    house_price: int,
    monthly_income: int,
    monthly_expense: int,
    initial_savings: int,
    max_months: int = 600,
) -> int | None:
    """Return the first affordable month, or None within the given horizon.

    All amounts are whole VND. Income and expenses are applied at the end of
    each month; house price and monthly cash flow stay constant.
    """
    amounts = {
        "house_price": house_price,
        "monthly_income": monthly_income,
        "monthly_expense": monthly_expense,
        "initial_savings": initial_savings,
    }
    for name, value in amounts.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be a whole number of VND")
        if value < 0:
            raise ValueError(f"{name} must not be negative")

    if house_price == 0:
        raise ValueError("house_price must be greater than zero")
    if isinstance(max_months, bool) or not isinstance(max_months, int):
        raise TypeError("max_months must be a positive integer")
    if max_months <= 0:
        raise ValueError("max_months must be a positive integer")

    savings = initial_savings
    if savings >= house_price:
        return 0

    for month in range(1, max_months + 1):
        savings += monthly_income - monthly_expense
        if savings >= house_price:
            return month

    return None
