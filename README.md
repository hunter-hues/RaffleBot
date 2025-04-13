# Twitch Giveaway Bot

A Flask-based application for managing Twitch giveaways with a chatbot interface.

## Features

- Create and manage giveaways
- Connect to Twitch chat
- Secure authentication with Twitch
- Rate limiting and security features
- SQLite database for data storage

## Local Development

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file with your Twitch API credentials
4. Run the application: `python app.py`

## Deployment on Render (Free Tier)

This application is configured to deploy on Render's free tier with minimal costs.

### Prerequisites

- A Render account (requires credit card even for free tier)
- Twitch API credentials (Client ID, Client Secret, Bot Token)

### Deployment Steps

1. **Push your code to GitHub**

2. **Create a new Web Service on Render**
   - Go to [render.com](https://render.com/) and sign in
   - Click "New" and select "Web Service"
   - Connect your GitHub repository
   - Select the repository containing this code
   - Configure the service:
     - Name: `giveaway-bot-web`
     - Environment: `Python`
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `gunicorn app:app`
     - Select the "Free" plan

3. **Configure Environment Variables**
   - Add the following environment variables:
     - `FLASK_SECRET_KEY`: A random secret key
     - `TWITCH_CLIENT_ID`: Your Twitch API client ID
     - `TWITCH_CLIENT_SECRET`: Your Twitch API client secret
     - `BOT_TOKEN`: Your Twitch bot token
     - `REDIRECT_URI`: Your Render URL + `/auth/twitch/callback` (e.g., `https://your-app-name.onrender.com/auth/twitch/callback`)
     - `FLASK_ENV`: `production`
     - `SESSION_COOKIE_SECURE`: `true`
     - `SESSION_COOKIE_HTTPONLY`: `true`
     - `SESSION_COOKIE_SAMESITE`: `Lax`
     - `LOG_LEVEL`: `INFO`
     - `RATE_LIMIT_DEFAULT`: `10/minute`
     - `RATE_LIMIT_AUTH`: `5/minute`

4. **Deploy the Service**
   - Click "Create Web Service"
   - Render will build and deploy your application
   - Once deployed, you'll receive a URL for your application

### Important Notes

- The free tier of Render has limitations:
  - Services may spin down after 15 minutes of inactivity
  - Limited bandwidth and compute hours
  - The first request after inactivity may be slow

- The application uses SQLite for data storage, which is suitable for the free tier
- The chatbot runs in a separate thread within the web service
- For production use with higher traffic, consider upgrading to a paid plan

## License

This project is licensed under the MIT License - see the LICENSE file for details. 