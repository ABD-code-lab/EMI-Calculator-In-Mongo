from datetime import datetime, timezone
import os
import socket
from flask import Flask, jsonify, render_template, request
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError

app = Flask(__name__)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("MONGO_DB", "emidb")
COLLECTION_NAME = "calculations"

_client = None


def get_db():
    global _client
    if not MONGO_URL:
        raise RuntimeError("MONGO_URL environment variable is not set")
    if _client is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=2000)
    return _client[DB_NAME]


def get_served_by() -> str:
    pod_name = os.environ.get("HOSTNAME") or socket.gethostname()
    try:
        pod_ip = os.environ.get("POD_IP") or socket.gethostbyname(socket.gethostname())
    except OSError:
        pod_ip = "unknown"
    return f"{pod_name} ({pod_ip})"


def calculate_emi(principal: float, annual_rate: float, months: int) -> dict:
    monthly_rate = annual_rate / 12.0 / 100.0
    factor = (1.0 + monthly_rate) ** months
    emi = (principal * monthly_rate * factor) / (factor - 1.0)
    total_payment = emi * months
    total_interest = total_payment - principal
    return {
        "emi": round(emi, 2),
        "totalPayment": round(total_payment, 2),
        "totalInterest": round(total_interest, 2),
    }


def parse_positive_inputs(data: dict):
    if not isinstance(data, dict):
        return None, "Invalid JSON payload"

    raw_p = data.get("P", data.get("principal"))
    raw_rate = data.get("rate")
    raw_n = data.get("n", data.get("months"))

    try:
        principal = float(raw_p)
        rate = float(raw_rate)
        months_float = float(raw_n)
    except (TypeError, ValueError):
        return None, "Principal, rate, and months must be valid numbers"

    if principal <= 0 or rate <= 0 or months_float <= 0:
        return None, "Principal, annual interest rate, and tenure in months must be positive numbers"

    if not months_float.is_integer():
        return None, "Tenure in months must be a positive whole number"

    months = int(months_float)
    return (principal, rate, months), None


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    try:
        db = get_db()
        db.command("ping")
        return jsonify({"status": "ok", "mongodb": "reachable"}), 200
    except Exception as exc:
        return jsonify({"status": "error", "mongodb": "unreachable", "detail": str(exc)}), 503


@app.route("/api/emi", methods=["POST"])
def api_emi():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()

    parsed, err = parse_positive_inputs(payload)
    if err:
        return jsonify({"error": err}), 400

    principal, rate, months = parsed
    calc = calculate_emi(principal, rate, months)
    served_by = get_served_by()
    created_at = datetime.now(timezone.utc)

    doc = {
        "principal": principal,
        "rate": rate,
        "months": months,
        "emi": calc["emi"],
        "servedBy": served_by,
        "createdAt": created_at,
    }

    try:
        db = get_db()
        db[COLLECTION_NAME].insert_one(doc)
    except PyMongoError as exc:
        return jsonify({"error": "Failed to store calculation in MongoDB", "detail": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "emi": calc["emi"],
            "totalPayment": calc["totalPayment"],
            "totalInterest": calc["totalInterest"],
            "servedBy": served_by,
            "createdAt": created_at.isoformat(),
        }
    ), 200


@app.route("/api/history", methods=["GET"])
def api_history():
    try:
        db = get_db()
        cursor = (
            db[COLLECTION_NAME]
            .find({}, {"_id": 0})
            .sort("createdAt", DESCENDING)
            .limit(10)
        )
        items = []
        for row in cursor:
            created = row.get("createdAt")
            items.append(
                {
                    "principal": row.get("principal"),
                    "rate": row.get("rate"),
                    "months": row.get("months"),
                    "emi": row.get("emi"),
                    "servedBy": row.get("servedBy"),
                    "createdAt": created.isoformat() if hasattr(created, "isoformat") else str(created),
                }
            )
        return jsonify({"history": items}), 200
    except PyMongoError as exc:
        return jsonify({"error": "Failed to fetch history from MongoDB", "detail": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)