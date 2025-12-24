# keep_alive.py
import os
from flask import Flask

app = Flask(__name__)

@app.get("/")
def home():
    return "OK", 200

def run():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
