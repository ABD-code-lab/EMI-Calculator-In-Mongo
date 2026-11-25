import os
import socket
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError

app = Flask(__name__)

MONGO_URL = os.environ.get("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("MONGO_URL environment variable is required")

client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=2000)
try:
    db = client.get_default_database()
except Exception:
    db = None

if db is None:
    db = client["emidb"]
calculations_col = db["calculations"]


def get_pod_info():
    pod_name = os.environ.get("HOSTNAME") or socket.gethostname()
    try:
        pod_ip = socket.gethostbyname(socket.gethostname())
    except socket.gaierror:
        pod_ip = "unknown"
    return f"{pod_name} ({pod_ip})"


def calculate_emi(principal: float, annual_rate: float, months: int):
    r = annual_rate / 12.0 / 100.0
    if r == 0:
        emi = principal / months
    else:
        factor = (1.0 + r) ** months
        emi = (principal * r * factor) / (factor - 1.0)

    total_payment = emi * months
    total_interest = total_payment - principal
    return round(emi, 2), round(total_payment, 2), round(total_interest, 2)


def validate_inputs(data):
    if not isinstance(data, dict):
        return None, "Invalid JSON payload"

    try:
        p = float(data.get("principal"))
        rate = float(data.get("rate"))
        n = int(data.get("months"))
    except (TypeError, ValueError):
        return None, "Principal, annual rate, and months must be valid numbers"

    if p <= 0 or rate <= 0 or n <= 0:
        return None, "Principal, annual rate, and months must be positive numbers"

    return (p, rate, n), None


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    try:
        client.admin.command("ping")
        return jsonify({"status": "ok", "mongodb": "reachable"}), 200
    except PyMongoError as exc:
        return jsonify({"status": "error", "mongodb": str(exc)}), 503


@app.route("/api/emi", methods=["POST"])
def api_emi():
    parsed, err = validate_inputs(request.get_json(silent=True))
    if err:
        return jsonify({"error": err}), 400

    p, rate, n = parsed
    emi, total_payment, total_interest = calculate_emi(p, rate, n)
    served_by = get_pod_info()
    created_at = datetime.now(timezone.utc)

    doc = {
        "principal": p,
        "rate": rate,
        "months": n,
        "emi": emi,
        "totalPayment": total_payment,
        "totalInterest": total_interest,
        "servedBy": served_by,
        "createdAt": created_at,
    }

    try:
        calculations_col.insert_one(doc)
    except PyMongoError as exc:
        return jsonify({"error": f"Database write failed: {exc}"}), 500

    return jsonify({
        "emi": emi,
        "totalPayment": total_payment,
        "totalInterest": total_interest,
        "servedBy": served_by,
    }), 200


@app.route("/api/history", methods=["GET"])
def api_history():
    try:
        cursor = calculations_col.find({}, {"_id": 0}).sort("createdAt", DESCENDING).limit(10)
        items = []
        for item in cursor:
            if isinstance(item.get("createdAt"), datetime):
                item["createdAt"] = item["createdAt"].isoformat()
            items.append(item)
        return jsonify(items), 200
    except PyMongoError as exc:
        return jsonify({"error": f"Database read failed: {exc}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)