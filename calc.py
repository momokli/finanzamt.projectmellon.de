"""Calculation layer for support.projectmellon.de.

Pure functions that take raw data.json and return all derived values.
No I/O, no Flask — just numbers. Testable independently.
"""

from datetime import datetime


def monthly_goal(costs):
    """Total monthly costs including amortized yearly items."""
    monthly = sum(costs.get("monthly", {}).values())
    yearly = sum(costs.get("yearly", {}).values()) / 12
    return round(monthly + yearly, 2)


def yearly_total(goal):
    return round(goal * 12, 2)


def _month_donations(donations, month_key):
    return donations.get(month_key, [])


def _split_recurring(donations_list, subscribers):
    """Split donations into recurring (subs) and one-time (onces).

    A donation is recurring if the donor name is in the subscribers list
    or if the donation itself has recurring=True.
    """
    subs = []
    onces = []
    for d in donations_list:
        if d.get("name") in subscribers or d.get("recurring"):
            subs.append(d)
        else:
            onces.append(d)
    return subs, onces


def month_stats(donations, month_key, subscribers):
    """Stats for a single month: recurring vs one-time breakdown.

    If the current month has no subscriber donations, fall back to the most
    recent prior month that does have subscriber data.
    """
    dons = _month_donations(donations, month_key)
    subs, onces = _split_recurring(dons, subscribers)

    # Fallback for subs: if no subs this month, use last month with subs
    if not subs and subscribers:
        sorted_keys = sorted(donations.keys(), reverse=True)
        for mk in sorted_keys:
            if mk >= month_key:
                continue
            test_dons = _month_donations(donations, mk)
            test_subs, _ = _split_recurring(test_dons, subscribers)
            if test_subs:
                subs = test_subs
                break

    subs_total = round(sum(d.get("amount", 0) for d in subs), 2)
    subs_unique = len(set(d.get("name", "") for d in subs))
    subs_avg = round(subs_total / subs_unique, 2) if subs_unique > 0 else 0

    onces_total = round(sum(d.get("amount", 0) for d in onces), 2)
    onces_count = len(onces)
    onces_avg = round(onces_total / onces_count, 2) if onces_count > 0 else 0

    received = round(subs_total + onces_total, 2)
    donation_count = len(dons)
    avg_donation = round(received / donation_count, 2) if donation_count > 0 else 0

    return {
        "subs_count": subs_unique,
        "subs_total": subs_total,
        "subs_avg": subs_avg,
        "onces_count": onces_count,
        "onces_total": onces_total,
        "onces_avg": onces_avg,
        "received": received,
        "donation_count": donation_count,
        "avg_donation": avg_donation,
    }


