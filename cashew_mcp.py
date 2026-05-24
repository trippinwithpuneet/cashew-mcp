"""
Cashew budget app MCP server.
Connects to the local SQLite database and exposes tools for querying
transactions, spending by category, budgets, and wallet balances.

Database path: ~/Downloads/cashew.sqlite by default.
Override with the CASHEW_DB environment variable.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

_default_db = Path.home() / "Downloads" / "cashew.sqlite"
DB_PATH = Path(os.environ.get("CASHEW_DB", str(_default_db)))

mcp = FastMCP("cashew-budget")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ts_to_date(ts: int | None) -> str | None:
    """Convert Unix timestamp to ISO date string."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def date_to_ts(date_str: str) -> int:
    """Convert YYYY-MM-DD to Unix timestamp (start of day UTC)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


@mcp.tool()
def get_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    wallet: str | None = None,
    type: str = "all",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Fetch transactions with optional filters.

    Args:
        start_date: Filter from this date (YYYY-MM-DD), inclusive.
        end_date: Filter until this date (YYYY-MM-DD), inclusive.
        category: Filter by category name (case-insensitive, partial match).
        wallet: Filter by wallet name (case-insensitive, partial match).
        type: 'expense', 'income', or 'all' (default).
        limit: Maximum rows to return (default 50, max 500).

    Returns:
        List of transactions with date, name, amount, category, wallet fields.
    """
    limit = min(limit, 500)

    where: list[str] = []
    params: list[Any] = []

    if start_date:
        where.append("t.date_created >= ?")
        params.append(date_to_ts(start_date))
    if end_date:
        # add one day so end_date is inclusive
        end_ts = date_to_ts(end_date) + 86400
        where.append("t.date_created < ?")
        params.append(end_ts)
    if category:
        where.append("(c.name LIKE ? OR c2.name LIKE ?)")
        params += [f"%{category}%", f"%{category}%"]
    if wallet:
        where.append("w.name LIKE ?")
        params.append(f"%{wallet}%")
    if type == "expense":
        where.append("t.income = 0")
    elif type == "income":
        where.append("t.income = 1")

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT
            t.transaction_pk,
            t.name,
            t.amount,
            t.income,
            t.note,
            t.date_created,
            c.name  AS category,
            c2.name AS sub_category,
            w.name  AS wallet,
            w.currency
        FROM transactions t
        LEFT JOIN categories c  ON c.category_pk  = t.category_fk
        LEFT JOIN categories c2 ON c2.category_pk = t.sub_category_fk
        LEFT JOIN wallets w     ON w.wallet_pk     = t.wallet_fk
        {where_clause}
        ORDER BY t.date_created DESC
        LIMIT ?
    """
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "id": r["transaction_pk"],
            "date": ts_to_date(r["date_created"]),
            "name": r["name"],
            "amount": r["amount"],
            "type": "income" if r["income"] else "expense",
            "note": r["note"],
            "category": r["category"],
            "sub_category": r["sub_category"],
            "wallet": r["wallet"],
            "currency": r["currency"],
        }
        for r in rows
    ]


@mcp.tool()
def get_spending_by_category(
    start_date: str | None = None,
    end_date: str | None = None,
    wallet: str | None = None,
    include_income: bool = False,
) -> list[dict[str, Any]]:
    """
    Return total spending (or income) grouped by category.

    Args:
        start_date: Start of period (YYYY-MM-DD). Defaults to all time.
        end_date: End of period (YYYY-MM-DD). Defaults to today.
        wallet: Filter by wallet name (case-insensitive, partial match).
        include_income: If True, include income categories in the result.

    Returns:
        List of {category, total, count, currency} sorted by total descending.
    """
    where: list[str] = []
    params: list[Any] = []

    if not include_income:
        where.append("t.income = 0")

    if start_date:
        where.append("t.date_created >= ?")
        params.append(date_to_ts(start_date))
    if end_date:
        where.append("t.date_created < ?")
        params.append(date_to_ts(end_date) + 86400)
    if wallet:
        where.append("w.name LIKE ?")
        params.append(f"%{wallet}%")

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT
            COALESCE(c.name, 'Uncategorised') AS category,
            SUM(t.amount)                      AS total,
            COUNT(*)                           AS count,
            w.currency
        FROM transactions t
        LEFT JOIN categories c ON c.category_pk = t.category_fk
        LEFT JOIN wallets w    ON w.wallet_pk    = t.wallet_fk
        {where_clause}
        GROUP BY t.category_fk, w.currency
        ORDER BY total ASC
    """

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "category": r["category"],
            "total": round(r["total"], 2),
            "count": r["count"],
            "currency": r["currency"],
        }
        for r in rows
    ]


