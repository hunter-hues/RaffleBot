from flask import Flask
import os
import sys

app = Flask(__name__)

@app.route('/')
def hello():
    # Print environment variables to help debug
    env_vars = {key: value for key, value in os.environ.items()}
    return f"Hello from Railway! App is working.<br>PORT: {os.getenv('PORT')}<br>Python: {sys.version}"

if __name__ == "__main__":
    # Try to get the port from different possible environment variables
    port = int(os.getenv("PORT", os.getenv("RAILWAY_PORT", 8080)))
    print(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port) 