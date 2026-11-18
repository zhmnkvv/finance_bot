# 💰 Finance Bot

**A personal finance tracker that lives in Telegram.** Record income and expenses in a couple of taps, see your
balance and spending charts, set monthly budgets, keep track of debts, and get payment reminders. No AI, no
paid APIs: the only external service is the Telegram Bot API.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-26A5E4?logo=telegram&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00)
![Alembic](https://img.shields.io/badge/migrations-Alembic-6BA81E)
![Database](https://img.shields.io/badge/DB-SQLite%20%7C%20PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Tests](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)

> The bot's interface is in **Russian**. The code, comments and this documentation describe the project in English.
> The default currency is the Kazakhstani tenge (KZT, ₸).

---

## Table of Contents

- [Project Overview](#project-overview)
- [Features](#features)
- [Screenshots](#screenshots)
- [Tech Stack](#tech-stack)
- [APIs and Integrations](#apis-and-integrations)
- [Project Architecture](#project-architecture)
- [Database](#database)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Running the Bot](#running-the-bot)
- [Docker](#docker)
- [Testing](#testing)
- [Security](#security)
- [Future Improvements](#future-improvements)
- [License](#license)

---

## Project Overview

Finance Bot is a Telegram bot for everyday money tracking. It is built for people who want to know where their
money goes without installing a separate app or filling in spreadsheets.

**Problems it solves:**

- **Logging spending is tedious.** An expense takes a few taps, or a single line such as `500 кофе` ("500 coffee").
- **"How much do I have left?"** The balance screen shows your all-time balance, this month's income and expenses,
  the remaining budget and your biggest spending category.
- **Overspending goes unnoticed.** Monthly budgets per category warn you at 50%, 80% and 100%.
- **Forgotten debts and bills.** Debts with due dates and recurring payment reminders arrive as Telegram messages.

Everything is driven by buttons, commands and simple, predictable parsing rules. No message is sent to any AI
service.

---

## Features

### 💸 Income and expenses
- Step-by-step flow: **amount → category → optional comment → confirmation**.
- The date can be changed before saving ("yesterday", `15.11`, `15.11.2026`). Future dates are rejected.
- **Cancel at any step** with the ❌ button, `/cancel`, or by typing "отмена".
- **Quick one-line input**, no buttons needed: `500 кофе` (expense), `+150000 зарплата` (income), `2.5к такси`
  (2,500). The category is guessed from a keyword dictionary; if no keyword matches, the bot asks you to pick one.
- 10 expense categories (Food, Transport, Shopping, Entertainment, Health, Education, Housing, Subscriptions,
  Transfers, Other) and 7 income categories (Salary, Scholarship, Freelance, Business, Gifts, Investments, Other).

### ✏️ Editing and deleting
- Every transaction has a card where you can change the **amount, category, comment or date**.
- Deleting asks for confirmation.
- Repeated or stale button presses are handled safely: no duplicates, no errors.

### 💱 Multiple currencies
- Supported currencies: **KZT, USD, EUR, RUB**. The main currency is chosen in Settings.
- Each transaction stores the currency it was recorded in. New transactions use the current main currency.
- Amounts in different currencies are **never mixed or converted**. There is no exchange-rate API and no invented
  rate. Balances in other currencies are shown separately.

### 💰 Balance
- **Current balance**: all-time income minus expenses, in the main currency.
- **This month**: income, expenses and the net result.
- **Budget**: the remaining amount across all budgets.
- **Top spending category** of the month with its share of total spending.

### 📊 Statistics and charts
- Income, expenses and their difference for **today, this week, this month and this year**. Periods follow the
  calendar in the user's time zone, and weeks start on Monday.
- **Expense breakdown by category** and the **top-5 categories** for the current month.
- **Month-over-month comparison**: this month vs. the previous one, in total and per category.
- Charts are rendered with **matplotlib** and sent as images: a donut chart of spending by category and a bar chart of
  income vs. expenses over the last 6 months.

### 📁 History
- Filters: all / income only / expenses only, **by category**, and **by period** (today, week, month, year, all
  time, or a custom range such as `01.11 - 15.11`).
- **Pagination**, 8 transactions per page. Each transaction is a button that opens its card.

### 🎯 Budgets
- A **monthly limit per expense category** in the main currency, with a progress bar.
- When you save or edit an expense, the bot warns you at **50%** (info), **80%** (warning) and **100%** (limit
  reached or exceeded). Each threshold is sent **once per month**, and the counters start fresh every month.
- Limits can be changed or deleted. Changing a limit re-arms its thresholds.

### 💳 Debts
- Two directions: **"I owe"** and **"Owed to me"**.
- Each debt has a person, amount, date, optional due date and comment.
- **Partial repayments** with a full repayment history, and a one-tap **full repayment** that closes the debt.
- Active and closed lists, plus totals per currency on the debts screen.
- **Due-date reminders**: 3 days before, 1 day before, on the due date, and every 3 days while overdue, at a time you
  choose in Settings.

### 🔔 Payment reminders
- Types: internet, rent, subscription, debt repayment, salary, or a custom title.
- Optional amount, first date and time, repeating **once, weekly or monthly**. A monthly reminder set for the 31st
  falls on the last day of shorter months.
- Times are kept in the **user's local time zone** and survive a time-zone change.
- A delivered reminder has buttons: **record it as a transaction** (if it has an amount), **done**, and **snooze for
  1 hour** (one-time reminders only).

### ⚙️ Settings
- Main currency, time zone (presets plus manual IANA input such as `Europe/Moscow`), notifications on/off, and the
  time of day for debt reminders.

### 🔒 User data safety
- Every record belongs to one Telegram user, and every query is filtered by that user. Another user's records cannot
  be opened, edited or deleted, even by crafting button data.
- Money is stored as exact decimals, never floats.
- See [Security](#security) for details.

---

## Screenshots

These are real screenshots of the running bot. The user's profile photo is pixelated; nothing else was changed.

| Main menu | Adding an expense |
|---|---|
| ![Main menu with all sections](docs/screenshots/main-menu.png) | ![Expense amount entered, the bot asks for an optional comment](docs/screenshots/add-expense-comment-step.png) |
| `/start` opens the inline main menu: balance, add income/expense, statistics, history, budgets, debts, reminders and settings. | After the amount (2,790 ₸) and the category, the bot asks for an optional comment, with **Skip** and **Cancel** buttons. |

**Statistics: expenses by category**

![Expense breakdown by category with top-5 list and a donut chart](docs/screenshots/expense-statistics-chart.png)

The text summary (total, per-category amounts and shares, top-5) is followed by a matplotlib chart sent as an image.

> Screenshots of the balance, budgets, debts and reminders screens have not been added yet.

---

## Tech Stack

| Area | Technology |
|---|---|
| Language | Python 3.11+ (developed and tested on 3.14; the Docker image uses 3.13) |
| Telegram framework | [aiogram 3](https://docs.aiogram.dev/): routers, inline keyboards, `CallbackData`, FSM |
| HTTP client (Telegram) | aiohttp, used internally by aiogram |
| ORM | SQLAlchemy 2 (async) |
| Migrations | Alembic |
| Databases | SQLite via `aiosqlite` (local runs and tests); PostgreSQL via `asyncpg` (production, Docker) |
| Validation and settings | Pydantic 2, pydantic-settings |
| Charts | matplotlib (headless `Agg` backend) |
| Time zones | `zoneinfo` + `tzdata` |
| Background jobs | Built-in asyncio scheduler (30-second tick) with all state stored in the database |
| Tests | pytest, pytest-asyncio |
| Containers | Dockerfile + Docker Compose (PostgreSQL 17) |

---

## APIs and Integrations

### Summary

The project uses **exactly one external API: the Telegram Bot API.** There are no currency-rate APIs, no AI or
LLM services, no analytics and no other third-party HTTP calls. Charts are drawn locally with matplotlib. The code
contains no direct `requests`, `httpx` or `urllib` calls; all network traffic goes through aiogram.

### Telegram Bot API

| | |
|---|---|
| **What it is** | Telegram's HTTPS API for bots (`https://api.telegram.org/bot<token>/<method>`) |
| **Used for** | Receiving user messages and button presses; sending replies, menus, charts and reminders |
| **Update delivery** | **Long polling** (`getUpdates`) via `Dispatcher.start_polling`. No webhook is used. Only `message` and `callback_query` updates are requested |
| **Methods called** | `getMe`, `setMyCommands`, `getUpdates`, `sendMessage`, `editMessageText`, `editMessageReplyMarkup`, `answerCallbackQuery`, `sendPhoto` |
| **Data sent to Telegram** | Reply texts (HTML parse mode), inline keyboards, PNG chart images, command list |
| **Data received** | Text messages and callback queries, including the sender's Telegram user id, chat id and first name |
| **Implementation** | `finance_bot/bot/app.py` creates `aiogram.Bot` and `Dispatcher` and starts polling. Handlers live in `finance_bot/bot/handlers/`. Outgoing notifications go through `finance_bot/bot/notifier.py`, which handles flood control (`retry_after`), blocked users and network errors |
| **Token required** | **Yes**, a bot token from [@BotFather](https://t.me/BotFather) |
| **Configured in** | `BOT_TOKEN` in `.env` (see [`.env.example`](.env.example)), loaded by `finance_bot/config.py` as a `SecretStr` |

### Telegram Bot API vs. aiogram vs. business logic

- **Telegram Bot API** is the remote HTTP service run by Telegram. It knows nothing about finances; it only delivers
  messages and button presses and displays what the bot sends back.
- **aiogram** is the Python library that talks to that API. It handles polling, parses updates into typed objects,
  routes them to handler functions, keeps the per-user conversation state (FSM) and sends API requests.
- **Business logic** is this project's own code in `finance_bot/services/`. It covers transactions, balances,
  statistics, budgets, debts and reminders. It works only with the database and Pydantic schemas and does not import
  aiogram, so it is unit-tested without Telegram.

The handlers in `finance_bot/bot/handlers/` connect these layers. They read the user's input from aiogram, call a
service, and format the result as a Telegram message.

---

## Project Architecture

```
Telegram ⇄ Telegram Bot API ⇄ aiogram (polling, routing, FSM)
                                   │
                    bot/handlers  (thin: parse input → call service → render reply)
                                   │
                    services/     (business logic, no Telegram code)
                                   │
                    db/ (SQLAlchemy models)  ⇄  SQLite / PostgreSQL
```

```
finance_bot/
├── __main__.py               # entry point: python -m finance_bot
├── config.py                 # settings from .env (only BOT_TOKEN is required)
├── logging_setup.py
├── bot/
│   ├── app.py                # builds Bot + Dispatcher, runs migrations, polling and the scheduler
│   ├── handlers/
│   │   ├── menu.py           # /start, main menu, cancel, balance
│   │   ├── transactions.py   # add (step-by-step and one-line), transaction card, edit, delete
│   │   ├── history.py        # filtered, paginated history
│   │   ├── stats.py          # statistics and charts
│   │   ├── budgets.py        # budget list, create, change, delete
│   │   ├── debts.py          # debts, repayments
│   │   ├── reminders.py      # payment reminders and their buttons
│   │   ├── settings.py       # currency, time zone, notifications
│   │   ├── fallback.py       # unknown input and stale buttons
│   │   ├── errors.py         # global error handler (logs the traceback, shows a friendly message)
│   │   └── common.py         # shared helpers
│   ├── keyboards.py          # inline/reply keyboards and CallbackData schemas
│   ├── states.py             # FSM states of the multi-step flows
│   ├── texts.py              # message templates (HTML, user input is escaped)
│   ├── middlewares.py        # one DB session + current user per update
│   ├── notifier.py           # safe message sending (flood control, blocked users)
│   └── scheduler.py          # background loop: payment and debt reminders
├── services/
│   ├── transactions.py       # idempotent create, update, delete, history
│   ├── stats.py              # totals, balances, category breakdown, monthly series
│   ├── budgets.py            # budgets and 50/80/100% alerts
│   ├── debts.py              # debts and repayments
│   ├── reminders.py          # reminder scheduling (once / weekly / monthly)
│   ├── charts.py             # matplotlib → PNG bytes
│   ├── quick_input.py        # rule-based "500 coffee" parser
│   ├── categories.py  settings.py  users.py
├── schemas/finance.py        # Pydantic input schemas
├── db/
│   ├── models.py             # SQLAlchemy models (UTC datetimes, Decimal money)
│   ├── seed.py               # built-in categories
│   └── session.py            # engine, sessions, running migrations
└── utils/
    ├── money.py              # amount parsing and formatting (Decimal)
    └── dates.py              # time zones, calendar periods, date parsing
migrations/                   # Alembic environment and revisions
tests/                        # pytest suite
docs/screenshots/             # README images
```

---

## Database

### Supported databases

- **SQLite** (`sqlite+aiosqlite://…`): the default for local runs. Tests use an in-memory SQLite database.
- **PostgreSQL** (`postgresql+asyncpg://…`): intended for production. `docker-compose.yml` starts PostgreSQL 17.
  An opt-in PostgreSQL test suite exists, but it has **not yet been run** against a real server (see
  [Testing](#testing)).

### Schema

```mermaid
erDiagram
    users ||--|| user_settings : has
    users ||--o{ transactions : owns
    users ||--o{ budgets : owns
    users ||--o{ debts : owns
    users ||--o{ reminders : owns
    users ||--o{ categories : "custom (optional)"
    categories ||--o{ transactions : classifies
    categories ||--o{ budgets : limits
    budgets ||--o{ budget_alerts : "sent thresholds"
    debts ||--o{ debt_payments : "repaid by"
    debts |o--o{ reminders : "may be linked"
```

| Table | Purpose | Key columns |
|---|---|---|
| `users` | Telegram user | `telegram_id` (unique), `chat_id`, `first_name`, `is_active` |
| `user_settings` | Per-user preferences | `currency`, `timezone`, `notifications_enabled`, `debt_reminder_time` |
| `categories` | Income/expense categories | `kind`, `code`, `name`, `emoji`; `user_id = NULL` means built-in |
| `transactions` | Income and expenses | `kind`, `amount NUMERIC(14,2)`, `currency`, `category_id`, `comment`, `occurred_at`, `client_token` |
| `budgets` | Monthly limit per category and currency | `category_id`, `amount`, `currency` (unique per user) |
| `budget_alerts` | Thresholds already sent | `budget_id`, `period` (`YYYY-MM`), `threshold` (50/80/100) |
| `debts` | Debts | `direction`, `person`, `amount`, `currency`, `opened_on`, `due_on`, `status`, `last_reminded_on` |
| `debt_payments` | Repayment history | `debt_id`, `amount`, `paid_at` |
| `reminders` | Payment reminders | `kind`, `title`, `amount`, `repeat`, `local_time`, `anchor_day`, `next_run_at`, `is_active` |

**Design rules:**

- **Money** is stored as `NUMERIC(14,2)` and handled as Python `Decimal`. Storing a `float` raises an error.
- **Timestamps** are stored in UTC (`timestamptz`). Periods such as "today" or "this month" are computed in the
  user's time zone.
- **Idempotency.** `client_token` columns with unique constraints guarantee that a double-tapped "Save" creates
  only one transaction, debt, repayment or reminder.

### Migrations (Alembic)

- `migrations/versions/0001_finance_schema.py` creates the schema and seeds the built-in categories.
- **Migrations run automatically when the bot starts.** You can also run them by hand with `alembic upgrade head`.
  Running them again on an up-to-date database is a no-op.
- The project started as an "AI Planner" bot. When the migration finds that older schema, it keeps the old data in
  `legacy_planner_*` tables instead of dropping it, and carries users and their time zones over to the new schema.

---

## Installation

1. **Install Python 3.11 or newer** from [python.org](https://www.python.org/downloads/).
2. **Clone the repository:**

   ```bash
   git clone https://github.com/zhmnkvv/finance_bot.git
   cd finance_bot
   ```

3. **Create a virtual environment and install the dependencies.**

   Windows (PowerShell):

   ```powershell
   py -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

   > On Windows, a bare `python` command is often the Microsoft Store stub, which silently does nothing. Use `py` to
   > create the environment and `.venv\Scripts\python.exe` to run the project.

   Linux / macOS:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```

`requirements.txt` holds the runtime dependencies. `requirements-dev.txt` adds pytest.

---

## Environment Variables

Copy the template and fill in your own values:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | **yes** | — | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | no | `sqlite+aiosqlite:///./finance.db` | SQLAlchemy async URL, e.g. `postgresql+asyncpg://USER:PASSWORD@localhost:5432/finance` |
| `DEFAULT_TIMEZONE` | no | `Asia/Qyzylorda` | IANA time zone assigned to new users |
| `DEFAULT_CURRENCY` | no | `KZT` | Main currency for new users: `KZT`, `USD`, `EUR` or `RUB` |
| `LOG_LEVEL` | no | `INFO` | Python logging level |

See [`.env.example`](.env.example). It contains only placeholders. **Never commit `.env`**; it is listed in
`.gitignore`.

---

## Running the Bot

```powershell
# Windows
.venv\Scripts\python.exe -m finance_bot
```

```bash
# Linux / macOS (with the virtual environment activated)
python -m finance_bot
```

On startup the bot:

1. applies database migrations (`alembic upgrade head`) and creates the built-in categories;
2. registers its command menu (`/menu`, `/balance`, `/cancel`, `/help`);
3. starts long polling and the reminder scheduler.

**Check that it works:** send `/start` to your bot. For every incoming message the log shows a line like this:

```
INFO    aiogram.event: Update id=… is handled. Duration 534 ms by bot id=…
```

If no such line appears, updates are not reaching this process. Possible causes: the bot is not running, another
instance is polling with the same token, or a webhook is set for that token.

The bot replies only while this process is running.

---

## Docker

The repository contains a `Dockerfile` (Python 3.13 slim, non-root user) and a `docker-compose.yml` that starts
**PostgreSQL 17** with a health check and the bot with `restart: unless-stopped`. The bot waits until the database is
healthy. Migrations run automatically when the bot container starts.

```bash
docker compose up -d --build
docker compose logs -f bot
docker compose down
```

The database password defaults to `finance`. To change it, set `POSTGRES_PASSWORD` in your environment or in `.env`.
Data is kept in the `pgdata` volume.

> ⚠️ **Not yet verified.** The Docker setup has not been built or run so far, because Docker was not available on
> the development machine. Please report any problems.

---

## Testing

```bash
python -m pytest -q
```

Latest local run (Python 3.14, Windows): **111 passed, 4 skipped.** The 4 skipped tests are the PostgreSQL suite,
which runs only when a test database is configured. **It has not been run yet.**

| Test file | What it covers |
|---|---|
| `test_money_dates.py` | Amount parsing (rejects negative, zero and over-precise amounts), money formatting, dates, periods, quick one-line input |
| `test_transactions.py` | Adding income and expenses, editing, deleting, balance vs. monthly totals, statistics, month comparison, currencies, history filters and pagination, user isolation |
| `test_budgets_debts_reminders.py` | Budgets and 50/80/100% alerts, partial and full debt repayment, due-date reminders, reminder scheduling, time-zone changes, isolation |
| `test_edge_cases.py` | Four currencies at once, local-midnight and week/month/year boundaries, budgets across a month change, effects of editing and deleting, cent-exact repayments |
| `test_bot_ui.py` | End-to-end runs through the real aiogram `Dispatcher` with a fake HTTP session: all main flows, double-tap protection, cancel, invalid input, forged callback data, HTML escaping, a simulated database error. Each callback must be answered exactly once |
| `test_migrations.py` | A fresh install matches the models; upgrading an old AI Planner database keeps its data |
| `test_user_race.py` | Concurrent first messages of a new user do not fail |
| `test_postgres.py` | *(opt-in)* Migrations, legacy upgrade, sequences and indexes, and business logic on a real PostgreSQL server |

**Running the PostgreSQL suite.** Create a dedicated database whose name ends in `_test`; the suite wipes its
`public` schema. Then put the connection URL in `.env.test`, which is git-ignored:

```bash
createdb -U postgres finance_test
echo TEST_DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/finance_test > .env.test
python -m pytest -q tests/test_postgres.py
```

---

## Security

- **Secrets stay out of the code.** The only secret is `BOT_TOKEN`. It is read from `.env` into a Pydantic
  `SecretStr`, it is never written to the logs, and it does not appear anywhere in the repository or its history.
- **`.gitignore`** excludes `.env` and every `.env.*` file except `.env.example`, local databases (`*.db`,
  `*.sqlite`, `*.sqlite3`), logs and virtual environments.
- **Data isolation.** Every service query is filtered by the current user's id. Forged callback data pointing at
  another user's record returns "not found".
- **Input validation.** Amounts must be positive, have at most 2 decimal places and stay below an upper bound. They
  are checked when parsed and again by Pydantic schemas. Callback data is strictly typed, so malformed values are
  rejected as stale buttons instead of crashing a handler.
- **Output escaping.** User-provided text is HTML-escaped before it is sent back.
- **Idempotency.** Double taps and repeated callbacks cannot create duplicate records.
- **Errors.** Exceptions are logged with a traceback, and the user sees a short, friendly message. Database errors
  roll back the current transaction.

---

## Future Improvements

These ideas are **not implemented yet**:

- Custom user categories. The database model already supports them; there is no UI yet.
- A persistent FSM storage (Redis or the database). Today, an unfinished multi-step dialog is lost if the bot
  restarts; saved data is not affected.
- Export to CSV or Excel.
- Manually entered exchange rates and an optional summary in a single currency.
- Recurring transactions, such as automatically recording subscriptions.
- Webhook mode and support for multiple instances.
- A CI pipeline that runs the test suite, including PostgreSQL, on every push.
- Verifying the Docker setup and adding screenshots of the remaining screens.

---

## License

No license has been added yet. Until one is chosen, all rights are reserved by the author.
