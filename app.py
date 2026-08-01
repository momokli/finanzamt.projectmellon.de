import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from importers.ing import is_mellon, parse_ing_csv, transaction_key
from importers.paypal import (
    is_mellon_paypal,
    parse_paypal_csv,
    paypal_transaction_key,
)

app = Flask(__name__)

DATA_FILE = Path("/data/data.json")

AUTH_USER = os.environ.get("AUTH_USER", "admin")
AUTH_PASS = os.environ.get("AUTH_PASS", "change-me")
KOFI_WEBHOOK_SECRET = os.environ.get("KOFI_WEBHOOK_SECRET", "")


def load_data():
    if not DATA_FILE.exists():
        return {
            "costs": {"monthly": {}, "yearly": {}},
            "donations": {},
            "running_balance": 0.0,
        }
    return json.loads(DATA_FILE.read_text())


def monthly_goal(data):
    monthly = sum(data["costs"].get("monthly", {}).values())
    yearly = sum(data["costs"].get("yearly", {}).values()) / 12
    return round(monthly + yearly, 2)


def current_month_key():
    return datetime.utcnow().strftime("%Y-%m")


def month_donations(data, month_key):
    return data.get("donations", {}).get(month_key, [])


def month_total(data, month_key):
    return round(sum(d.get("amount", 0) for d in month_donations(data, month_key)), 2)


def check_auth(username, password):
    return username == AUTH_USER and password == AUTH_PASS


def authenticate():
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Support Dashboard"'},
    )


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)

    return decorated


@app.route("/admin")
@require_auth
def admin_dashboard():
    data = load_data()
    year = datetime.utcnow().strftime("%Y")
    goal = monthly_goal(data)

    # Collect all donations for the current year
    donations_by_month = {}
    all_donations = []
    for month_key, dons in sorted(data.get("donations", {}).items()):
        if not month_key.startswith(year):
            continue
        donations_by_month[month_key] = dons
        all_donations.extend(dons)

    # Per-source totals
    source_totals = {}
    for d in all_donations:
        src = d.get("source", "unknown")
        source_totals[src] = source_totals.get(src, 0.0) + d.get("amount", 0)

    # Monthly totals
    monthly_totals = {}
    for mk, dons in donations_by_month.items():
        monthly_totals[mk] = {
            "total": round(sum(d.get("amount", 0) for d in dons), 2),
            "count": len(dons),
        }

    # Year totals
    ytd_total = round(sum(d.get("amount", 0) for d in all_donations), 2)
    current_month_num = datetime.utcnow().month
    ytd_goal = round(goal * current_month_num, 2)
    ytd_pct = min(round((ytd_total / ytd_goal) * 100, 1), 100) if ytd_goal > 0 else 100
    running_balance = round(data.get("running_balance", 0), 2)

    # All donors
    all_donors = set()
    for d in all_donations:
        if d.get("name"):
            all_donors.add(d["name"])

    return render_template(
        "admin.html",
        data=data,
        year=year,
        goal=goal,
        source_totals=source_totals,
        monthly_totals=monthly_totals,
        ytd_total=ytd_total,
        ytd_goal=ytd_goal,
        ytd_pct=ytd_pct,
        running_balance=running_balance,
        all_donations=sorted(
            all_donations, key=lambda d: d.get("date", ""), reverse=True
        ),
        donor_count=len(all_donors),
    )


@app.route("/admin/import", methods=["GET", "POST"])
@require_auth
def admin_import():
    preview = None
    counts = None
    error = None

    if request.method == "POST":
        action = request.form.get("action", "preview")

        if action == "preview":
            file = request.files.get("csv_file")
            if not file or file.filename == "":
                error = "No file selected."
            else:
                try:
                    content = file.read()

                    # Auto-detect: PayPal has "Beschreibung" column, ING has "Buchung"
                    content_str = (
                        content.decode("utf-8-sig")
                        if isinstance(content, bytes)
                        else content
                    )
                    if "Beschreibung" in content_str[:500]:
                        source = "paypal"
                        txs = parse_paypal_csv(content)
                    else:
                        source = "iban"
                        txs = parse_ing_csv(content)

                    if not txs:
                        error = "No incoming transactions found in the CSV."
                    else:
                        # Mark which ones are auto-detected as Mellon
                        preview = []
                        for tx in txs:
                            if source == "paypal":
                                preview.append(
                                    {
                                        "date": tx.datum,
                                        "sender": tx.name or tx.email or "Unknown",
                                        "verwendungszweck": tx.beschreibung,
                                        "betrag": tx.netto,
                                        "betrag_brutto": tx.brutto,
                                        "entgelt": tx.entgelt,
                                        "is_mellon": is_mellon_paypal(tx),
                                        "key": paypal_transaction_key(tx),
                                        "source": source,
                                    }
                                )
                            else:
                                preview.append(
                                    {
                                        "date": tx.buchung,
                                        "sender": tx.sender,
                                        "verwendungszweck": tx.verwendungszweck,
                                        "betrag": tx.betrag,
                                        "betrag_brutto": tx.betrag,
                                        "entgelt": 0,
                                        "is_mellon": is_mellon(tx),
                                        "key": transaction_key(tx),
                                        "source": source,
                                    }
                                )
                except ValueError as e:
                    error = str(e)
                except Exception as e:
                    error = f"Failed to parse CSV: {e}"

        elif action == "confirm":
            data = load_data()
            selected_keys = request.form.getlist("selected")
            if not selected_keys:
                error = "No transactions selected."
            else:
                imported_count = 0
                skipped_count = 0

                # Build lookup of existing transaction keys (normalize date to DD.MM.YYYY)
                existing_keys = set()
                for month_donations_list in data.get("donations", {}).values():
                    for d in month_donations_list:
                        try:
                            dt = datetime.strptime(d.get("date", ""), "%Y-%m-%d")
                            date_norm = dt.strftime("%d.%m.%Y")
                        except ValueError:
                            date_norm = d.get("date", "")
                        key = f"{date_norm}|{d.get('amount', 0):.2f}|{d.get('name', '').lower()}"
                        existing_keys.add(key)

                for entry_json in selected_keys:
                    entry = json.loads(entry_json)
                    key = entry["key"]
                    if key in existing_keys:
                        skipped_count += 1
                        continue

                    # Convert "DD.MM.YYYY" to "YYYY-MM-DD"
                    try:
                        dt = datetime.strptime(entry["date"], "%d.%m.%Y")
                        date_str = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        date_str = entry["date"]

                    month_key = date_str[:7]  # "YYYY-MM"
                    data.setdefault("donations", {}).setdefault(month_key, []).append(
                        {
                            "date": date_str,
                            "amount": round(entry["betrag"], 2),
                            "source": entry.get("source", "iban"),
                            "name": entry["sender"],
                        }
                    )
                    imported_count += 1
                    existing_keys.add(key)

                DATA_FILE.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n"
                )
                counts = {"imported": imported_count, "skipped": skipped_count}

    return render_template(
        "import.html",
        preview=preview,
        counts=counts,
        error=error,
    )


