# LedgerLite — Expense Tracker with Analytics

A personal finance web app built with **Django 5.2**, **Django REST Framework**, and **vanilla JavaScript + Chart.js**. Users record income and expenses, organise them by category, set monthly budgets per expense category, and view live analytics on a dashboard that refreshes through `fetch()` without page reloads.

Every total, percentage and budget status is calculated **server-side with Django ORM aggregation** (`Sum`, `Count`, `GROUP BY`, `TruncMonth`). The frontend only renders what the API returns.

---

## Feature map

| Requirement | Where it lives |
| --- | --- |
| Register, log in, log out | `accounts/` (Django auth pages + `/api/auth/` endpoints) |
| Per-user data isolation | User-scoped querysets in `tracker/views.py`; other users' objects return **404** |
| Category CRUD, unique name per user, no delete when used | `tracker/models.py`, `CategoryListCreateView`, `CategoryDetailView` |
| Transaction CRUD, amount > 0, no future dates | `tracker/serializers.py`, model check constraints |
| Filtering by date range / category / type / search | `tracker/services.py::apply_transaction_filters` |
| Monthly budgets + 80% warning + over-budget alert | `tracker/services.py::build_budget_status` |
| Monthly summary, category totals, 6-month trend | `tracker/services.py` (ORM aggregation), `/api/analytics/*` |
| Doughnut chart, 6-month bar chart, summary cards | `static/js/dashboard.js`, Chart.js 4.4 |
| Month selector with no page reload | Dashboard month picker → `fetch` dashboard + transactions |
| CSV export (bonus) | `TransactionExportView` → `/api/transactions/export/?month=YYYY-MM` |
| All models in Django Admin | `tracker/admin.py`, `/admin/` |

---

## Tech stack

- Python 3.11, Django 5.2.19, Django REST Framework 3.16.1
- SQLite (development default)
- Session authentication + CSRF for both pages and API calls
- Bootstrap 5.3.3, Bootstrap Icons, Inter, Chart.js 4.4.1 — **vendored locally** in `static/vendor/` so the app works offline
- No build step, no frontend framework

---

## Quick start

> Use the project's virtualenv (Django 5.2). If another environment such as `bkend-env` is active, run `deactivate` first, then activate `.venv`. You can also call the interpreter directly: `.venv/bin/python manage.py runserver`.

```bash
cd Final

# 1. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Apply migrations
python manage.py migrate

# 4. (Optional) Create six months of realistic demo data
python manage.py seed_demo

# 5. Run the development server
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. You will be redirected to the login page.

### Demo account

`seed_demo` creates (or refreshes) this account:

| Username | Password |
| --- | --- |
| `demo` | `demo12345` |

The demo data covers the **six months ending with the current month**, with six expense budgets per month. The current month is tuned so that one budget is in the **warning** band (≈86%) and one is **over budget** (≈115%), so all alert states are visible immediately.

Re-running `seed_demo` replaces that user's transactions, budgets and categories. Use `--keep-data` to only add missing pieces, or `--username` / `--password` to target another account.

### Admin

```bash
python manage.py createsuperuser
```

Then visit <http://127.0.0.1:8000/admin/>. `Category`, `Transaction`, and `Budget` are registered. Non-superuser staff only see rows they own.

---

## Configuration

All settings have development-safe defaults; override them with environment variables.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | insecure dev key | **Set this in production.** |
| `DJANGO_DEBUG` | `1` | `0` disables debug mode |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1],testserver` | Comma-separated host list |
| `CURRENCY_CODE` | `INR` | ISO currency used by `Intl.NumberFormat` on the frontend and exposed to templates |
| `CURRENCY_LOCALE` | `en-IN` | Number/date formatting locale |

The timezone is `Asia/Kolkata` and the server uses `USE_TZ=True`. "Today" checks and default dates use `timezone.localdate()`.

Example for a USD deployment:

```bash
DJANGO_DEBUG=0 \
DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')" \
DJANGO_ALLOWED_HOSTS=ledger.example.com \
CURRENCY_CODE=USD \
CURRENCY_LOCALE=en-US \
python manage.py runserver
```

---

## Using the app

1. **Register** or sign in with the demo account.
2. **Create categories** (e.g. Food/Expense, Salary/Income). Names are unique per user, case-insensitively.
3. **Add transactions** with an amount, category, date and optional description. The transaction type follows the category type.
4. **Set budgets** for expense categories, per month. The server returns `status`, `percent_used`, `spent`, `remaining` and `over_by`.
5. **Switch months** with the picker in the top bar. Cards, both charts, budgets and the transaction table update via `fetch()` — the URL never changes.
6. **Filter transactions** by search text, date range, category and type. Filters apply through the API.
7. **Export CSV** for the selected month (export respects active category/type/search filters).