def _ytd_split_stats(donations, year, subscribers, mode):
    """Year-to-date stats: count unique donors, total, and median per-person-per-month.

    mode = "subs" or "onces"
    """
    totals = {}
    for key, dons in donations.items():
        if key.startswith(str(year)):
            if mode == "subs":
                items, _ = _split_recurring(dons, subscribers)
            else:
                _, items = _split_recurring(dons, subscribers)
            for d in items:
                name = d.get("name", "")
                if name:
                    totals[name] = totals.get(name, 0) + d.get("amount", 0)

    total = round(sum(totals.values()), 2)
    unique_donors = len(totals)
    current_month = datetime.utcnow().month

    # Median: subs = per-person-per-month, onces = per-donation
    if mode == "subs":
        values = sorted([t / current_month for t in totals.values()])
    else:
        values = []
        for key, dons in donations.items():
            if key.startswith(str(year)):
                _, onces = _split_recurring(dons, subscribers)
                for d in onces:
                    values.append(d.get("amount", 0))
        values.sort()

    n = len(values)
    if n == 0:
        median = 0.0
    elif n % 2 == 1:
        median = values[n // 2]
    else:
        median = (values[n // 2 - 1] + values[n // 2]) / 2

    return {
        "count": unique_donors,
        "total": total,
        "avg": round(median, 2),
    }


def ytd_stats(donations, goal, year):
    """Year-to-date donation totals."""
    ytd_received = 0.0
    for key, dons in donations.items():
        if key.startswith(str(year)):
            ytd_received += sum(d.get("amount", 0) for d in dons)
    ytd_received = round(ytd_received, 2)

    current_month = datetime.utcnow().month
    ytd_goal = round(goal * current_month, 2)
    ytd_pct = (
        min(round((ytd_received / ytd_goal) * 100, 1), 100) if ytd_goal > 0 else 100
    )

    return {
        "ytd_received": ytd_received,
        "ytd_goal": ytd_goal,
        "ytd_pct": ytd_pct,
    }


def year_progress(ytd_received, goal, current_month_num):
    """Build month-by-month year progress view."""
    months_funded = int(ytd_received // goal) if goal > 0 else 0
    months_funded = min(months_funded, 12)

    month_partial_pct = 0
    if months_funded < 12 and goal > 0:
        remainder = ytd_received - (months_funded * goal)
        month_partial_pct = min(round((remainder / goal) * 100), 100)

    month_labels = [
        "Jan",
        "Feb",
        "Mär",
        "Apr",
        "Mai",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Okt",
        "Nov",
        "Dez",
    ]
    months = []
    for i in range(12):
        m = {"label": month_labels[i], "index": i + 1}
        if i < months_funded:
            m["state"] = "funded"
        elif i == months_funded and month_partial_pct > 0:
            m["state"] = "partial"
            m["partial_pct"] = month_partial_pct
        elif (i + 1) == current_month_num and months_funded < (i + 1):
            m["state"] = "current"
        else:
            m["state"] = "future"
        months.append(m)

    return months, months_funded


def cost_items(costs):
    """Flatten cost dict into a display-ready list with labels."""
    items = []
    merged_licenses = 0.0

    for key, amount in costs.get("monthly", {}).items():
        if key == "usenet_provider":
            merged_licenses += amount
            continue
        items.append(
            {"key": key, "amount": amount, "label": cost_label(key), "cycle": "monthly"}
        )
    for key, amount in costs.get("yearly", {}).items():
        monthly_amount = round(amount / 12, 2)
        if key == "usenet_indexer":
            merged_licenses += monthly_amount
            continue
        items.append(
            {
                "key": key,
                "amount": monthly_amount,
                "label": cost_label(key),
                "cycle": "yearly",
            }
        )

    if merged_licenses > 0:
        items.append(
            {
                "key": "lizenzen",
                "amount": round(merged_licenses, 2),
                "label": "Lizenzen",
                "cycle": "merged",
            }
        )

    return items


def cost_label(key):
    """Human-readable label for a cost key."""
    labels = {
        "hetzner_ex44": "Hetzner EX44 Root Server",
        "hetzner_cloud": "Hetzner CX11 Cloud (5×)",
        "hetzner_storage": "Hetzner Storage (BX21+3×BX41)",
        "contabo_vps": "Contabo VPS",
        "usenet_provider": "Interne Dienste",
        "domains": "Domains",
        "usenet_indexer": "Wartung & Lizenzen",
    }
    return labels.get(key, key.replace("_", " ").title())


def total_donors(donations):
    """Count unique donor names across all time."""
    names = set()
    for dons in donations.values():
        for d in dons:
            if d.get("name"):
                names.add(d["name"])
    return len(names)


def compute(data):
    """Main entry point: compute everything from raw data.json."""
    costs = data.get("costs", {})
    donations = data.get("donations", {})
    running_balance = round(data.get("running_balance", 0), 2)
    subscribers = set(data.get("subscribers", []))

    goal = monthly_goal(costs)
    mk = datetime.utcnow().strftime("%Y-%m")
    year = int(datetime.utcnow().strftime("%Y"))

    ms = month_stats(donations, mk, subscribers)
    ytd = ytd_stats(donations, goal, year)
    ytd_subs = _ytd_split_stats(donations, year, subscribers, "subs")
    ytd_onces = _ytd_split_stats(donations, year, subscribers, "onces")
    ymonths, months_funded = year_progress(
        ytd["ytd_received"], goal, datetime.utcnow().month
    )

    surplus = round(ms["received"] - goal, 2)
    projected = round(running_balance + surplus, 2)
    pct = min(round((ms["received"] / goal) * 100, 1), 100) if goal > 0 else 100

    return {
        "goal": goal,
        "yearly_total": yearly_total(goal),
        "month_key": mk,
        "year": year,
        "current_month": datetime.utcnow().month,
        "running_balance": running_balance,
        "surplus": surplus,
        "projected": projected,
        "pct": pct,
        # Month stats
        "received": ms["received"],
        # YTD
        "ytd_received": ytd["ytd_received"],
        "ytd_goal": ytd["ytd_goal"],
        "ytd_pct": ytd["ytd_pct"],
        # YTD subs (Abo)
        "subs_ytd_count": ytd_subs["count"],
        "subs_ytd_total": ytd_subs["total"],
        "subs_ytd_avg": ytd_subs["avg"],
        # YTD one-time (Spenden)
        "onces_ytd_count": ytd_onces["count"],
        "onces_ytd_total": ytd_onces["total"],
        "onces_ytd_avg": ytd_onces["avg"],
        # Year progress
        "year_months": ymonths,
        "months_funded": months_funded,
        # Costs
        "cost_items": cost_items(costs),
        # Donors
        "total_donors": total_donors(donations),
    }
