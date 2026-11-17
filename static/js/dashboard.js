/* ==========================================================================
   LedgerLite dashboard
   All data is loaded through the REST API with fetch(); no page reloads.
   ========================================================================== */
(() => {
    "use strict";

    const body = document.body;
    const TODAY = body.dataset.today;
    const CURRENT_MONTH = body.dataset.currentMonth;
    const LOGIN_URL = body.dataset.loginUrl || "/accounts/login/";

    const CURRENCY = body.dataset.currency || "USD";
    const LOCALE = body.dataset.locale || undefined;

    const PALETTE = [
        "#4f46e5", "#0ea5a4", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4",
        "#ec4899", "#84cc16", "#f97316", "#14b8a6", "#6366f1", "#eab308",
    ];

    const moneyFormatter = new Intl.NumberFormat(LOCALE, {
        style: "currency",
        currency: CURRENCY,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const compactFormatter = new Intl.NumberFormat(LOCALE, {
        style: "currency",
        currency: CURRENCY,
        notation: "compact",
        maximumFractionDigits: 1,
        currencyDisplay: "narrowSymbol",
    });
    const compactMoneyFormatter = new Intl.NumberFormat(LOCALE, {
        style: "currency",
        currency: CURRENCY,
        notation: "compact",
        maximumFractionDigits: 1,
    });
    const numberFormatter = new Intl.NumberFormat(LOCALE);

    const CURRENCY_SYMBOL = (() => {
        try {
            const part = moneyFormatter
                .formatToParts(0)
                .find((piece) => piece.type === "currency");
            return part ? part.value : CURRENCY;
        } catch (error) {
            return CURRENCY;
        }
    })();

    const fmtMoney = (value) => moneyFormatter.format(Number(value) || 0);
    const fmtCompact = (value) => compactFormatter.format(Number(value) || 0);
    const fmtCompactMoney = (value) => compactMoneyFormatter.format(Number(value) || 0);
    const fmtNumber = (value) => numberFormatter.format(Number(value) || 0);

    const state = {
        month: CURRENT_MONTH,
        categories: [],
        dashboard: null,
        transactions: [],
        budgets: [],
        filters: { start: "", end: "", category: "", type: "", search: "" },
        charts: { expense: null, trend: null },
        activeSection: "overview",
        loading: false,
    };

    let txnRequestId = 0;
    let dashboardRequestId = 0;
    let searchTimer = null;

    // ---------------------------------------------------------------------
    // Tiny helpers
    // ---------------------------------------------------------------------
    const $ = (selector, root = document) => root.querySelector(selector);
    const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (character) => {
            const entities = {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;",
            };
            return entities[character];
        });
    }

    function getCookie(name) {
        const match = document.cookie.match(
            new RegExp("(^|;\\s*)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]*)")
        );
        return match ? decodeURIComponent(match[2]) : "";
    }

    function monthBounds(monthKey) {
        const [year, month] = monthKey.split("-").map(Number);
        const lastDay = new Date(year, month, 0).getDate();
        return {
            start: `${monthKey}-01`,
            end: `${monthKey}-${String(lastDay).padStart(2, "0")}`,
        };
    }

    function monthLabel(monthKey) {
        const [year, month] = monthKey.split("-").map(Number);
        return new Date(year, month - 1, 1).toLocaleDateString(LOCALE, {
            month: "long",
            year: "numeric",
        });
    }

    function shiftMonth(monthKey, offset) {
        const [year, month] = monthKey.split("-").map(Number);
        const shifted = new Date(year, month - 1 + offset, 1);
        return `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, "0")}`;
    }

    function defaultTxnDate() {
        if (state.month === CURRENT_MONTH) return TODAY;
        const bounds = monthBounds(state.month);
        return bounds.end > TODAY ? TODAY : bounds.end;
    }

    function categoryColor(categoryId) {
        const index = state.categories.findIndex(
            (category) => Number(category.id) === Number(categoryId)
        );
        return PALETTE[(index >= 0 ? index : Number(categoryId) || 0) % PALETTE.length];
    }

    function categoryInitial(name) {
        return (String(name || "?").trim().charAt(0) || "?").toUpperCase();
    }

    function plural(count, singular, pluralWord = `${singular}s`) {
        return `${fmtNumber(count)} ${count === 1 ? singular : pluralWord}`;
    }

    // ---------------------------------------------------------------------
    // API client
    // ---------------------------------------------------------------------
    class ApiError extends Error {
        constructor(message, status = 0, data = null) {
            super(message);
            this.name = "ApiError";
            this.status = status;
            this.data = data;
        }
    }

    function extractError(data) {
        if (!data) return "";
        if (typeof data === "string") return data;
        if (Array.isArray(data)) return data.map(extractError).filter(Boolean).join(" ");
        if (typeof data === "object") {
            if (data.detail) return extractError(data.detail);
            return Object.entries(data)
                .filter(([key]) => key !== "code")
                .map(([, value]) => extractError(value))
                .filter(Boolean)
                .join(" ");
        }
        return String(data);
    }

    async function api(path, options = {}) {
        const method = (options.method || "GET").toUpperCase();
        const headers = new Headers(options.headers || {});
        headers.set("Accept", "application/json");

        let requestBody = options.body;
        if (requestBody !== undefined && !(requestBody instanceof FormData)) {
            headers.set("Content-Type", "application/json");
            requestBody = JSON.stringify(requestBody);
        }
        if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
            headers.set("X-CSRFToken", getCookie("csrftoken"));
        }

        let response;
        try {
            response = await fetch(path, {
                ...options,
                method,
                headers,
                body: requestBody,
                credentials: "same-origin",
            });
        } catch (error) {
            throw new ApiError("Network error. Check your connection and try again.");
        }

        if (response.status === 401 || response.status === 403) {
            const contentType = response.headers.get("content-type") || "";
            let payload = null;
            if (contentType.includes("application/json")) {
                try {
                    payload = await response.json();
                } catch (error) {
                    payload = null;
                }
            }
            const detail = extractError(payload);

            if (
                response.status === 401 ||
                /authentication credentials|not authenticated|session/i.test(detail)
            ) {
                const next = encodeURIComponent(window.location.pathname);
                window.location.href = `${LOGIN_URL}?next=${next}`;
                throw new ApiError("Your session expired. Redirecting to sign in…", 401);
            }

            if (!payload) {
                throw new ApiError(
                    "Your security token expired. Please refresh the page and try again.",
                    403
                );
            }
            throw new ApiError(
                detail || "You do not have permission to do that.",
                403,
                payload
            );
        }

        if (response.status === 204) return null;

        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
            if (!response.ok) {
                throw new ApiError(
                    `Unexpected server response (${response.status}).`,
                    response.status
                );
            }
            return null;
        }

        const data = await response.json();
        if (!response.ok) {
            throw new ApiError(
                extractError(data) || "Something went wrong. Please try again.",
                response.status,
                data
            );
        }
        return data;
    }

    // ---------------------------------------------------------------------
    // Notifications
    // ---------------------------------------------------------------------
    function showToast(message, variant = "success", title = "") {
        const container = $("#toastContainer");
        if (!container || !window.bootstrap) return;

        const icons = {
            success: "bi-check-circle-fill",
            error: "bi-exclamation-circle-fill",
            info: "bi-info-circle-fill",
        };
        const element = document.createElement("div");
        element.className = `toast app-toast toast--${variant}`;
        element.setAttribute("role", "status");
        element.setAttribute("aria-live", "polite");
        element.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">
                    <i class="bi ${icons[variant] || icons.info}"></i>
                    <div>
                        ${title ? `<strong class="d-block">${escapeHtml(title)}</strong>` : ""}
                        ${escapeHtml(message)}
                    </div>
                </div>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>`;
        container.appendChild(element);
        const toast = new bootstrap.Toast(element, { delay: 4200 });
        element.addEventListener("hidden.bs.toast", () => element.remove());
        toast.show();
    }

    function showPageAlert(message) {
        const alertBox = $("#pageAlert");
        if (!alertBox) return;
        alertBox.innerHTML = `
            <span><i class="bi bi-exclamation-circle-fill me-2"></i>${escapeHtml(message)}</span>
            <button type="button" aria-label="Dismiss">&times;</button>`;
        alertBox.classList.remove("d-none");
        const dismiss = $("button", alertBox);
        if (dismiss) {
            dismiss.addEventListener("click", () => alertBox.classList.add("d-none"));
        }
    }

    function handleError(error) {
        if (error && error.status === 401) return; // already redirecting
        const message =
            (error && error.message) || "Something went wrong. Please try again.";
        showToast(message, "error", "Request failed");
        console.error(error);
    }

    function askConfirm({ title, message, confirmLabel = "Delete", danger = true }) {
        return new Promise((resolve) => {
            const modalElement = $("#confirmModal");
            if (!modalElement || !window.bootstrap) {
                resolve(window.confirm(message));
                return;
            }

            $("#confirmTitle").textContent = title;
            $("#confirmMessage").textContent = message;
            const confirmButton = $("#confirmOkBtn");
            confirmButton.textContent = confirmLabel;
            confirmButton.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;

            const icon = $("#confirmIcon");
            icon.classList.toggle("is-danger", danger);
            icon.innerHTML = `<i class="bi ${danger ? "bi-trash3" : "bi-exclamation-triangle"}"></i>`;

            const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
            let confirmed = false;

            const finish = () => {
                confirmButton.removeEventListener("click", onConfirm);
                modalElement.removeEventListener("hidden.bs.modal", onHidden);
            };

            const onConfirm = () => {
                confirmed = true;
                finish();
                closeModal(modal);
                resolve(true);
            };

            const onHidden = () => {
                finish();
                if (!confirmed) resolve(false);
            };

            confirmButton.addEventListener("click", onConfirm);
            modalElement.addEventListener("hidden.bs.modal", onHidden);
            modal.show();
        });
    }

    /**
     * Bootstrap ignores hide() while the opening transition is still running.
     * Defer closing until the modal has finished appearing so a very fast
     * submit can never leave the dialog stuck open.
     */
    function closeModal(modal) {
        if (!modal) return;
        if (modal._isTransitioning) {
            const element = modal._element;
            const onShown = () => {
                element.removeEventListener("shown.bs.modal", onShown);
                modal.hide();
            };
            element.addEventListener("shown.bs.modal", onShown);
        } else {
            modal.hide();
        }
    }

    // ---------------------------------------------------------------------
    // Filters & month state
    // ---------------------------------------------------------------------
    function applyMonthBoundsToFilters() {
        const bounds = monthBounds(state.month);
        state.filters.start = bounds.start;
        state.filters.end = bounds.end;
        state.filters.category = "";
        state.filters.type = "";
        state.filters.search = "";
    }

    function syncFilterInputs() {
        const start = $("#filterStart");
        const end = $("#filterEnd");
        const category = $("#filterCategory");
        const type = $("#filterType");
        const search = $("#filterSearch");

        if (start) start.value = state.filters.start;
        if (end) end.value = state.filters.end;
        if (type) type.value = state.filters.type;
        if (search) search.value = state.filters.search;
        if (category) {
            const valid = Array.from(category.options).some(
                (option) => option.value === state.filters.category
            );
            category.value = valid ? state.filters.category : "";
            if (!valid) state.filters.category = "";
        }
    }

    function readFilterInputs() {
        state.filters = {
            start: $("#filterStart").value,
            end: $("#filterEnd").value,
            category: $("#filterCategory").value,
            type: $("#filterType").value,
            search: $("#filterSearch").value.trim(),
        };
    }

    function transactionParams() {
        const params = new URLSearchParams();
        if (state.filters.start) params.set("start", state.filters.start);
        if (state.filters.end) params.set("end", state.filters.end);
        if (!state.filters.start && !state.filters.end) {
            params.set("month", state.month);
        }
        if (state.filters.category) params.set("category", state.filters.category);
        if (state.filters.type) params.set("type", state.filters.type);
        if (state.filters.search) params.set("search", state.filters.search);
        return params;
    }

    function switchMonth(monthKey) {
        if (!monthKey) return false;
        if (monthKey > CURRENT_MONTH) {
            monthKey = CURRENT_MONTH;
        }
        if (monthKey === state.month) {
            const picker = $("#monthPicker");
            if (picker) picker.value = monthKey;
            return false;
        }

        state.month = monthKey;
        const picker = $("#monthPicker");
        if (picker) picker.value = monthKey;
        applyMonthBoundsToFilters();
        syncFilterInputs();
        updateMonthLabels();
        updateSectionHeading(state.activeSection);
        return true;
    }

    function updateMonthLabels() {
        $$(".js-month-label").forEach((element) => {
            element.textContent = monthLabel(state.month);
        });
    }

    function hasActiveFilters() {
        const bounds = monthBounds(state.month);
        return Boolean(
            state.filters.category ||
            state.filters.type ||
            state.filters.search ||
            state.filters.start !== bounds.start ||
            state.filters.end !== bounds.end
        );
    }

    function setBusy(isBusy) {
        state.loading = isBusy;
        const picker = $("#monthPicker");
        if (picker) picker.disabled = isBusy;
    }

    // ---------------------------------------------------------------------
    // Categories
    // ---------------------------------------------------------------------
    function categoriesForType(type) {
        return state.categories.filter((category) => category.type === type);
    }

    function fillCategorySelect(select, categories, { placeholder, selected = null } = {}) {
        if (!select) return;
        const options = [`<option value="">${escapeHtml(placeholder || "Choose a category")}</option>`];
        categories.forEach((category) => {
            options.push(
                `<option value="${category.id}">${escapeHtml(category.name)}</option>`
            );
        });
        select.innerHTML = options.join("");
        if (selected !== null && selected !== undefined) {
            const value = String(selected);
            if (Array.from(select.options).some((option) => option.value === value)) {
                select.value = value;
            }
        }
    }

    function renderFilterCategoryOptions() {
        const select = $("#filterCategory");
        if (!select) return;
        const current = state.filters.category;
        const expense = categoriesForType("expense");
        const income = categoriesForType("income");
        const parts = ['<option value="">All categories</option>'];

        if (expense.length) {
            parts.push('<optgroup label="Expense">');
            expense.forEach((category) => {
                parts.push(
                    `<option value="${category.id}">${escapeHtml(category.name)}</option>`
                );
            });
            parts.push("</optgroup>");
        }
        if (income.length) {
            parts.push('<optgroup label="Income">');
            income.forEach((category) => {
                parts.push(
                    `<option value="${category.id}">${escapeHtml(category.name)}</option>`
                );
            });
            parts.push("</optgroup>");
        }

        select.innerHTML = parts.join("");
        if (Array.from(select.options).some((option) => option.value === current)) {
            select.value = current;
        } else {
            state.filters.category = "";
            select.value = "";
        }
    }

    function renderCategories() {
        const container = $("#categoryList");
        const empty = $("#categoryEmpty");
        if (!container) return;

        const count = state.categories.length;
        const countLabel = $("#categoryCountLabel");
        if (countLabel) {
            countLabel.textContent = plural(count, "category", "categories");
        }

        if (!count) {
            container.innerHTML = "";
            container.classList.add("d-none");
            if (empty) empty.classList.remove("d-none");
            return;
        }

        if (empty) empty.classList.add("d-none");
        container.classList.remove("d-none");

        const sorted = [...state.categories].sort((a, b) => {
            if (a.type !== b.type) return a.type === "expense" ? -1 : 1;
            return a.name.localeCompare(b.name);
        });

        container.innerHTML = sorted
            .map((category) => {
                const color = categoryColor(category.id);
                const icon =
                    category.type === "income"
                        ? "bi-arrow-down-left"
                        : "bi-arrow-up-right";
                const txnLabel = plural(
                    Number(category.transaction_count) || 0,
                    "transaction"
                );
                return `
                    <article class="category-card">
                        <span class="category-icon" style="background:${color}1f;color:${color}">
                            ${escapeHtml(categoryInitial(category.name))}
                        </span>
                        <div class="category-copy">
                            <strong title="${escapeHtml(category.name)}">${escapeHtml(category.name)}</strong>
                            <div class="category-meta">
                                <i class="bi ${icon}"></i>
                                ${escapeHtml(category.type_display)} · ${txnLabel}
                            </div>
                        </div>
                        <div class="category-actions">
                            <button class="budget-action" type="button"
                                    data-category-edit="${category.id}"
                                    aria-label="Edit ${escapeHtml(category.name)}"
                                    title="Edit category">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="budget-action budget-action--delete" type="button"
                                    data-category-delete="${category.id}"
                                    aria-label="Delete ${escapeHtml(category.name)}"
                                    title="Delete category">
                                <i class="bi bi-trash3"></i>
                            </button>
                        </div>
                    </article>`;
            })
            .join("");
    }

    async function loadCategories() {
        state.categories = await api("/api/categories/");
        renderCategories();
        renderFilterCategoryOptions();
        syncFilterInputs();
    }

    // ---------------------------------------------------------------------
    // Summary
    // ---------------------------------------------------------------------
    function renderSummary(data) {
        const summary = data.summary;
        const budgetSummary = data.budget_summary;

        $("#summaryIncome").textContent = fmtMoney(summary.income);
        $("#summaryExpense").textContent = fmtMoney(summary.expense);
        $("#summaryNet").textContent = fmtMoney(summary.net);

        $("#summaryIncomeCount").textContent = summary.income_count
            ? `${plural(summary.income_count, "income entry", "income entries")}`
            : "No income recorded";
        $("#summaryExpenseCount").textContent = summary.expense_count
            ? `${plural(summary.expense_count, "expense")} recorded`
            : "No expenses recorded";

        const savingsBadge = $("#summarySavings");
        const rate = Number(summary.savings_rate) || 0;
        if (summary.income > 0) {
            savingsBadge.textContent = `${Math.round(rate)}% saved`;
        } else {
            savingsBadge.textContent = "No income";
        }
        savingsBadge.classList.toggle("is-negative", rate < 0);

        $("#summaryNetCount").textContent =
            summary.income > 0
                ? rate >= 0
                    ? "You kept part of your income"
                    : "Spending exceeds income"
                : "Income minus expenses";

        const alerts = (budgetSummary.over_count || 0) + (budgetSummary.warning_count || 0);
        const alertValue = $("#summaryBudgetAlerts");
        const alertMeta = $("#summaryBudgetMeta");
        const alertCard = $("#budgetAlertCard");

        if (!budgetSummary.budget_count) {
            alertValue.textContent = "Not set";
            alertMeta.textContent = "Set a budget to track limits";
        } else if (alerts > 0) {
            alertValue.textContent = plural(alerts, "alert");
            alertMeta.textContent = `${fmtMoney(budgetSummary.total_budget)} budgeted · ${fmtNumber(
                budgetSummary.healthy_count
            )} on track`;
        } else {
            alertValue.textContent = "On track";
            alertMeta.textContent = `${fmtMoney(budgetSummary.total_spent)} of ${fmtMoney(
                budgetSummary.total_budget
            )} used`;
        }
        if (alertCard) alertCard.classList.toggle("is-alert", alerts > 0);

        const navBadge = $("#navBudgetBadge");
        if (navBadge) {
            navBadge.textContent = String(alerts);
            navBadge.classList.toggle("d-none", alerts === 0);
        }
    }

    // ---------------------------------------------------------------------
    // Charts
    // ---------------------------------------------------------------------
    if (window.Chart) {
        Chart.defaults.font.family =
            "'Inter Variable', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif";
        Chart.defaults.color = "#6b7691";

        Chart.register({
            id: "doughnutCenterText",
            afterDraw(chart) {
                if (chart.config.type !== "doughnut") return;
                const dataset = chart.data.datasets[0];
                if (!dataset || !dataset.data || !dataset.data.length) return;

                const total = dataset.data.reduce(
                    (accumulator, value) => accumulator + (Number(value) || 0),
                    0
                );
                if (!total) return;

                const { ctx, chartArea } = chart;
                if (!chartArea) return;
                const centerX = (chartArea.left + chartArea.right) / 2;
                const centerY = (chartArea.top + chartArea.bottom) / 2;
                const centerGap = (chartArea.bottom - chartArea.top) * 0.13;

                ctx.save();
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillStyle = "#8b95ab";
                ctx.font = "600 10.5px 'Inter Variable', 'Inter', sans-serif";
                ctx.fillText("TOTAL SPENT", centerX, centerY - centerGap);
                ctx.fillStyle = "#17203a";
                ctx.font = "700 17px 'Inter Variable', 'Inter', sans-serif";
                ctx.fillText(fmtCompact(total), centerX, centerY);
                ctx.restore();
            },
        });
    }

    function destroyChart(name) {
        if (state.charts[name]) {
            state.charts[name].destroy();
            state.charts[name] = null;
        }
    }

    function renderCharts(data) {
        if (!window.Chart) return;

        const rows = data.categories || [];
        const expenseCanvas = $("#expenseChart");
        const expenseEmpty = $("#expenseChartEmpty");
        const legend = $("#categoryLegend");

        destroyChart("expense");
        if (!rows.length) {
            if (expenseEmpty) expenseEmpty.classList.remove("d-none");
            if (legend) {
                legend.innerHTML =
                    '<p class="legend-empty">No expense categories to show yet.</p>';
            }
        } else {
            if (expenseEmpty) expenseEmpty.classList.add("d-none");
            if (legend) {
                legend.innerHTML = rows
                    .map((row) => {
                        const color = categoryColor(row.category_id);
                        return `
                            <div class="legend-row">
                                <span class="legend-dot" style="background:${color}"></span>
                                <span class="legend-name" title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</span>
                                <span class="legend-value">
                                    ${escapeHtml(fmtMoney(row.total))}
                                    <span class="legend-pct">${Number(row.percentage).toFixed(1)}%</span>
                                </span>
                            </div>`;
                    })
                    .join("");
            }

            state.charts.expense = new Chart(expenseCanvas, {
                type: "doughnut",
                data: {
                    labels: rows.map((row) => row.name),
                    datasets: [
                        {
                            data: rows.map((row) => Number(row.total)),
                            backgroundColor: rows.map((row) =>
                                categoryColor(row.category_id)
                            ),
                            borderColor: "#ffffff",
                            borderWidth: 3,
                            hoverOffset: 6,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "68%",
                    layout: { padding: 6 },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            padding: 10,
                            cornerRadius: 8,
                            callbacks: {
                                label(context) {
                                    return ` ${context.label}: ${fmtMoney(context.parsed)}`;
                                },
                            },
                        },
                    },
                    animation: { animateRotate: true, duration: 650 },
                },
            });
        }

        const trend = data.trend || [];
        const hasTrendData = trend.some(
            (row) => Number(row.income) > 0 || Number(row.expense) > 0
        );
        const trendCanvas = $("#trendChart");
        const trendEmpty = $("#trendChartEmpty");

        destroyChart("trend");
        if (!hasTrendData) {
            if (trendEmpty) trendEmpty.classList.remove("d-none");
        } else {
            if (trendEmpty) trendEmpty.classList.add("d-none");
            state.charts.trend = new Chart(trendCanvas, {
                type: "bar",
                data: {
                    labels: trend.map((row) => row.label),
                    datasets: [
                        {
                            label: "Income",
                            data: trend.map((row) => Number(row.income)),
                            backgroundColor: "#10b981",
                            hoverBackgroundColor: "#059669",
                            borderRadius: 6,
                            borderSkipped: false,
                            maxBarThickness: 26,
                            categoryPercentage: 0.62,
                            barPercentage: 0.86,
                        },
                        {
                            label: "Expenses",
                            data: trend.map((row) => Number(row.expense)),
                            backgroundColor: "#f87171",
                            hoverBackgroundColor: "#ef4444",
                            borderRadius: 6,
                            borderSkipped: false,
                            maxBarThickness: 26,
                            categoryPercentage: 0.62,
                            barPercentage: 0.86,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: "index", intersect: false },
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: {
                                usePointStyle: true,
                                pointStyle: "circle",
                                boxWidth: 7,
                                boxHeight: 7,
                                padding: 16,
                                color: "#6b7691",
                                font: { size: 11.5, weight: "500" },
                            },
                        },
                        tooltip: {
                            padding: 10,
                            cornerRadius: 8,
                            callbacks: {
                                title(items) {
                                    if (!items.length) return "";
                                    return trend[items[0].dataIndex].label_full;
                                },
                                label(context) {
                                    return ` ${context.dataset.label}: ${fmtMoney(
                                        context.parsed.y
                                    )}`;
                                },
                            },
                        },
                    },
                    scales: {
                        x: {
                            grid: { display: false },
                            border: { display: false },
                            ticks: {
                                color: "#8b95ab",
                                font: { size: 11 },
                            },
                        },
                        y: {
                            beginAtZero: true,
                            grid: { color: "#eef1f7" },
                            border: { display: false },
                            ticks: {
                                color: "#8b95ab",
                                font: { size: 10.5 },
                                maxTicksLimit: 5,
                                callback(value) {
                                    return fmtCompactMoney(value);
                                },
                            },
                        },
                    },
                    animation: { duration: 600 },
                },
            });
        }
    }

    // ---------------------------------------------------------------------
    // Budgets
    // ---------------------------------------------------------------------
    function renderBudgets(data) {
        const budgets = data.budgets || [];
        const summary = data.budget_summary;
        const list = $("#budgetList");
        const empty = $("#budgetEmpty");
        const summaryStrip = $("#budgetSummary");
        state.budgets = budgets;

        if (summaryStrip) {
            if (!summary.budget_count) {
                summaryStrip.classList.add("d-none");
                summaryStrip.innerHTML = "";
            } else {
                const remaining = Number(summary.total_remaining);
                const remainingChip =
                    remaining >= 0
                        ? `<span class="summary-chip">Remaining <strong>${escapeHtml(
                              fmtMoney(remaining)
                          )}</strong></span>`
                        : `<span class="summary-chip summary-chip--warning">Over budget by <strong>${escapeHtml(
                              fmtMoney(Math.abs(remaining))
                          )}</strong></span>`;
                const alertChip =
                    summary.over_count + summary.warning_count > 0
                        ? `<span class="summary-chip summary-chip--warning"><i class="bi bi-exclamation-triangle"></i> ${plural(
                              summary.over_count + summary.warning_count,
                              "alert"
                          )}</span>`
                        : `<span class="summary-chip"><i class="bi bi-check2-circle"></i> All within limits</span>`;

                summaryStrip.innerHTML = `
                    <span class="summary-chip">Budgeted <strong>${escapeHtml(
                        fmtMoney(summary.total_budget)
                    )}</strong></span>
                    <span class="summary-chip">Spent <strong>${escapeHtml(
                        fmtMoney(summary.total_spent)
                    )}</strong></span>
                    ${remainingChip}
                    ${alertChip}`;
                summaryStrip.classList.remove("d-none");
            }
        }

        if (!budgets.length) {
            if (list) {
                list.innerHTML = "";
                list.classList.add("d-none");
            }
            if (empty) empty.classList.remove("d-none");
            return;
        }

        if (empty) empty.classList.add("d-none");
        if (list) {
            list.classList.remove("d-none");
            list.innerHTML = budgets
                .map((budget) => {
                    const color = categoryColor(budget.category_id);
                    const statusClass =
                        budget.status === "over"
                            ? "is-over"
                            : budget.status === "warning"
                            ? "is-warning"
                            : "";
                    const pillClass =
                        budget.status === "over"
                            ? "status-pill status-pill--over"
                            : budget.status === "warning"
                            ? "status-pill status-pill--warning"
                            : "status-pill";
                    const statusIcon =
                        budget.status === "over"
                            ? "bi-exclamation-octagon"
                            : budget.status === "warning"
                            ? "bi-exclamation-triangle"
                            : "bi-check2-circle";
                    const width = Math.max(
                        0,
                        Math.min(Number(budget.percent_used) || 0, 100)
                    );
                    const track =
                        Number(budget.remaining) >= 0
                            ? `${fmtMoney(budget.remaining)} left`
                            : `${fmtMoney(budget.over_by)} over`;

                    return `
                        <article class="budget-card ${statusClass}" data-budget-id="${budget.id}">
                            <div class="budget-card-top">
                                <span class="budget-dot" style="background:${color}"></span>
                                <span class="budget-name" title="${escapeHtml(
                                    budget.category_name
                                )}">${escapeHtml(budget.category_name)}</span>
                                <div class="budget-actions">
                                    <button class="budget-action" type="button"
                                            data-budget-edit="${budget.id}"
                                            aria-label="Edit ${escapeHtml(
                                                budget.category_name
                                            )} budget" title="Edit budget">
                                        <i class="bi bi-pencil"></i>
                                    </button>
                                    <button class="budget-action budget-action--delete" type="button"
                                            data-budget-delete="${budget.id}"
                                            aria-label="Delete ${escapeHtml(
                                                budget.category_name
                                            )} budget" title="Delete budget">
                                        <i class="bi bi-trash3"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="budget-figures">
                                <span><strong>${escapeHtml(
                                    fmtMoney(budget.spent)
                                )}</strong> of ${escapeHtml(
                        fmtMoney(budget.amount)
                    )}</span>
                                <span>${Number(budget.percent_used).toFixed(1)}%</span>
                            </div>
                            <div class="budget-bar" role="progressbar"
                                 aria-valuenow="${Number(budget.percent_used).toFixed(1)}"
                                 aria-valuemin="0" aria-valuemax="100"
                                 aria-label="${escapeHtml(
                                     budget.category_name
                                 )} budget used">
                                <span style="width:${width}%"></span>
                            </div>
                            <div class="budget-card-foot">
                                <span class="${pillClass}"><i class="bi ${statusIcon}"></i> ${escapeHtml(
                        budget.status_label
                    )}</span>
                                <span class="track-amount">${escapeHtml(track)}</span>
                            </div>
                        </article>`;
                })
                .join("");
        }
    }

    // ---------------------------------------------------------------------
    // Transactions
    // ---------------------------------------------------------------------
    function renderTransactions() {
        const tbody = $("#transactionTableBody");
        const empty = $("#transactionEmpty");
        const tableWrap = $(".table-wrap");
        if (!tbody) return;

        const count = state.transactions.length;
        const countLabel = $("#txnCountLabel");
        if (countLabel) {
            countLabel.textContent = plural(count, "transaction");
        }

        if (!count) {
            tbody.innerHTML = "";
            if (empty) empty.classList.remove("d-none");
            if (tableWrap) tableWrap.classList.add("d-none");
            const emptyText = $("#transactionEmptyText");
            if (emptyText) {
                emptyText.textContent = hasActiveFilters()
                    ? "No transactions match these filters. Try a wider date range or clear them."
                    : "No transactions in this month yet. Add your first one.";
            }
            return;
        }

        if (empty) empty.classList.add("d-none");
        if (tableWrap) {
            tableWrap.classList.remove("d-none");
            tableWrap.scrollTop = 0;
        }

        tbody.innerHTML = state.transactions
            .map((transaction) => {
                const color = categoryColor(transaction.category);
                const date = new Date(`${transaction.date}T00:00:00`);
                const day = date.toLocaleDateString(LOCALE, {
                    day: "2-digit",
                    month: "short",
                });
                const isIncome = transaction.type === "income";
                const description = transaction.description
                    ? escapeHtml(transaction.description)
                    : '<span class="text-muted fw-normal fst-italic">No description</span>';
                const sign = isIncome ? "+" : "−";

                return `
                    <tr>
                        <td class="txn-date">${escapeHtml(day)}<span>${date.getFullYear()}</span></td>
                        <td>
                            <div class="txn-desc">
                                <span class="txn-avatar" style="background:${color}1f;color:${color}">
                                    ${escapeHtml(categoryInitial(transaction.category_name))}
                                </span>
                                <div class="txn-desc-text">
                                    <strong title="${escapeHtml(
                                        transaction.description || ""
                                    )}">${description}</strong>
                                    <span>${escapeHtml(transaction.type_display)} entry</span>
                                </div>
                            </div>
                        </td>
                        <td>
                            <span class="category-chip">
                                <span class="dot" style="background:${color}"></span>
                                ${escapeHtml(transaction.category_name)}
                            </span>
                        </td>
                        <td>
                            <span class="type-chip type-chip--${transaction.type}">
                                <i class="bi bi-arrow-${isIncome ? "down-left" : "up-right"}"></i>
                                ${isIncome ? "Income" : "Expense"}
                            </span>
                        </td>
                        <td class="txn-amount txn-amount--${transaction.type}">
                            ${sign}${escapeHtml(fmtMoney(transaction.amount))}
                        </td>
                        <td>
                            <div class="row-actions">
                                <button class="row-action" type="button"
                                        data-txn-edit="${transaction.id}"
                                        aria-label="Edit transaction" title="Edit">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="row-action row-action--delete" type="button"
                                        data-txn-delete="${transaction.id}"
                                        aria-label="Delete transaction" title="Delete">
                                    <i class="bi bi-trash3"></i>
                                </button>
                            </div>
                        </td>
                    </tr>`;
            })
            .join("");
    }

    async function loadTransactions() {
        const requestId = ++txnRequestId;
        const params = transactionParams();
        const data = await api(`/api/transactions/?${params.toString()}`);
        if (requestId !== txnRequestId) return;
        state.transactions = data;
        renderTransactions();
    }

    async function loadDashboard() {
        const requestId = ++dashboardRequestId;
        const data = await api(
            `/api/analytics/dashboard/?month=${encodeURIComponent(state.month)}`
        );
        if (requestId !== dashboardRequestId) return;
        state.dashboard = data;
        updateMonthLabels();
        renderSummary(data);
        renderCharts(data);
        renderBudgets(data);
    }

    async function refreshAll() {
        try {
            await loadCategories();
            await Promise.all([loadDashboard(), loadTransactions()]);
        } catch (error) {
            handleError(error);
        }
    }

    async function refreshMonthData() {
        setBusy(true);
        try {
            await Promise.all([loadDashboard(), loadTransactions()]);
        } catch (error) {
            handleError(error);
        } finally {
            setBusy(false);
        }
    }

    // ---------------------------------------------------------------------
    // Transaction modal
    // ---------------------------------------------------------------------
    function txnModalInstance() {
        return bootstrap.Modal.getOrCreateInstance($("#transactionModal"));
    }

    function selectedTxnType() {
        const checked = document.querySelector('input[name="txnType"]:checked');
        return checked ? checked.value : "expense";
    }

    function refreshTxnCategoryOptions(selected = null) {
        const type = selectedTxnType();
        const categories = categoriesForType(type);
        const select = $("#txnCategory");
        fillCategorySelect(select, categories, {
            placeholder: categories.length ? "Choose a category" : "No categories available",
            selected,
        });
        const hint = $("#txnCategoryHint");
        if (hint) hint.classList.toggle("d-none", categories.length > 0);
        const submit = $("#txnSubmitBtn");
        if (submit) submit.disabled = categories.length === 0;
    }

    function clearFormError(element) {
        if (!element) return;
        element.classList.add("d-none");
        element.textContent = "";
    }

    function showFormError(element, message) {
        if (!element) return;
        element.textContent = message;
        element.classList.remove("d-none");
    }

    function openTransactionModal(transaction = null) {
        const form = $("#transactionForm");
        form.reset();
        clearFormError($("#txnFormError"));

        $("#txnId").value = transaction ? transaction.id : "";
        $("#transactionModalTitle").textContent = transaction
            ? "Edit transaction"
            : "Add transaction";
        $("#transactionModalSubtitle").textContent = transaction
            ? "Update the details and save your changes."
            : "Record a new income or expense entry.";
        $("#txnSubmitBtn").textContent = transaction ? "Save changes" : "Save transaction";
        $("#txnDate").max = TODAY;

        if (transaction) {
            $("#txnAmount").value = transaction.amount;
            $("#txnDate").value = transaction.date;
            $("#txnDescription").value = transaction.description || "";
            $("#txnTypeIncome").checked = transaction.type === "income";
            $("#txnTypeExpense").checked = transaction.type === "expense";
        } else {
            $("#txnAmount").value = "";
            $("#txnDescription").value = "";
            $("#txnDate").value = defaultTxnDate();
            $("#txnTypeExpense").checked = true;
            $("#txnTypeIncome").checked = false;
        }

        refreshTxnCategoryOptions(transaction ? transaction.category : null);
        txnModalInstance().show();
        window.setTimeout(() => $("#txnAmount").focus(), 250);
    }

    async function submitTransaction(event) {
        event.preventDefault();
        clearFormError($("#txnFormError"));

        const id = $("#txnId").value;
        const amount = $("#txnAmount").value;
        const category = $("#txnCategory").value;
        const date = $("#txnDate").value;
        const description = $("#txnDescription").value.trim();
        const type = selectedTxnType();

        if (!amount || Number(amount) <= 0) {
            showFormError($("#txnFormError"), "Enter an amount greater than zero.");
            return;
        }
        if (!category) {
            showFormError($("#txnFormError"), "Choose a category for this transaction.");
            return;
        }
        if (!date) {
            showFormError($("#txnFormError"), "Choose a date.");
            return;
        }
        if (date > TODAY) {
            showFormError($("#txnFormError"), "A transaction date cannot be in the future.");
            return;
        }

        const payload = {
            amount: Number(amount).toFixed(2),
            category: Number(category),
            date,
            description,
            type,
        };

        const submitButton = $("#txnSubmitBtn");
        const originalLabel = submitButton.textContent;
        submitButton.disabled = true;
        submitButton.textContent = "Saving…";

        try {
            await api(
                id ? `/api/transactions/${id}/` : "/api/transactions/",
                { method: id ? "PATCH" : "POST", body: payload }
            );

            closeModal(txnModalInstance());
            showToast(id ? "Transaction updated." : "Transaction added.", "success");

            const targetMonth = date.slice(0, 7);
            if (targetMonth !== state.month) {
                switchMonth(targetMonth);
            }
            await refreshAll();
        } catch (error) {
            if (error.status !== 401) {
                showFormError($("#txnFormError"), error.message);
            }
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = originalLabel;
        }
    }

    async function deleteTransaction(transaction) {
        if (!transaction) return;
        const label = transaction.description
            ? `“${transaction.description}”`
            : `${transaction.category_name} ${fmtMoney(transaction.amount)}`;
        const confirmed = await askConfirm({
            title: "Delete transaction?",
            message: `Remove ${label}? This cannot be undone.`,
            confirmLabel: "Delete transaction",
        });
        if (!confirmed) return;

        try {
            await api(`/api/transactions/${transaction.id}/`, { method: "DELETE" });
            showToast("Transaction deleted.", "success");
            await refreshAll();
        } catch (error) {
            handleError(error);
        }
    }

    // ---------------------------------------------------------------------
    // Category modal
    // ---------------------------------------------------------------------
    function categoryModalInstance() {
        return bootstrap.Modal.getOrCreateInstance($("#categoryModal"));
    }

    function openCategoryModal(category = null) {
        $("#categoryForm").reset();
        clearFormError($("#catFormError"));

        $("#catId").value = category ? category.id : "";
        $("#catName").value = category ? category.name : "";
        $("#catType").value = category ? category.type : "expense";

        const hasTransactions = Boolean(
            category && Number(category.transaction_count) > 0
        );
        $("#catType").disabled = hasTransactions;
        $("#catTypeHint").classList.toggle("d-none", !hasTransactions);

        $("#categoryModalTitle").textContent = category
            ? "Edit category"
            : "New category";
        $("#catSubmitBtn").textContent = category ? "Save changes" : "Create category";

        categoryModalInstance().show();
        window.setTimeout(() => $("#catName").focus(), 250);
    }

    async function submitCategory(event) {
        event.preventDefault();
        clearFormError($("#catFormError"));

        const id = $("#catId").value;
        const name = $("#catName").value.trim();
        const type = $("#catType").value;

        if (!name) {
            showFormError($("#catFormError"), "Category name cannot be blank.");
            return;
        }

        const submitButton = $("#catSubmitBtn");
        const originalLabel = submitButton.textContent;
        submitButton.disabled = true;
        submitButton.textContent = "Saving…";

        try {
            await api(id ? `/api/categories/${id}/` : "/api/categories/", {
                method: id ? "PATCH" : "POST",
                body: { name, type },
            });
            closeModal(categoryModalInstance());
            showToast(id ? "Category updated." : "Category created.", "success");
            await refreshAll();
        } catch (error) {
            if (error.status !== 401) {
                showFormError($("#catFormError"), error.message);
            }
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = originalLabel;
        }
    }

    async function deleteCategory(category) {
        if (!category) return;
        const hasTransactions = Number(category.transaction_count) > 0;
        const confirmed = await askConfirm({
            title: "Delete category?",
            message: hasTransactions
                ? `“${category.name}” has ${plural(
                      Number(category.transaction_count),
                      "transaction"
                  )} and cannot be deleted until those are removed.`
                : `Delete “${category.name}”? This cannot be undone.`,
            confirmLabel: "Delete category",
        });
        if (!confirmed) return;

        try {
            await api(`/api/categories/${category.id}/`, { method: "DELETE" });
            if (state.filters.category === String(category.id)) {
                state.filters.category = "";
            }
            showToast("Category deleted.", "success");
            await refreshAll();
        } catch (error) {
            handleError(error);
        }
    }

    // ---------------------------------------------------------------------
    // Budget modal
    // ---------------------------------------------------------------------
    function budgetModalInstance() {
        return bootstrap.Modal.getOrCreateInstance($("#budgetModal"));
    }

    function openBudgetModal(budget = null) {
        $("#budgetForm").reset();
        clearFormError($("#budgetFormError"));

        const expenseCategories = categoriesForType("expense");
        const categorySelect = $("#budgetCategory");
        fillCategorySelect(categorySelect, expenseCategories, {
            placeholder: expenseCategories.length
                ? "Choose an expense category"
                : "No expense categories",
            selected: budget ? budget.category_id : null,
        });

        $("#budgetId").value = budget ? budget.id : "";
        $("#budgetMonth").value = budget ? budget.month : state.month;
        $("#budgetMonth").max = CURRENT_MONTH;
        $("#budgetAmount").value = budget ? budget.amount : "";

        categorySelect.disabled = Boolean(budget);
        $("#budgetMonth").disabled = Boolean(budget);
        $("#budgetEditHint").hidden = !budget;
        const hint = $("#budgetHint");
        if (hint) hint.classList.toggle("d-none", expenseCategories.length > 0);

        $("#budgetModalTitle").textContent = budget ? "Edit budget" : "Set budget";
        $("#budgetSubmitBtn").textContent = budget ? "Update budget" : "Save budget";
        $("#budgetSubmitBtn").disabled = expenseCategories.length === 0;

        budgetModalInstance().show();
        window.setTimeout(() => {
            (budget ? $("#budgetAmount") : categorySelect).focus();
        }, 250);
    }

    async function submitBudget(event) {
        event.preventDefault();
        clearFormError($("#budgetFormError"));

        const id = $("#budgetId").value;
        const category = $("#budgetCategory").value;
        const month = $("#budgetMonth").value;
        const amount = $("#budgetAmount").value;

        if (!id && !category) {
            showFormError($("#budgetFormError"), "Choose an expense category.");
            return;
        }
        if (!month) {
            showFormError($("#budgetFormError"), "Choose a month.");
            return;
        }
        if (!amount || Number(amount) <= 0) {
            showFormError($("#budgetFormError"), "Enter a limit greater than zero.");
            return;
        }

        const payload = { amount: Number(amount).toFixed(2) };
        if (!id) {
            payload.category = Number(category);
            payload.month = month;
        }

        const submitButton = $("#budgetSubmitBtn");
        const originalLabel = submitButton.textContent;
        submitButton.disabled = true;
        submitButton.textContent = "Saving…";

        try {
            await api(id ? `/api/budgets/${id}/` : "/api/budgets/", {
                method: id ? "PATCH" : "POST",
                body: payload,
            });
            closeModal(budgetModalInstance());
            showToast(id ? "Budget updated." : "Budget saved.", "success");

            const targetMonth = id ? state.month : month;
            if (targetMonth !== state.month) {
                switchMonth(targetMonth);
            }
            await refreshMonthData();
        } catch (error) {
            if (error.status !== 401) {
                showFormError($("#budgetFormError"), error.message);
            }
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = originalLabel;
        }
    }

    async function deleteBudget(budget) {
        if (!budget) return;
        const confirmed = await askConfirm({
            title: "Delete budget?",
            message: `Remove the ${fmtMoney(budget.amount)} limit for “${
                budget.category_name
            }” in ${monthLabel(budget.month)}?`,
            confirmLabel: "Delete budget",
        });
        if (!confirmed) return;

        try {
            await api(`/api/budgets/${budget.id}/`, { method: "DELETE" });
            showToast("Budget deleted.", "success");
            await refreshMonthData();
        } catch (error) {
            handleError(error);
        }
    }

    // ---------------------------------------------------------------------
    // Section navigation
    // ---------------------------------------------------------------------
    const SECTION_COPY = {
        overview: () => [
            "Overview",
            `Your money at a glance for ${monthLabel(state.month)}.`,
        ],
        budgets: () => [
            "Monthly budgets",
            `Limits and alerts for ${monthLabel(state.month)}.`,
        ],
        transactions: () => [
            "Transactions",
            `Every entry matching your filters in ${monthLabel(state.month)}.`,
        ],
        categories: () => [
            "Categories",
            "Organise your income and expense labels.",
        ],
    };

    function updateSectionHeading(sectionId) {
        const copy = SECTION_COPY[sectionId];
        if (!copy) return;
        const [title, subtitle] = copy();
        const titleElement = $("#pageTitle");
        const subtitleElement = $("#pageSubtitle");
        if (titleElement) titleElement.textContent = title;
        if (subtitleElement) subtitleElement.textContent = subtitle;
    }

    function setActiveSection(sectionId) {
        state.activeSection = sectionId;
        $$("[data-section-link]").forEach((link) => {
            link.classList.toggle(
                "is-active",
                link.dataset.sectionLink === sectionId
            );
        });
        updateSectionHeading(sectionId);
    }

    function initSectionObserver() {
        const sections = ["overview", "budgets", "transactions", "categories"]
            .map((id) => document.getElementById(id))
            .filter(Boolean);
        if (!sections.length || !("IntersectionObserver" in window)) return;

        const observer = new IntersectionObserver(
            (entries) => {
                const visible = entries
                    .filter((entry) => entry.isIntersecting)
                    .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
                if (visible.length) {
                    setActiveSection(visible[0].target.id);
                }
            },
            { rootMargin: "-18% 0px -55% 0px", threshold: [0.05, 0.25, 0.5] }
        );
        sections.forEach((section) => observer.observe(section));
    }

    // ---------------------------------------------------------------------
    // Event wiring
    // ---------------------------------------------------------------------
    function wireEvents() {
        // Month selector
        const monthPicker = $("#monthPicker");
        if (monthPicker) {
            monthPicker.addEventListener("change", async () => {
                const value = monthPicker.value;
                if (!value) {
                    monthPicker.value = state.month;
                    return;
                }
                if (value > CURRENT_MONTH) {
                    monthPicker.value = state.month;
                    showToast("Future months are not available yet.", "info");
                    return;
                }
                switchMonth(value);
                await refreshMonthData();
            });
        }

        // Sidebar (mobile)
        $$("[data-sidebar-toggle]").forEach((button) =>
            button.addEventListener("click", () =>
                document.body.classList.add("sidebar-open")
            )
        );
        $$("[data-sidebar-close]").forEach((element) =>
            element.addEventListener("click", () =>
                document.body.classList.remove("sidebar-open")
            )
        );
        $$("[data-section-link]").forEach((link) =>
            link.addEventListener("click", () => {
                document.body.classList.remove("sidebar-open");
                setActiveSection(link.dataset.sectionLink);
            })
        );
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                document.body.classList.remove("sidebar-open");
            }
        });

        // Transaction modal
        $$("[data-add-transaction], #addTransactionBtn").forEach((button) =>
            button.addEventListener("click", () => openTransactionModal())
        );
        $("#transactionForm").addEventListener("submit", submitTransaction);
        $$('input[name="txnType"]').forEach((radio) =>
            radio.addEventListener("change", () => refreshTxnCategoryOptions())
        );

        $("#transactionTableBody").addEventListener("click", (event) => {
            const editButton = event.target.closest("[data-txn-edit]");
            if (editButton) {
                const transaction = state.transactions.find(
                    (item) => item.id === Number(editButton.dataset.txnEdit)
                );
                if (transaction) openTransactionModal(transaction);
                return;
            }
            const deleteButton = event.target.closest("[data-txn-delete]");
            if (deleteButton) {
                const transaction = state.transactions.find(
                    (item) => item.id === Number(deleteButton.dataset.txnDelete)
                );
                deleteTransaction(transaction);
            }
        });

        // Categories
        $("#addCategoryBtn").addEventListener("click", () => openCategoryModal());
        $("#emptyAddCategoryBtn").addEventListener("click", () => openCategoryModal());
        $("#categoryForm").addEventListener("submit", submitCategory);

        $("#categoryList").addEventListener("click", (event) => {
            const editButton = event.target.closest("[data-category-edit]");
            if (editButton) {
                const category = state.categories.find(
                    (item) => item.id === Number(editButton.dataset.categoryEdit)
                );
                if (category) openCategoryModal(category);
                return;
            }
            const deleteButton = event.target.closest("[data-category-delete]");
            if (deleteButton) {
                const category = state.categories.find(
                    (item) => item.id === Number(deleteButton.dataset.categoryDelete)
                );
                deleteCategory(category);
            }
        });

        // Budgets
        $("#addBudgetBtn").addEventListener("click", () => openBudgetModal());
        $("#emptyAddBudgetBtn").addEventListener("click", () => openBudgetModal());
        $("#budgetForm").addEventListener("submit", submitBudget);

        $("#budgetList").addEventListener("click", (event) => {
            const editButton = event.target.closest("[data-budget-edit]");
            if (editButton) {
                const budget = state.budgets.find(
                    (item) => item.id === Number(editButton.dataset.budgetEdit)
                );
                if (budget) openBudgetModal(budget);
                return;
            }
            const deleteButton = event.target.closest("[data-budget-delete]");
            if (deleteButton) {
                const budget = state.budgets.find(
                    (item) => item.id === Number(deleteButton.dataset.budgetDelete)
                );
                deleteBudget(budget);
            }
        });

        // Filters
        const filterForm = $("#filterForm");
        filterForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            readFilterInputs();
            if (
                state.filters.start &&
                state.filters.end &&
                state.filters.start > state.filters.end
            ) {
                showToast("The ‘From’ date must be before the ‘To’ date.", "error");
                return;
            }
            try {
                await loadTransactions();
            } catch (error) {
                handleError(error);
            }
        });

        $("#filterSearch").addEventListener("input", () => {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(async () => {
                state.filters.search = $("#filterSearch").value.trim();
                try {
                    await loadTransactions();
                } catch (error) {
                    handleError(error);
                }
            }, 350);
        });

        $("#filterReset").addEventListener("click", async () => {
            applyMonthBoundsToFilters();
            syncFilterInputs();
            try {
                await loadTransactions();
            } catch (error) {
                handleError(error);
            }
        });
        $("#emptyResetFilters").addEventListener("click", async () => {
            applyMonthBoundsToFilters();
            syncFilterInputs();
            try {
                await loadTransactions();
            } catch (error) {
                handleError(error);
            }
        });

        // Export
        const exportCsv = () => {
            const params = new URLSearchParams();
            params.set("month", state.month);
            if (state.filters.category) {
                params.set("category", state.filters.category);
            }
            if (state.filters.type) params.set("type", state.filters.type);
            if (state.filters.search) params.set("search", state.filters.search);
            showToast("Preparing your CSV download…", "info");
            window.location.href = `/api/transactions/export/?${params.toString()}`;
        };
        const exportButton = $("#exportBtn");
        if (exportButton) exportButton.addEventListener("click", exportCsv);
        $$("[data-export]").forEach((button) =>
            button.addEventListener("click", exportCsv)
        );
    }

    // ---------------------------------------------------------------------
    // Boot
    // ---------------------------------------------------------------------
    function applyCurrencyLabels() {
        $$("[data-currency-symbol]").forEach((element) => {
            element.textContent = CURRENCY_SYMBOL;
        });
    }

    async function init() {
        applyCurrencyLabels();
        applyMonthBoundsToFilters();
        syncFilterInputs();
        updateMonthLabels();
        wireEvents();
        initSectionObserver();

        setBusy(true);
        try {
            await loadCategories();
            await Promise.all([loadDashboard(), loadTransactions()]);
        } catch (error) {
            handleError(error);
            showPageAlert(
                (error && error.message) || "Could not load your dashboard."
            );
        } finally {
            setBusy(false);
        }
    }

    init();
})();
