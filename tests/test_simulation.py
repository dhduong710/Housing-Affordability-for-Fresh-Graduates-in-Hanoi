"""Tests for the fixed-cash-flow home purchase simulation."""

import unittest

from src.simulation.financial_simulator import calculate_months_to_buy


class CalculateMonthsToBuyTests(unittest.TestCase):
    def test_reaches_target_after_ten_months(self):
        result = calculate_months_to_buy(
            house_price=200_000_000,
            monthly_income=15_000_000,
            monthly_expense=5_000_000,
            initial_savings=100_000_000,
        )
        self.assertEqual(result, 10)

    def test_already_affordable_at_month_zero(self):
        result = calculate_months_to_buy(200, 5, 10, 200)
        self.assertEqual(result, 0)

    def test_reaches_target_on_last_month(self):
        result = calculate_months_to_buy(200, 15, 5, 100, max_months=10)
        self.assertEqual(result, 10)

    def test_returns_none_when_horizon_is_too_short(self):
        result = calculate_months_to_buy(200, 15, 5, 100, max_months=9)
        self.assertIsNone(result)

    def test_income_not_greater_than_expenses(self):
        for income in (5, 4):
            with self.subTest(income=income):
                result = calculate_months_to_buy(200, income, 5, 100)
                self.assertIsNone(result)

    def test_invalid_values_raise_error(self):
        invalid_inputs = (
            (0, 15, 5, 100, 10),
            (200, -1, 5, 100, 10),
            (200, 15, -1, 100, 10),
            (200, 15, 5, -1, 10),
            (200, 15, 5, 100, 0),
        )
        for arguments in invalid_inputs:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                calculate_months_to_buy(*arguments)

    def test_invalid_types_raise_error(self):
        invalid_inputs = (
            (200, 15, 5, 100, 1.5),
            (200, 15.5, 5, 100, 10),
            (200, True, 5, 100, 10),
        )
        for arguments in invalid_inputs:
            with self.subTest(arguments=arguments), self.assertRaises(TypeError):
                calculate_months_to_buy(*arguments)


if __name__ == "__main__":
    unittest.main()