Budget colouring: green `ok`, amber `warning` at ≥ 80%, red `over` above 100%.

---

## API reference

Base URL: `/api/`
Authentication: Django **session cookie**. Authenticated API calls that modify data must include the CSRF token (`X-CSRFToken` header or `csrfmiddlewaretoken` form field).

Unauthenticated requests to protected endpoints return `401`/`403`. Requests for another user's object return `404` — the querysets are scoped to `request.user`.

### Authentication

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/auth/register/` | Create a user and log in |
| `POST` | `/api/auth/login/` | Log in |
| `POST` | `/api/auth/logout/` | Log out (`204`) |
| `GET` | `/api/auth/me/` | Current user |

Register payload:

```json
{
  "username": "alice",
  "email": "alice@example.com",
  "password": "V3ry-Str0ng-Pass!",
  "password_confirm": "V3ry-Str0ng-Pass!"
}
```

Login payload:

```json
{ "username": "alice", "password": "V3ry-Str0ng-Pass!" }
```

Both return the user object:

```json
{
  "id": 3,
  "username": "alice",
  "email": "alice@example.com",
  "first_name": "",
  "last_name": "",
  "display_name": "alice"
}
```

### Categories

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/categories/` | List own categories (with `transaction_count`) |
| `POST` | `/api/categories/` | Create |
| `GET` | `/api/categories/{id}/` | Retrieve |
| `PATCH` / `PUT` | `/api/categories/{id}/` | Update |
| `DELETE` | `/api/categories/{id}/` | Delete (only if unused) |

Create payload:

```json
{ "name": "Food", "type": "expense" }
```

`type` is `"income"` or `"expense"`. Responses include `type_display`, `transaction_count`, `created_at`.

Validation rules:

- Name is required and unique per user (case-insensitive).
- A category with transactions cannot be deleted (`400`).
- The type of a category with transactions cannot be changed (`400`).
- Budgets belonging to a deleted category are removed with it.

### Transactions

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/transactions/` | List own transactions |
| `POST` | `/api/transactions/` | Create |
| `GET` | `/api/transactions/{id}/` | Retrieve |
| `PATCH` / `PUT` | `/api/transactions/{id}/` | Update |
| `DELETE` | `/api/transactions/{id}/` | Delete |
| `GET` | `/api/transactions/export/` | CSV download |

Query filters (all optional, combinable):

| Parameter | Format | Meaning |
| --- | --- | --- |
| `month` | `YYYY-MM` | Whole calendar month |
| `start` | `YYYY-MM-DD` | Inclusive lower bound |
| `end` | `YYYY-MM-DD` | Inclusive upper bound |
| `category` | integer id | Only that category |
| `type` | `income` / `expense` | Only that type |
| `search` | text | Case-insensitive match on description |

Create payload:

```json
{
  "amount": "120.50",
  "category": 4,
  "date": "2026-11-05",
  "description": "Weekly groceries",
  "type": "expense"
}
```

Response:

```json
{
  "id": 101,
  "amount": 120.5,
  "category": 4,
  "category_name": "Food",
  "category_type": "expense",
  "date": "2026-11-05",
  "description": "Weekly groceries",
  "type": "expense",
  "type_display": "Expense",
  "created_at": "2026-11-05T09:12:44Z",
  "updated_at": "2026-11-05T09:12:44Z"
}
```

Validation rules:

- `amount` must be greater than zero.
- `date` cannot be in the future.
- `type` must match the selected category's type.
- The category must belong to the requesting user.

Filtering examples:

```bash
curl -b cookies.txt "http://127.0.0.1:8000/api/transactions/?month=2026-11"
curl -b cookies.txt "http://127.0.0.1:8000/api/transactions/?start=2026-11-01&end=2026-11-15"
curl -b cookies.txt "http://127.0.0.1:8000/api/transactions/?type=expense&category=4"
```

Malformed values (`month=2026-13`, `start=nope`, `type=transfer`, non-numeric category) return `400` with a readable message.

### Budgets

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/budgets/?month=YYYY-MM` | List budgets for a month |
| `POST` | `/api/budgets/` | Create **or update** the budget for that category + month |
| `GET` | `/api/budgets/{id}/` | Retrieve |
| `PATCH` / `PUT` | `/api/budgets/{id}/` | Update amount (category/month are fixed in the UI) |
| `DELETE` | `/api/budgets/{id}/` | Delete |

`POST` payload:

```json
{ "category": 4, "month": "2026-11", "amount": "6000.00" }
```