@app.route("/")
def index():
    data = load_data()
    goal = monthly_goal(data)
    mk = current_month_key()
    received = month_total(data, mk)
    balance = round(data.get("running_balance", 0), 2)

    surplus = round(received - goal, 2)
    projected = round(balance + surplus, 2)
    pct = min(round((received / goal) * 100, 1), 100) if goal > 0 else 100

    this_month = month_donations(data, mk)
    donation_count = len(this_month)
    avg_donation = round(received / donation_count, 2) if donation_count > 0 else 0

    all_names = set()
    for donations_list in data.get("donations", {}).values():
        for d in donations_list:
            if d.get("name"):
                all_names.add(d["name"])
    total_donors = len(all_names)

    # year-to-date
    year = datetime.utcnow().strftime("%Y")
    ytd_received = 0.0
    for key, donations_list in data.get("donations", {}).items():
        if key.startswith(year):
            ytd_received += sum(d.get("amount", 0) for d in donations_list)
    ytd_received = round(ytd_received, 2)
    current_month_num = datetime.utcnow().month
    ytd_goal = round(goal * current_month_num, 2)
    yearly_total = round(goal * 12, 2)
    ytd_pct = (
        min(round((ytd_received / ytd_goal) * 100, 1), 100) if ytd_goal > 0 else 100
    )

    # Month-by-month year progress: which months are fully funded?
    months_funded = int(ytd_received // goal) if goal > 0 else 0
    if months_funded > 12:
        months_funded = 12
    # Partial fill percentage for the next month (if any)
    month_partial_pct = 0
    if months_funded < 12 and goal > 0:
        remainder = ytd_received - (months_funded * goal)
        month_partial_pct = min(round((remainder / goal) * 100), 100)

    # Build list of 12 month dicts for template
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
    year_months = []
    for i in range(12):
        m = {
            "label": month_labels[i],
            "index": i + 1,
        }
        if i < months_funded:
            m["state"] = "funded"
        elif i == months_funded and month_partial_pct > 0:
            m["state"] = "partial"
            m["partial_pct"] = month_partial_pct
        elif (i + 1) == current_month_num and months_funded < (i + 1):
            m["state"] = "current"
        else:
            m["state"] = "future"
        year_months.append(m)

    return render_template(
        "index.html",
        data=data,
        goal=goal,
        month_key=mk,
        received=received,
        surplus=surplus,
        balance=balance,
        projected=projected,
        pct=pct,
        donation_count=donation_count,
        avg_donation=avg_donation,
        total_donors=total_donors,
        year=year,
        ytd_received=ytd_received,
        ytd_goal=ytd_goal,
        yearly_total=yearly_total,
        ytd_pct=ytd_pct,
        year_months=year_months,
        months_funded=months_funded,
    )


@app.route("/api/kofi-webhook", methods=["POST"])
def kofi_webhook():
    if not KOFI_WEBHOOK_SECRET:
        return jsonify({"error": "webhook not configured"}), 501

    token = request.form.get("verification_token", "")
    if token != KOFI_WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 403

    raw = request.form.get("data", "")
    if not raw:
        return jsonify({"error": "missing data"}), 400

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    if payload.get("type") != "Donation":
        return jsonify({"status": "ignored", "reason": "not a donation"}), 200

    amount_str = payload.get("amount", "0")
    try:
        amount = round(float(amount_str), 2)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid amount"}), 400

    name = (payload.get("from_name") or "Anonymous").strip()
    ts = payload.get("timestamp", datetime.utcnow().isoformat())
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    data = load_data()
    mk = current_month_key()
    data.setdefault("donations", {}).setdefault(mk, []).append(
        {"date": date_str, "amount": amount, "source": "kofi", "name": name}
    )
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
