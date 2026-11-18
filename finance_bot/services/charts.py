"""Графики через matplotlib (backend Agg, без GUI). Возвращают PNG-байты."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

from finance_bot.services.stats import CategoryShare, Totals  # noqa: E402
from finance_bot.utils.money import CURRENCY_SYMBOLS  # noqa: E402

PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D",
           "#BAB0AC"]  # fmt: skip
INCOME_COLOR, EXPENSE_COLOR = "#54A24B", "#E45756"


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def categories_pie(shares: list[CategoryShare], title: str, currency: str) -> bytes:
    top = shares[:7]
    rest = sum((s.amount for s in shares[7:]), start=0)
    labels = [s.category.name for s in top] + (["Остальное"] if rest else [])
    values = [float(s.amount) for s in top] + ([float(rest)] if rest else [])
    fig, ax = plt.subplots(figsize=(6, 6))
    wedges, *_ = ax.pie(
        values,
        colors=PALETTE[: len(values)],
        startangle=90,
        counterclock=False,
        autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
        pctdistance=0.78,
        wedgeprops={"width": 0.45, "edgecolor": "white"},
        textprops={"color": "white", "fontsize": 11, "weight": "bold"},
    )
    total = sum(values)
    ax.text(0, 0, f"{total:,.0f}".replace(",", " ") + f" {CURRENCY_SYMBOLS.get(currency, currency)}",
            ha="center", va="center", fontsize=14, weight="bold")  # fmt: skip
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1, 0.5), frameon=False)
    ax.set_title(title, fontsize=14, weight="bold")
    return _png(fig)


def months_bars(series: list[tuple[str, Totals]], currency: str) -> bytes:
    labels = [s[0] for s in series]
    inc = [float(s[1].income) for s in series]
    exp = [float(s[1].expense) for s in series]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(7, 4))
    w = 0.38
    ax.bar([i - w / 2 for i in x], inc, w, label="Доходы", color=INCOME_COLOR)
    ax.bar([i + w / 2 for i in x], exp, w, label="Расходы", color=EXPENSE_COLOR)
    ax.set_xticks(list(x), labels)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", " ")))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False)
    ax.set_title(f"Доходы и расходы по месяцам, {CURRENCY_SYMBOLS.get(currency, currency)}", weight="bold")
    return _png(fig)