`POST` is an **upsert**: the first call for a category/month returns `201`, repeating it with a new amount updates the existing row and returns `200`. A month sent as `YYYY-MM-DD` is normalised to the first day.

Each budget row carries server-calculated fields:

```json
{
  "id": 12,
  "category": 4,
  "category_name": "Food",
  "month": "2026-11",
  "amount": 6000.0,
  "spent": 5270.0,
  "remaining": 730.0,
  "over_by": 0.0,
  "percent_used": 87.83,
  "status": "warning",
  "status_label": "Approaching limit",
  "created_at": "2026-11-01T05:00:00Z",
  "updated_at": "2026-11-17T08:20:00Z"
}
```

#### Budget status rules

| Condition | `status` | Display |
| --- | --- | --- |
| `percent_used > 100` | `over` | Over budget |
| `80 <= percent_used <= 100` | `warning` | Approaching limit |
| `percent_used < 80` | `ok` | On track |

Budgets may only reference **expense** categories, and only one budget may exist per `(user, category, month)`.

### Analytics

All analytics are computed in `tracker/services.py` with ORM aggregation — no Python loops over rows for totals.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/analytics/summary/?month=YYYY-MM` | Income, expense, net, savings rate, counts |
| `GET` | `/api/analytics/categories/?month=YYYY-MM` | Expense totals per category |
| `GET` | `/api/analytics/trend/?month=YYYY-MM&months=6` | Income vs expense per month (zero-filled) |
| `GET` | `/api/analytics/dashboard/?month=YYYY-MM` | Everything the dashboard needs in one request |

Without `month`, endpoints default to the current month and return `month` + `month_label`.

**Summary**

```json
{
  "month": "2026-11",
  "month_label": "November 2026",
  "income": 73200.0,
  "expense": 30570.0,
  "net": 42630.0,
  "savings_rate": 58.24,
  "transaction_count": 10,
  "income_count": 2,
  "expense_count": 8
}
```

**Category totals** (`percentage` is the share of that month's total expenses):

```json
{
  "month": "2026-11",
  "month_label": "November 2026",
  "categories": [
    { "category_id": 4, "name": "Rent", "total": 18000.0, "count": 1, "percentage": 58.88 },
    { "category_id": 3, "name": "Food", "total": 5270.0, "count": 3, "percentage": 17.24 }
  ]
}
```

**Trend** returns exactly `months` buckets (default 6, allowed 1–24), ending at the selected month, zero-filled for empty months:

```json
{
  "end_month": "2026-11",
  "months": 6,
  "trend": [
    {
      "month": "2026-06",
      "label": "Jun 26",
      "label_full": "June 2026",
      "income": 65000.0,
      "expense": 31970.0,
      "net": 33030.0,
      "count": 9
    }
  ]
}
```

**Dashboard** combines summary, category totals, budget rows, budget summary and trend:

```json
{
  "month": "2026-11",
  "month_label": "November 2026",
  "summary": { "...": "..." },
  "categories": [ "..." ],
  "budgets": [ "..." ],
  "budget_summary": {
    "month": "2026-11",
    "budget_count": 6,
    "total_budget": 33083.0,
    "total_spent": 30570.0,
    "total_remaining": 2513.0,
    "warning_count": 1,
    "over_count": 1,
    "healthy_count": 4,
    "unbudgeted_spent": 0.0
  },
  "trend": [ "..." ],
  "generated_at": "2026-11-17T09:20:31.412345+00:00"
}
```

`budget_summary.total_spent` counts spending only in budgeted categories; `unbudgeted_spent` reports the rest.

### CSV export

```bash
curl -b cookies.txt -OJ "http://127.0.0.1:8000/api/transactions/export/?month=2026-11"
```

- Defaults to the **current month** when no `month` / `start` / `end` is given.
- Respects `category`, `type` and `search`.
- Filename: `transactions-YYYY-MM.csv` (or `transactions-custom-range.csv`).
- Columns: `Date, Type, Category, Description, Amount`.
- UTF-8 with a BOM so Excel opens non-ASCII descriptions correctly.

### Example session from curl

```bash
# 1. Fetch the login page to get a CSRF cookie
curl -c cookies.txt http://127.0.0.1:8000/accounts/login/ -o /dev/null

# 2. Log in
TOKEN=$(awk '$6 == "csrftoken" {print $7}' cookies.txt)
curl -b cookies.txt -c cookies.txt -X POST http://127.0.0.1:8000/accounts/login/ \
  -H "Referer: http://127.0.0.1:8000/accounts/login/" \
  -d "username=demo&password=demo12345&csrfmiddlewaretoken=$TOKEN"