@mcp.tool()
def get_budgets(include_archived: bool = False) -> list[dict[str, Any]]:
    """
    Return all budgets with their current spending progress.

    Args:
        include_archived: Include archived budgets (default False).

    Returns:
        List of budgets with name, allocated amount, spent, remaining, and period info.
    """
    where = "" if include_archived else "WHERE b.archived = 0"

    sql2 = f"""
        SELECT
            b.budget_pk,
            b.name,
            b.amount       AS budget_amount,
            b.start_date,
            b.end_date,
            b.archived,
            b.reoccurrence,
            b.period_length,
            w.name         AS wallet,
            w.currency     AS wallet_currency
        FROM budgets b
        LEFT JOIN wallets w ON w.wallet_pk = b.wallet_fk
        {where}
        ORDER BY b.name
    """

    recurrence_map = {0: "custom", 1: "monthly", 2: "weekly", 3: "yearly", 4: "daily"}

    with get_conn() as conn:
        budgets = conn.execute(sql2).fetchall()

        result = []
        for b in budgets:
            # Compute actual spend within budget period
            spent_row = conn.execute(
                """
                SELECT COALESCE(SUM(t.amount), 0) AS spent
                FROM transactions t
                WHERE t.income = 0
                  AND t.date_created >= ?
                  AND t.date_created <  ?
                """,
                (b["start_date"], b["end_date"] + 1),
            ).fetchone()

            spent = abs(spent_row["spent"]) if spent_row else 0
            budget_amount = b["budget_amount"]
            remaining = budget_amount - spent

            result.append(
                {
                    "id": b["budget_pk"],
                    "name": b["name"],
                    "budget_amount": budget_amount,
                    "spent": round(spent, 2),
                    "remaining": round(remaining, 2),
                    "utilisation_pct": round(spent / budget_amount * 100, 1)
                    if budget_amount
                    else None,
                    "start_date": ts_to_date(b["start_date"]),
                    "end_date": ts_to_date(b["end_date"]),
                    "recurrence": recurrence_map.get(b["reoccurrence"], "custom"),
                    "period_length": b["period_length"],
                    "wallet": b["wallet"],
                    "currency": b["wallet_currency"],
                    "archived": bool(b["archived"]),
                }
            )

    return result


@mcp.tool()
def get_wallet_balances() -> list[dict[str, Any]]:
    """
    Return all wallets with their current balance (sum of all transactions).

    Returns:
        List of wallets: name, currency, balance, income_total, expense_total.
    """
    sql = """
        SELECT
            w.wallet_pk,
            w.name,
            w.currency,
            w.archived,
            COALESCE(SUM(t.amount), 0)                             AS balance,
            COALESCE(SUM(CASE WHEN t.income = 1 THEN t.amount ELSE 0 END), 0) AS income_total,
            COALESCE(SUM(CASE WHEN t.income = 0 THEN t.amount ELSE 0 END), 0) AS expense_total
        FROM wallets w
        LEFT JOIN transactions t ON t.wallet_fk = w.wallet_pk
        GROUP BY w.wallet_pk
        ORDER BY w.archived, w."order"
    """

    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()

    return [
        {
            "name": r["name"],
            "currency": r["currency"],
            "balance": round(r["balance"], 2),
            "income_total": round(r["income_total"], 2),
            "expense_total": round(r["expense_total"], 2),
            "archived": bool(r["archived"]),
        }
        for r in rows
    ]


@mcp.tool()
def search_transactions(
    query: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """
    Full-text search over transaction names and notes.

    Args:
        query: Search string (case-insensitive).
        limit: Max results (default 30, max 200).

    Returns:
        Matching transactions ordered by date descending.
    """
    limit = min(limit, 200)
    pattern = f"%{query}%"

    sql = """
        SELECT
            t.transaction_pk,
            t.name,
            t.amount,
            t.income,
            t.note,
            t.date_created,
            c.name  AS category,
            w.name  AS wallet,
            w.currency
        FROM transactions t
        LEFT JOIN categories c ON c.category_pk = t.category_fk
        LEFT JOIN wallets w    ON w.wallet_pk    = t.wallet_fk
        WHERE t.name LIKE ? OR t.note LIKE ?
        ORDER BY t.date_created DESC
        LIMIT ?
    """

    with get_conn() as conn:
        rows = conn.execute(sql, [pattern, pattern, limit]).fetchall()

    return [
        {
            "id": r["transaction_pk"],
            "date": ts_to_date(r["date_created"]),
            "name": r["name"],
            "amount": r["amount"],
            "type": "income" if r["income"] else "expense",
            "note": r["note"],
            "category": r["category"],
            "wallet": r["wallet"],
            "currency": r["currency"],
        }
        for r in rows
    ]


@mcp.tool()
def get_monthly_summary(
    year: int | None = None,
    wallet: str | None = None,
) -> list[dict[str, Any]]:
    """
    Monthly income vs expense summary.

    Args:
        year: Filter to a specific year (e.g. 2025). Defaults to all years.
        wallet: Filter by wallet name (case-insensitive, partial match).

    Returns:
        List of {month, income, expenses, net} sorted chronologically.
    """
    where: list[str] = []
    params: list[Any] = []

    if wallet:
        where.append("w.name LIKE ?")
        params.append(f"%{wallet}%")

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT
            strftime('%Y-%m', datetime(t.date_created, 'unixepoch')) AS month,
            COALESCE(SUM(CASE WHEN t.income = 1 THEN t.amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN t.income = 0 THEN t.amount ELSE 0 END), 0) AS expenses
        FROM transactions t
        LEFT JOIN wallets w ON w.wallet_pk = t.wallet_fk
        {where_clause}
        GROUP BY month
        ORDER BY month
    """

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    result = []
    for r in rows:
        if year and not r["month"].startswith(str(year)):
            continue
        income = round(r["income"], 2)
        expenses = round(r["expenses"], 2)
        result.append(
            {
                "month": r["month"],
                "income": income,
                "expenses": expenses,
                "net": round(income + expenses, 2),
            }
        )

    return result


def main():
    mcp.run()


if __name__ == "__main__":
    main()
