"""Create a demo account full of realistic transactions and budgets."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tracker.models import Budget, Category, Transaction
from tracker.services import shift_month


CATEGORY_BLUEPRINT = [
    ("Salary", Category.Type.INCOME),
    ("Freelance", Category.Type.INCOME),
    ("Food", Category.Type.EXPENSE),
    ("Rent", Category.Type.EXPENSE),
    ("Travel", Category.Type.EXPENSE),
    ("Shopping", Category.Type.EXPENSE),
    ("Utilities", Category.Type.EXPENSE),
    ("Health", Category.Type.EXPENSE),
]


class Command(BaseCommand):
    help = (
        "Seed a demo user (default: demo / demo12345) with six months of "
        "transactions and budgets. Existing data for that user is replaced."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default="demo")
        parser.add_argument("--password", default="demo12345")
        parser.add_argument("--email", default="demo@example.com")
        parser.add_argument(
            "--keep-data",
            action="store_true",
            help="Do not delete the demo user's existing transactions/budgets.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        username = options["username"]
        password = options["password"]
        today = timezone.localdate()
        current_month = today.replace(day=1)

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": options["email"], "first_name": "Demo", "last_name": "User"},
        )
        user.email = options["email"]
        user.first_name = user.first_name or "Demo"
        user.last_name = user.last_name or "User"
        user.set_password(password)
        user.save()

        if not options["keep_data"]:
            Transaction.objects.filter(user=user).delete()
            Budget.objects.filter(user=user).delete()
            Category.objects.filter(user=user).delete()

        categories = {}
        for name, category_type in CATEGORY_BLUEPRINT:
            category, _ = Category.objects.get_or_create(
                user=user, name=name, defaults={"type": category_type}
            )
            categories[name] = category

        rng = random.Random(42)
        transactions = []

        def add(month: date, day_number: int, category_name: str, amount, description):
            last_day = monthrange(month.year, month.month)[1]
            max_day = min(last_day, today.day) if month == current_month else last_day
            day_number = max(1, min(day_number, max_day))
            category = categories[category_name]
            transactions.append(
                Transaction(
                    user=user,
                    category=category,
                    amount=Decimal(str(amount)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    ),
                    date=date(month.year, month.month, day_number),
                    description=description,
                    type=category.type,
                )
            )

        for offset in range(-5, 1):
            month = shift_month(current_month, offset)
            month_key = month.strftime("%B")

            add(month, 1, "Salary", Decimal("65000.00"), f"Monthly salary — {month_key}")
            if offset % 2 == 0:
                add(
                    month,
                    14,
                    "Freelance",
                    Decimal(str(7500 + rng.randint(0, 35) * 100)),
                    "Freelance design project",
                )

            add(
                month,
                4,
                "Food",
                Decimal(str(3100 + rng.randint(0, 12) * 100)),
                "Weekly groceries",
            )
            add(
                month,
                12,
                "Food",
                Decimal(str(850 + rng.randint(0, 9) * 100)),
                "Dinner with friends",
            )
            add(
                month,
                21,
                "Food",
                Decimal(str(420 + rng.randint(0, 5) * 50)),
                "Coffee and snacks",
            )

            add(month, 3, "Rent", Decimal("18000.00"), "Apartment rent")
            add(
                month,
                9,
                "Utilities",
                Decimal(str(1700 + rng.randint(0, 8) * 100)),
                "Electricity and internet",
            )
            add(
                month,
                17,
                "Shopping",
                Decimal(str(1800 + rng.randint(0, 15) * 150)),
                rng.choice(
                    ["New shoes", "Home supplies", "Bookstore haul", "Kitchen gadgets"]
                ),
            )
            add(
                month,
                24,
                "Health",
                Decimal(str(650 + rng.randint(0, 6) * 100)),
                rng.choice(["Pharmacy", "Gym membership", "Doctor visit"]),
            )
            if offset % 2 != 0:
                add(
                    month,
                    19,
                    "Travel",
                    Decimal(str(2200 + rng.randint(0, 20) * 200)),
                    rng.choice(["Weekend trip", "Train tickets", "Hotel booking"]),
                )
            else:
                add(
                    month,
                    27,
                    "Travel",
                    Decimal(str(600 + rng.randint(0, 6) * 100)),
                    "Local transport and cabs",
                )

        Transaction.objects.bulk_create(transactions)

        # Budgets: one per expense category per month. Ratios are chosen so the
        # current month demonstrates healthy, warning (>=80%) and over (>100%)
        # states out of the box.
        ratios = {
            "Food": Decimal("0.86"),
            "Rent": Decimal("1.15"),
            "Travel": Decimal("0.55"),
            "Shopping": Decimal("0.68"),
            "Utilities": Decimal("0.74"),
            "Health": Decimal("0.50"),
        }
        current_spend = {}
        for category_name in ratios:
            total = sum(
                (
                    txn.amount
                    for txn in transactions
                    if txn.category_id == categories[category_name].id
                    and txn.date.replace(day=1) == current_month
                ),
                Decimal("0.00"),
            )
            current_spend[category_name] = total

        budgets = []
        for offset in range(-5, 1):
            month = shift_month(current_month, offset)
            for category_name, ratio in ratios.items():
                spend = current_spend.get(category_name, Decimal("0.00"))
                base = (spend / ratio) if spend else Decimal("5000")
                amount = base.quantize(Decimal("100"), rounding=ROUND_HALF_UP)
                amount = max(amount, Decimal("100"))
                budgets.append(
                    Budget(
                        user=user,
                        category=categories[category_name],
                        month=month,
                        amount=amount,
                    )
                )
        Budget.objects.bulk_create(budgets)

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} demo account."))
        self.stdout.write("")
        self.stdout.write(f"  Username: {username}")
        self.stdout.write(f"  Password: {password}")
        self.stdout.write(f"  Transactions: {len(transactions)}")
        self.stdout.write(f"  Budgets: {len(budgets)}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Open /accounts/login/ and sign in, or register a fresh account."
            )
        )
