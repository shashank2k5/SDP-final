from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Category(models.Model):
    """A user-owned label for transactions, either income or expense."""

    class Type(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=60)
    type = models.CharField(max_length=10, choices=Type.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["type", "name"]
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "user",
                name="uniq_category_name_per_user",
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=["income", "expense"]),
                name="category_valid_type",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    @property
    def is_expense(self):
        return self.type == self.Type.EXPENSE

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Category name cannot be blank."})

        if self.pk and self.type:
            previous_type = (
                Category.objects.filter(pk=self.pk)
                .values_list("type", flat=True)
                .first()
            )
            if (
                previous_type
                and previous_type != self.type
                and self.transactions.exists()
            ):
                raise ValidationError(
                    {
                        "type": (
                            "The type of a category with existing transactions "
                            "cannot be changed."
                        )
                    }
                )


class Transaction(models.Model):
    """A single income or expense entry belonging to one user."""

    class Type(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    category = models.ForeignKey(
        Category,
        # PROTECT guarantees a category with transactions can never be deleted,
        # even from the admin or a shell session.
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=10, choices=Type.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["user", "date"], name="txn_user_date_idx"),
            models.Index(fields=["user", "type"], name="txn_user_type_idx"),
            models.Index(fields=["user", "category"], name="txn_user_category_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="transaction_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=["income", "expense"]),
                name="transaction_valid_type",
            ),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} — {self.category.name}"

    def clean(self):
        super().clean()
        errors = {}

        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Amount must be greater than zero."

        if self.date and self.date > timezone.localdate():
            errors["date"] = "A transaction date cannot be in the future."

        category = self.category if self.category_id else None
        if category is not None:
            if self.user_id and category.user_id != self.user_id:
                errors["category"] = "That category belongs to another user."
            if self.type and category.type != self.type:
                errors["type"] = (
                    f"'{category.name}' is an {category.get_type_display().lower()} "
                    f"category, so the transaction must be {category.type}."
                )

        if errors:
            raise ValidationError(errors)


class Budget(models.Model):
    """A spending limit for one expense category in one calendar month."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="budgets",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="budgets",
    )
    month = models.DateField(help_text="Always stored as the first day of the month.")
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category", "month"],
                name="uniq_budget_per_category_month",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="budget_amount_positive",
            ),
        ]
        indexes = [models.Index(fields=["user", "month"], name="budget_user_month_idx")]

    def __str__(self):
        return f"{self.category.name} — {self.month:%B %Y}"

    def save(self, *args, **kwargs):
        if self.month:
            self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}

        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Budget amount must be greater than zero."

        category = self.category if self.category_id else None
        if category is not None:
            if category.type != Category.Type.EXPENSE:
                errors["category"] = "Budgets can only be set for expense categories."
            if self.user_id and category.user_id != self.user_id:
                errors["category"] = "That category belongs to another user."

        if errors:
            raise ValidationError(errors)
