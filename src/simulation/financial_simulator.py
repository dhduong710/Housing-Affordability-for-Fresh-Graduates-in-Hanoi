"""Simulate the time needed to buy an apartment with savings."""

from decimal import ROUND_HALF_UP, Decimal
from math import isfinite


def simulate_cash_purchase(
    house_price: int,
    monthly_income: int,
    monthly_expense: int,
    initial_savings: int,
    max_months: int = 600,
    transaction_fee: int = 0,
    annual_savings_rate: float = 0.0,
    annual_salary_growth: float = 0.0,
    annual_house_price_growth: float = 0.0,
) -> dict:
    """Return the first affordable month and the monthly savings history.

    Money is in whole VND; the annual savings rate is a decimal (0.06 = 6%).
    Interest is calculated on positive opening savings and rounded to whole VND.
    Income and expenses are applied at the end of each month. Salary increases
    after every 12 completed months; house prices change monthly.
    """
    amounts = {
        "house_price": house_price,
        "monthly_income": monthly_income,
        "monthly_expense": monthly_expense,
        "initial_savings": initial_savings,
        "transaction_fee": transaction_fee,
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
    rates = {
        "annual_savings_rate": annual_savings_rate,
        "annual_salary_growth": annual_salary_growth,
        "annual_house_price_growth": annual_house_price_growth,
    }
    for name, rate in rates.items():
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise TypeError(f"{name} must be a number")
        if not isfinite(rate) or rate < 0:
            raise ValueError(f"{name} must be finite and non-negative")

    savings = initial_savings
    annual_rate = Decimal(str(annual_savings_rate))
    salary_growth = Decimal(str(annual_salary_growth))
    house_growth = Decimal(str(annual_house_price_growth))
    history = []
    for month in range(max_months + 1):
        income = 0
        expense = 0
        interest = 0
        if month > 0:
            completed_years = (month - 1) // 12
            income = int(
                (
                    Decimal(monthly_income) * (1 + salary_growth) ** completed_years
                ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            )
            expense = monthly_expense
            interest = int(
                (Decimal(max(savings, 0)) * annual_rate / 12).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )
        savings += interest + income - expense
        current_house_price = int(
            (
                Decimal(house_price) * (1 + house_growth) ** (Decimal(month) / 12)
            ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
        target_price = current_house_price + transaction_fee
        history.append(
            {
                "month": month,
                "income": income,
                "expense": expense,
                "interest": interest,
                "savings": savings,
                "house_price": current_house_price,
                "target_price": target_price,
            }
        )
        if savings >= target_price:
            return {"months_to_buy": month, "history": history}

    return {"months_to_buy": None, "history": history}


def calculate_months_to_buy(
    house_price: int,
    monthly_income: int,
    monthly_expense: int,
    initial_savings: int,
    max_months: int = 600,
    transaction_fee: int = 0,
    annual_savings_rate: float = 0.0,
    annual_salary_growth: float = 0.0,
    annual_house_price_growth: float = 0.0,
) -> int | None:
    """Return the first affordable month, or None within the given horizon."""
    result = simulate_cash_purchase(
        house_price,
        monthly_income,
        monthly_expense,
        initial_savings,
        max_months,
        transaction_fee,
        annual_savings_rate,
        annual_salary_growth,
        annual_house_price_growth,
    )
    return result["months_to_buy"]