# 3. Call the API with the session cookie
curl -b cookies.txt "http://127.0.0.1:8000/api/analytics/dashboard/?month=2026-11"
```

---

## Testing

```bash
python manage.py test
```

The suite contains **56 tests** covering:

- Authentication: protected pages/APIs, register/login/logout flows, weak passwords, duplicate usernames
- Isolation: other users' categories, transactions and budgets return 404; analytics never include other users' rows
- Categories: unique-per-user names, delete protection, type changes, budget cascade
- Transactions: amount/date/type/category validation, filters across month/date range/category/type/search, malformed filters
- Budgets: upsert behaviour, month normalisation, thresholds at 79.99% / 80% / 100% / 100.01%, summary counts, permissions
- Analytics: summary, category percentages, zero-filled six-month trend, dashboard composition, invalid months
- CSV export: content, filename, filtering, current-month default, authentication
- Models and admin: check constraints, month normalisation, admin registration

A Puppeteer end-to-end pass (kept outside the repo) additionally verifies create/edit/delete flows for categories, transactions and budgets, filtering, month switching without reloads, desktop/mobile overflow, and the mobile sidebar.

---

## Project structure

```
Final/
├── config/
│   ├── settings.py            # apps, DRF, auth, currency, static
│   ├── urls.py                # pages, auth pages, /api/auth/, /api/
│   └── context_processors.py  # site name + currency in templates
├── accounts/
│   ├── forms.py               # styled login/register forms
│   ├── views.py               # page auth views
│   ├── serializers.py         # register/login/user serializers
│   ├── api.py                 # /api/auth/* endpoints
│   └── urls.py, api_urls.py
├── tracker/
│   ├── models.py              # Category, Transaction, Budget + constraints
│   ├── services.py            # filters, ORM analytics, budget status, trend
│   ├── serializers.py         # validation, computed budget fields
│   ├── views.py               # CRUD, budgets upsert, analytics, CSV
│   ├── urls.py
│   ├── admin.py
│   ├── tests.py               # 56 tests
│   └── management/commands/seed_demo.py
├── templates/
│   ├── base.html
│   ├── accounts/              # auth layout, login, register
│   └── tracker/dashboard.html # dashboard, modals, tables
├── static/
│   ├── css/app.css            # design system + responsive layout
│   ├── js/dashboard.js        # fetch client, charts, CRUD, filters
│   ├── favicon.svg
│   └── vendor/                # Bootstrap, Icons, Inter, Chart.js
├── manage.py
├── requirements.txt
└── README.md
```

---

## Design decisions

- **Server-owned analytics.** The dashboard calls `/api/analytics/dashboard/` and renders the response. Percentages, spend totals and statuses are never recomputed in JavaScript.
- **One request per month switch.** Dashboard data and the transaction list load in parallel via `Promise.all`; categories load once at boot.
- **Session auth + CSRF.** The dashboard reads the `csrftoken` cookie and sends `X-CSRFToken` on mutating fetches. The CSRF cookie is intentionally readable by JS (`CSRF_COOKIE_HTTPONLY=False`).
- **404 for foreign objects.** Querysets are always filtered by `request.user`, so another user's IDs reveal nothing.
- **Money as JSON numbers.** `COERCE_DECIMAL_TO_STRING=False` lets Chart.js and the cards consume amounts directly; the database still stores `Decimal`.
- **Budgets are upserts.** The UI can save a limit without first knowing whether one exists; the API returns `201` on create and `200` on update.
- **Local assets.** No CDN dependency, so the app renders correctly offline and in restricted networks.
- **Demo data includes edge cases.** The seeded current month deliberately includes a warning and an over-budget category.

---

## Troubleshooting

- **`TypeError: CheckConstraint.__init__() got an unexpected keyword argument 'condition'`** — you are running the project with an older Django (for example a global Python 3.9 environment) instead of the project virtualenv. Activate it first: `source .venv/bin/activate`, then `python manage.py runserver`. The settings file also detects Django < 5.2 and prints this hint.
- **`403 CSRF verification failed` on API writes** — reload the dashboard so the `csrftoken` cookie is set, and send it as `X-CSRFToken`. The bundled JS does this automatically.
- **Dashboard is empty** — sign in and ensure the selected month has transactions. Use `python manage.py seed_demo` for instant data.
- **Static files look unstyled** — run with `DEBUG=1` (the development server serves `static/` automatically). For production, run `python manage.py collectstatic` and serve `staticfiles/`.
- **Reset everything** — stop the server, delete `db.sqlite3`, then `python manage.py migrate && python manage.py seed_demo`.
- **Currency shows the wrong symbol** — set `CURRENCY_CODE` / `CURRENCY_LOCALE` before starting the server.

---

Built with Django and Chart.js. No trackers, no external services, and every account only ever sees its own ledger.
