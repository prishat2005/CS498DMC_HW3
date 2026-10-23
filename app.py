import os
import time
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, request, g
from pymongo import MongoClient, ReadPreference
from pymongo.write_concern import WriteConcern
from pymongo.errors import PyMongoError

POLICIES = {"fast" : WriteConcern(w=1), "safe":WriteConcern(w="majority")}
MODES={"tesla":("TESLA",ReadPreference.PRIMARY), "bmw":("BMW", ReadPreference.SECONDARY_PREFERRED)}
load_dotenv(Path(__file__).parent / ".env")
app = Flask(__name__)
mongo = MongoClient(
    os.environ["MONGO_URI"],
    serverSelectionTimeoutMS=10000
)
ev_database = mongo.get_database("ev_db")
inventory = ev_database.get_collection("vehicles")

@app.before_request
def mark_req_start():
    g.started = time.perf_counter()

@app.after_request
def add_runtime(response):
    elapsed = time.perf_counter() - g.started
    response.headers["X-Processing-Ms"] = f"{elapsed * 1000:.2f}"
    return response


def init_entry():
    payload = request.get_json(silent=True)
    if not isinstance(payload,dict):
        return None
    return {field: value for field, value in payload.items() if field != "_id"}

def database_scan():
    try:
        mongo.admin.command("ping")
        return True
    except PyMongoError:
        app.logger.warning("Connection check failed")
        return False


def commit_vehicle(mode):
    document = init_entry()
    if document is None:
        return jsonify({"error": "JSON object required"}), 400
    target = inventory.with_options(
        write_concern=POLICIES[mode]
    )
    try:
        result = target.insert_one(document)
    except PyMongoError:
        app.logger.exception("Unable to insert vehicle")
        return jsonify({"error": "Database write failed"}), 503

    return jsonify({"inserted_id": str(result.inserted_id)}), 201


def count_matches(manufacturer, preference):
    q_filter = {"Make": manufacturer}
    try:
        src = inventory.with_options(read_preference=preference)
        count = src.count_documents(q_filter)
        return jsonify({"count":count}), 200
    except PyMongoError:
        app.logger.exception("Lookup failed for %s", manufacturer)
        return jsonify({"error": "Read failed"}), 503

@app.route("/insert-fast", methods=["POST"])
def quick_insert():
    return commit_vehicle("fast")

@app.route("/insert-safe", methods=["POST"])
def confirmed_insert():
    return commit_vehicle("safe")
    
@app.route("/count-bmw-secondary", methods=["GET"])
def bmw_inventory():
    return count_matches(*MODES["bmw"])
    

@app.route("/count-tesla-primary", methods=["GET"])
def tesla_inventory():
    return count_matches(*MODES["tesla"])
    
@app.route("/health", methods=["GET"])
def checkup():
    status = database_scan()
    return jsonify({"database":"reachable" if status else "unavailable"}), 200 if status else 503
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
