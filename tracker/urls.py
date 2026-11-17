from django.urls import path

from . import views

app_name = "tracker"

urlpatterns = [
    path("", views.ApiRootView.as_view(), name="api_root"),
    path("categories/", views.CategoryListCreateView.as_view(), name="category_list"),
    path(
        "categories/<int:pk>/",
        views.CategoryDetailView.as_view(),
        name="category_detail",
    ),
    path(
        "transactions/",
        views.TransactionListCreateView.as_view(),
        name="transaction_list",
    ),
    path(
        "transactions/export/",
        views.TransactionExportView.as_view(),
        name="transaction_export",
    ),
    path(
        "transactions/<int:pk>/",
        views.TransactionDetailView.as_view(),
        name="transaction_detail",
    ),
    path("budgets/", views.BudgetListCreateView.as_view(), name="budget_list"),
    path("budgets/<int:pk>/", views.BudgetDetailView.as_view(), name="budget_detail"),
    path(
        "analytics/summary/",
        views.MonthlySummaryView.as_view(),
        name="analytics_summary",
    ),
    path(
        "analytics/categories/",
        views.CategoryTotalsView.as_view(),
        name="analytics_categories",
    ),
    path("analytics/trend/", views.TrendView.as_view(), name="analytics_trend"),
    path(
        "analytics/dashboard/",
        views.DashboardAnalyticsView.as_view(),
        name="analytics_dashboard",
    ),
]
