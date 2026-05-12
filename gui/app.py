"""Flask app for the PackingPlan GUI."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from packing_core import build_catalog, build_plan, parse_excel

app = Flask(__name__, static_folder="static", template_folder="templates")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/catalog")
def catalog():
    return jsonify(build_catalog())


@app.route("/api/plan", methods=["POST"])
def plan():
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    return jsonify(build_plan(items))


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    suffix = Path(f.filename or "").suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name
    try:
        items = parse_excel(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return jsonify({"items": items})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
