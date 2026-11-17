from datetime import date, timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.serializers import RegisterSerializer
from tracker.models import Budget, Category, Transaction

PASSWORD = "V3ry-Str0ng-Pass!"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def make_user(username="alice", email=None):
    return User.objects.create_user(
        username=username,
        email=email or f"{username}@example.com",
        password=PASSWORD,
    )


def make_category(user, name="Food", category_type=Category.Type.EXPENSE):
    return Category.objects.create(user=user, name=name, type=category_type)


def make_transaction(
    user,
    category,
    amount="100.00",
    day=date(2026, 5, 10),
    description="Sample",
    txn_type=None,
):
    return Transaction.objects.create(
        user=user,
        category=category,
        amount=Decimal(amount),
        date=day,
        description=description,
        type=txn_type or category.type,
    )


class ApiTestCase(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.other = make_user("bob")
        self.client = APIClient()
        self.client.force_login(self.user)

    def api(self, path, method="get", data=None):
        handler = getattr(self.client, method)
        if method in {"post", "put", "patch"}:
            return handler(path, data or {}, format="json")
        return handler(path)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_api_requires_login(self):
        response = self.client.get("/api/transactions/")
        self.assertIn(response.status_code, (401, 403))

        response = self.client.post(
            "/api/categories/", data={"name": "Food", "type": "expense"}
        )
        self.assertIn(response.status_code, (401, 403))

    def test_register_page_creates_and_logs_in_user(self):
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("dashboard"))
        self.assertTrue(User.objects.filter(username="newuser").exists())

        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)

    def test_register_page_rejects_duplicate_username(self):
        make_user("taken")
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "TAKEN",
                "email": "other@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already taken")

    def test_register_page_rejects_mismatched_passwords(self):
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "mismatch",
                "email": "mismatch@example.com",
                "password1": PASSWORD,
                "password2": "Different-Pass-99!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="mismatch").exists())

    def test_login_and_logout_pages(self):
        make_user("carol")
        response = self.client.post(
            "/accounts/login/", {"username": "carol", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

        response = self.client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_login_page_rejects_bad_password(self):
        make_user("dave")
        response = self.client.post(
            "/accounts/login/", {"username": "dave", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_api_register_me_logout_flow(self):
        client = APIClient()
        response = client.post(
            "/api/auth/register/",
            {
                "username": "apiuser",
                "email": "api@example.com",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["username"], "apiuser")

        me = client.get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["username"], "apiuser")

        logout = client.post("/api/auth/logout/")
        self.assertEqual(logout.status_code, 204)
        self.assertIn(client.get("/api/auth/me/").status_code, (401, 403))

    def test_api_login_rejects_bad_credentials(self):
        make_user("erin")
        client = APIClient()
        response = client.post(
            "/api/auth/login/",
            {"username": "erin", "password": "nope-nope-nope"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_register_serializer_rejects_weak_password(self):
        serializer = RegisterSerializer(
            data={
                "username": "weak",
                "email": "weak@example.com",
                "password": "12345678",
                "password_confirm": "12345678",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("password", serializer.errors)


# ---------------------------------------------------------------------------
# Category management
# ---------------------------------------------------------------------------
class CategoryTests(ApiTestCase):
    def test_create_and_list_own_categories(self):
        response = self.api(
            "/api/categories/",
            "post",
            {"name": "Groceries", "type": "expense"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Groceries")
        self.assertEqual(response.data["transaction_count"], 0)

        make_category(self.other, "Bob Only")
        listing = self.api("/api/categories/")
        self.assertEqual(listing.status_code, 200)
        names = [row["name"] for row in listing.data]
        self.assertIn("Groceries", names)
        self.assertNotIn("Bob Only", names)

    def test_category_names_are_unique_per_user_case_insensitively(self):
        make_category(self.user, "Food")
        response = self.api(
            "/api/categories/", "post", {"name": "  food  ", "type": "expense"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

        # A different user may reuse the name.
        client = APIClient()
        client.force_login(self.other)
        other_response = client.post(
            "/api/categories/",
            {"name": "Food", "type": "expense"},
            format="json",
        )
        self.assertEqual(other_response.status_code, 201)

    def test_category_with_transactions_cannot_be_deleted(self):
        category = make_category(self.user, "Rent")
        make_transaction(self.user, category)

        response = self.api(f"/api/categories/{category.id}/", "delete")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())

    def test_empty_category_can_be_deleted(self):
        category = make_category(self.user, "Temp")
        response = self.api(f"/api/categories/{category.id}/", "delete")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())

    def test_deleting_category_cascades_its_budgets(self):
        category = make_category(self.user, "Utilities")
        Budget.objects.create(
            user=self.user,
            category=category,
            month=date(2026, 5, 1),
            amount=Decimal("1000.00"),
        )
        response = self.api(f"/api/categories/{category.id}/", "delete")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Budget.objects.filter(user=self.user).count(), 0)

    def test_type_cannot_change_when_transactions_exist(self):
        category = make_category(self.user, "Food")
        make_transaction(self.user, category)

        response = self.api(
            f"/api/categories/{category.id}/", "patch", {"type": "income"}
        )
        self.assertEqual(response.status_code, 400)
        category.refresh_from_db()
        self.assertEqual(category.type, Category.Type.EXPENSE)

    def test_category_update_without_transactions(self):
        category = make_category(self.user, "Food")
        response = self.api(
            f"/api/categories/{category.id}/",
            "patch",
            {"name": "Dining", "type": "income"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Dining")
        self.assertEqual(response.data["type"], "income")

    def test_other_users_category_is_not_visible(self):
        category = make_category(self.other, "Secret")
        url = f"/api/categories/{category.id}/"

        self.assertEqual(self.api(url).status_code, 404)
        self.assertEqual(
            self.api(url, "patch", {"name": "Hijacked"}).status_code, 404
        )
        self.assertEqual(self.api(url, "delete").status_code, 404)
        category.refresh_from_db()
        self.assertEqual(category.name, "Secret")


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
class TransactionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.food = make_category(self.user, "Food")
        self.salary = make_category(
            self.user, "Salary", Category.Type.INCOME
        )

    def test_create_valid_transaction(self):
        response = self.api(
            "/api/transactions/",
            "post",
            {
                "amount": "42.50",
                "category": self.food.id,
                "date": "2026-05-10",
                "description": "Lunch",
                "type": "expense",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertAlmostEqual(response.data["amount"], 42.50)
        self.assertEqual(response.data["category_name"], "Food")
        self.assertEqual(
            Transaction.objects.filter(user=self.user).count(), 1
        )

    def test_amount_must_be_greater_than_zero(self):
        for amount in ("0", "-5", "0.00"):
            response = self.api(
                "/api/transactions/",
                "post",
                {
                    "amount": amount,
                    "category": self.food.id,
                    "date": "2026-05-10",
                    "type": "expense",
                },
            )
            self.assertEqual(response.status_code, 400, amount)
            self.assertIn("amount", response.data)

    def test_future_date_is_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self.api(
            "/api/transactions/",
            "post",
            {
                "amount": "10.00",
                "category": self.food.id,
                "date": tomorrow.isoformat(),
                "type": "expense",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("date", response.data)

    def test_type_must_match_category_type(self):
        response = self.api(
            "/api/transactions/",
            "post",
            {
                "amount": "10.00",
                "category": self.food.id,
                "date": "2026-05-10",
                "type": "income",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("type", response.data)

    def test_cannot_use_another_users_category(self):
        other_category = make_category(self.other, "Bobs Food")
        response = self.api(
            "/api/transactions/",
            "post",
            {
                "amount": "10.00",
                "category": other_category.id,
                "date": "2026-05-10",
                "type": "expense",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.data)

    def test_list_only_own_transactions_and_supports_filters(self):
        make_transaction(self.user, self.food, "100.00", date(2026, 5, 2), "Groceries")
        make_transaction(self.user, self.food, "200.00", date(2026, 5, 20), "Dinner")
        make_transaction(
            self.user, self.salary, "5000.00", date(2026, 5, 1), "May salary"
        )
        make_transaction(self.other, make_category(self.other), "999.00")
        make_transaction(
            self.user, self.food, "50.00", date(2026, 11, 1), "Future month"
        )

        response = self.api("/api/transactions/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)

        response = self.api("/api/transactions/?month=2026-05")
        self.assertEqual(len(response.data), 3)

        response = self.api(
            "/api/transactions/?start=2026-05-01&end=2026-05-10"
        )
        self.assertEqual(len(response.data), 2)

        response = self.api(f"/api/transactions/?category={self.food.id}")
        self.assertEqual(len(response.data), 3)

        response = self.api("/api/transactions/?type=income")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["description"], "May salary")

        response = self.api("/api/transactions/?search=dinner")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["description"], "Dinner")

        response = self.api(
            f"/api/transactions/?month=2026-05&type=expense&category={self.food.id}"
        )
        self.assertEqual(len(response.data), 2)

    def test_invalid_filters_return_400(self):
        for query in (
            "start=not-a-date",
            "end=2026-13-01",
            "category=abc",
            "type=transfer",
            "month=2026-99",
        ):
            response = self.api(f"/api/transactions/?{query}")
            self.assertEqual(response.status_code, 400, query)

    def test_update_transaction(self):
        txn = make_transaction(self.user, self.food)
        response = self.api(
            f"/api/transactions/{txn.id}/",
            "patch",
            {"amount": "75.00", "description": "Updated"},
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        self.assertEqual(txn.amount, Decimal("75.00"))
        self.assertEqual(txn.description, "Updated")

    def test_update_cannot_create_invalid_state(self):
        txn = make_transaction(self.user, self.food)
        url = f"/api/transactions/{txn.id}/"

        response = self.api(url, "patch", {"amount": "0"})
        self.assertEqual(response.status_code, 400)

        response = self.api(
            url, "patch", {"date": (timezone.localdate() + timedelta(days=1)).isoformat()}
        )
        self.assertEqual(response.status_code, 400)

        response = self.api(url, "patch", {"type": "income"})
        self.assertEqual(response.status_code, 400)

    def test_delete_own_transaction(self):
        txn = make_transaction(self.user, self.food)
        response = self.api(f"/api/transactions/{txn.id}/", "delete")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Transaction.objects.filter(pk=txn.pk).exists())

    def test_cannot_read_update_or_delete_other_users_transaction(self):
        other_txn = make_transaction(
            self.other, make_category(self.other, "Bobs")
        )
        url = f"/api/transactions/{other_txn.id}/"

        self.assertEqual(self.api(url).status_code, 404)
        self.assertEqual(
            self.api(url, "patch", {"amount": "1.00"}).status_code, 404
        )
        self.assertEqual(self.api(url, "delete").status_code, 404)
        self.assertTrue(Transaction.objects.filter(pk=other_txn.pk).exists())


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
class BudgetTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.food = make_category(self.user, "Food")

    def create_budget(self, amount="1000.00", month="2026-05", category=None):
        return self.api(
            "/api/budgets/",
            "post",
            {
                "category": (category or self.food).id,
                "month": month,
                "amount": amount,
            },
        )

    def test_create_budget_with_server_calculated_fields(self):
        response = self.create_budget()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["month"], "2026-05")
        self.assertEqual(response.data["spent"], 0.0)
        self.assertEqual(response.data["percent_used"], 0.0)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["status_label"], "On track")
        self.assertAlmostEqual(response.data["remaining"], 1000.0)

    def test_budget_only_for_expense_categories(self):
        salary = make_category(self.user, "Salary", Category.Type.INCOME)
        response = self.create_budget(category=salary)
        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.data)

    def test_budget_upsert_updates_existing_row(self):
        first = self.create_budget("1000.00")
        self.assertEqual(first.status_code, 201)

        second = self.create_budget("1500.00")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(Budget.objects.filter(user=self.user).count(), 1)
        self.assertAlmostEqual(second.data["amount"], 1500.0)

    def test_budget_status_thresholds(self):
        cases = [
            ("799.99", "ok"),
            ("800.00", "warning"),
            ("1000.00", "warning"),
            ("1000.01", "over"),
        ]
        for index, (spent, expected_status) in enumerate(cases):
            category = make_category(self.user, f"Threshold {index}")
            month = date(2026, 3, 1)
            Budget.objects.create(
                user=self.user,
                category=category,
                month=month,
                amount=Decimal("1000.00"),
            )
            make_transaction(
                self.user,
                category,
                amount=spent,
                day=date(2026, 3, 15),
            )
            response = self.api("/api/analytics/dashboard/?month=2026-03")
            self.assertEqual(response.status_code, 200)
            row = next(
                budget
                for budget in response.data["budgets"]
                if budget["category_id"] == category.id
            )
            self.assertEqual(row["status"], expected_status, spent)

    def test_budget_month_is_normalised_and_validated(self):
        response = self.create_budget(month="2026-05-21")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["month"], "2026-05")

        invalid = self.create_budget(month="05/2026")
        self.assertEqual(invalid.status_code, 400)

    def test_budget_amount_must_be_positive(self):
        response = self.create_budget(amount="0")
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.data)

    def test_budget_list_and_permissions(self):
        own = Budget.objects.create(
            user=self.user,
            category=self.food,
            month=date(2026, 5, 1),
            amount=Decimal("1000.00"),
        )
        other_category = make_category(self.other, "Bobs Food")
        other = Budget.objects.create(
            user=self.other,
            category=other_category,
            month=date(2026, 5, 1),
            amount=Decimal("500.00"),
        )

        listing = self.api("/api/budgets/?month=2026-05")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([row["id"] for row in listing.data], [own.id])

        url = f"/api/budgets/{other.id}/"
        self.assertEqual(self.api(url).status_code, 404)
        self.assertEqual(
            self.api(url, "patch", {"amount": "1.00"}).status_code, 404
        )
        self.assertEqual(self.api(url, "delete").status_code, 404)

    def test_delete_own_budget(self):
        budget = Budget.objects.create(
            user=self.user,
            category=self.food,
            month=date(2026, 5, 1),
            amount=Decimal("1000.00"),
        )
        response = self.api(f"/api/budgets/{budget.id}/", "delete")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Budget.objects.filter(pk=budget.pk).exists())

    def test_budget_summary_counts_alerts(self):
        warning_category = make_category(self.user, "Warning")
        over_category = make_category(self.user, "Over")
        for category, budget_amount, spent in (
            (warning_category, "1000.00", "850.00"),
            (over_category, "1000.00", "1200.00"),
        ):
            Budget.objects.create(
                user=self.user,
                category=category,
                month=date(2026, 6, 1),
                amount=Decimal(budget_amount),
            )
            make_transaction(
                self.user, category, amount=spent, day=date(2026, 6, 10)
            )

        response = self.api("/api/analytics/dashboard/?month=2026-06")
        summary = response.data["budget_summary"]
        self.assertEqual(summary["warning_count"], 1)
        self.assertEqual(summary["over_count"], 1)
        self.assertAlmostEqual(summary["total_budget"], 2000.0)
        self.assertAlmostEqual(summary["total_spent"], 2050.0)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
class AnalyticsTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.food = make_category(self.user, "Food")
        self.travel = make_category(self.user, "Travel")
        self.salary = make_category(self.user, "Salary", Category.Type.INCOME)

        make_transaction(
            self.user, self.salary, "5000.00", date(2026, 5, 2), "May salary"
        )
        make_transaction(
            self.user, self.food, "1200.00", date(2026, 5, 10), "Groceries"
        )
        make_transaction(
            self.user, self.travel, "800.00", date(2026, 5, 20), "Trip"
        )
        make_transaction(
            self.user, self.salary, "4000.00", date(2026, 4, 2), "April salary"
        )
        make_transaction(
            self.user, self.food, "500.00", date(2026, 4, 12), "April food"
        )

        other_food = make_category(self.other, "Other Food")
        make_transaction(
            self.other, other_food, "9999.00", date(2026, 5, 10), "Not yours"
        )

    def test_monthly_summary(self):
        response = self.api("/api/analytics/summary/?month=2026-05")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["month"], "2026-05")
        self.assertEqual(response.data["month_label"], "May 2026")
        self.assertAlmostEqual(response.data["income"], 5000.0)
        self.assertAlmostEqual(response.data["expense"], 2000.0)
        self.assertAlmostEqual(response.data["net"], 3000.0)
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["income_count"], 1)
        self.assertEqual(response.data["expense_count"], 2)
        self.assertAlmostEqual(response.data["savings_rate"], 60.0)

    def test_summary_defaults_to_current_month(self):
        response = self.api("/api/analytics/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["month"], timezone.localdate().strftime("%Y-%m")
        )

    def test_category_totals_only_include_expenses(self):
        response = self.api("/api/analytics/categories/?month=2026-05")
        self.assertEqual(response.status_code, 200)
        categories = response.data["categories"]
        self.assertEqual(len(categories), 2)

        by_name = {row["name"]: row for row in categories}
        self.assertNotIn("Salary", by_name)
        self.assertAlmostEqual(by_name["Food"]["total"], 1200.0)
        self.assertAlmostEqual(by_name["Travel"]["total"], 800.0)
        self.assertAlmostEqual(by_name["Food"]["percentage"], 60.0)
        self.assertAlmostEqual(by_name["Travel"]["percentage"], 40.0)
        self.assertEqual(by_name["Food"]["count"], 1)

    def test_trend_returns_six_months_with_zero_fill(self):
        response = self.api(
            "/api/analytics/trend/?month=2026-05&months=6"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["months"], 6)
        trend = response.data["trend"]
        self.assertEqual(len(trend), 6)
        self.assertEqual(trend[0]["month"], "2025-12")
        self.assertEqual(trend[-1]["month"], "2026-05")

        may = trend[-1]
        self.assertAlmostEqual(may["income"], 5000.0)
        self.assertAlmostEqual(may["expense"], 2000.0)
        self.assertAlmostEqual(may["net"], 3000.0)

        april = trend[-2]
        self.assertAlmostEqual(april["income"], 4000.0)
        self.assertAlmostEqual(april["expense"], 500.0)

        december = trend[0]
        self.assertAlmostEqual(december["income"], 0.0)
        self.assertAlmostEqual(december["expense"], 0.0)
        self.assertEqual(december["count"], 0)

    def test_trend_validation(self):
        for query in ("months=0", "months=25", "months=abc"):
            response = self.api(f"/api/analytics/trend/?month=2026-05&{query}")
            self.assertEqual(response.status_code, 400, query)

    def test_invalid_month_returns_400_for_analytics(self):
        for path in (
            "/api/analytics/summary/?month=2026-13",
            "/api/analytics/categories/?month=nope",
            "/api/analytics/dashboard/?month=13-2026",
        ):
            response = self.api(path)
            self.assertEqual(response.status_code, 400, path)

    def test_dashboard_combines_analytics(self):
        Budget.objects.create(
            user=self.user,
            category=self.food,
            month=date(2026, 5, 1),
            amount=Decimal("1500.00"),
        )
        response = self.api("/api/analytics/dashboard/?month=2026-05")
        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.data)
        self.assertIn("categories", response.data)
        self.assertIn("budgets", response.data)
        self.assertIn("budget_summary", response.data)
        self.assertIn("trend", response.data)
        self.assertEqual(len(response.data["trend"]), 6)
        self.assertAlmostEqual(response.data["summary"]["net"], 3000.0)

        budget_row = response.data["budgets"][0]
        self.assertAlmostEqual(budget_row["spent"], 1200.0)
        self.assertAlmostEqual(budget_row["percent_used"], 80.0)
        self.assertEqual(budget_row["status"], "warning")

    def test_analytics_are_scoped_to_requesting_user(self):
        response = self.api("/api/analytics/summary/?month=2026-05")
        self.assertAlmostEqual(response.data["expense"], 2000.0)
        self.assertNotAlmostEqual(response.data["expense"], 11999.0)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
class ExportTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.food = make_category(self.user, "Food")
        self.salary = make_category(self.user, "Salary", Category.Type.INCOME)
        make_transaction(
            self.user,
            self.food,
            "120.50",
            date(2026, 5, 5),
            'Lunch, cafe "downtown"',
        )
        make_transaction(
            self.user, self.salary, "5000.00", date(2026, 5, 1), "May salary"
        )
        make_transaction(
            self.user, self.food, "75.00", date(2026, 4, 5), "April lunch"
        )
        make_transaction(
            self.other, make_category(self.other), "999.00", date(2026, 5, 5), "Other"
        )

    def test_export_selected_month_as_csv(self):
        response = self.client.get("/api/transactions/export/?month=2026-05")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn(
            'filename="transactions-2026-05.csv"',
            response["Content-Disposition"],
        )

        content = response.content.decode("utf-8-sig")
        rows = list(__import__("csv").reader(content.splitlines()))
        self.assertEqual(
            rows[0], ["Date", "Type", "Category", "Description", "Amount"]
        )
        self.assertEqual(len(rows), 3)
        descriptions = [row[3] for row in rows[1:]]
        self.assertIn('Lunch, cafe "downtown"', descriptions)
        self.assertIn("May salary", descriptions)
        self.assertNotIn("April lunch", descriptions)
        self.assertNotIn("Other", descriptions)

    def test_export_applies_filters(self):
        response = self.client.get(
            "/api/transactions/export/?month=2026-05&type=expense"
        )
        content = response.content.decode("utf-8-sig")
        rows = list(__import__("csv").reader(content.splitlines()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "Expense")

    def test_export_defaults_to_current_month(self):
        response = self.client.get("/api/transactions/export/")
        self.assertEqual(response.status_code, 200)
        current = timezone.localdate().strftime("%Y-%m")
        self.assertIn(
            f'filename="transactions-{current}.csv"', response["Content-Disposition"]
        )

    def test_export_requires_authentication(self):
        client = APIClient()
        response = client.get("/api/transactions/export/?month=2026-05")
        self.assertIn(response.status_code, (401, 403))


# ---------------------------------------------------------------------------
# Model + admin registration
# ---------------------------------------------------------------------------
class ModelValidationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.expense = make_category(self.user, "Food")
        self.income = make_category(self.user, "Salary", Category.Type.INCOME)

    def test_transaction_model_rejects_invalid_amount_and_future_date(self):
        txn = Transaction(
            user=self.user,
            category=self.expense,
            amount=Decimal("0"),
            date=timezone.localdate(),
            type=Category.Type.EXPENSE,
        )
        with self.assertRaises(Exception):
            txn.full_clean()

        txn.amount = Decimal("10.00")
        txn.date = timezone.localdate() + timedelta(days=2)
        with self.assertRaises(Exception):
            txn.full_clean()

    def test_transaction_model_rejects_category_type_mismatch(self):
        txn = Transaction(
            user=self.user,
            category=self.income,
            amount=Decimal("10.00"),
            date=timezone.localdate(),
            type=Category.Type.EXPENSE,
        )
        with self.assertRaises(Exception):
            txn.full_clean()

    def test_budget_model_rejects_income_category(self):
        budget = Budget(
            user=self.user,
            category=self.income,
            month=date(2026, 5, 1),
            amount=Decimal("100.00"),
        )
        with self.assertRaises(Exception):
            budget.full_clean()

    def test_budget_month_normalised_on_save(self):
        budget = Budget.objects.create(
            user=self.user,
            category=self.expense,
            month=date(2026, 5, 23),
            amount=Decimal("100.00"),
        )
        budget.refresh_from_db()
        self.assertEqual(budget.month, date(2026, 5, 1))

    def test_category_unique_per_user_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(
                    user=self.user, name="food", type=Category.Type.EXPENSE
                )


class AdminRegistrationTests(TestCase):
    def test_all_models_are_registered(self):
        for model in (Category, Transaction, Budget):
            self.assertIn(model, admin.site._registry)
