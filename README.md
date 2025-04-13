# Twitch Giveaway Bot

A Flask-based web application for managing Twitch giveaways.

## Features

- Create and manage giveaways
- Connect to Twitch chat
- Automatically select winners
- Track giveaway history
- Secure authentication with Twitch

## Local Development

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file based on `.env.example`
4. Run the application: `python app.py`

## Deployment on Render

### Prerequisites

- A Render account
- A PostgreSQL database (Render provides this)
- Twitch API credentials

### Steps

1. Create a new Web Service on Render
2. Connect your GitHub repository
3. Configure the service:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Environment Variables: Copy from `.env.example` and fill in your values

4. Create a new Worker Service on Render
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python worker.py`
   - Environment Variables: Copy from `.env.example` and fill in your values

5. Create a PostgreSQL database on Render
   - Name: `giveaway_db`
   - User: `giveaway_user`
   - Password: Generate a secure password

6. Update the `DATABASE_URL` environment variable in both services to point to your Render PostgreSQL database

7. Deploy the services

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `FLASK_SECRET_KEY`: Secret key for Flask sessions
- `TWITCH_CLIENT_ID`: Twitch API client ID
- `TWITCH_CLIENT_SECRET`: Twitch API client secret
- `BOT_TOKEN`: Twitch bot OAuth token
- `REDIRECT_URI`: OAuth redirect URI (must match your Twitch app settings)

## License

MIT 