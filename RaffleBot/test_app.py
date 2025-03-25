from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def hello():
    return "Hello from Railway! The app is working."

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port) 