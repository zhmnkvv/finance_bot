from finance_bot.bot.handlers import (
    budgets,
    debts,
    fallback,
    history,
    menu,
    reminders,
    settings,
    stats,
    transactions,
)

# Порядок важен: fallback — последним
routers = [
    menu.router,
    transactions.router,
    history.router,
    stats.router,
    budgets.router,
    debts.router,
    reminders.router,
    settings.router,
    fallback.router,
]
