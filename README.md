# RaffleBot - Twitch Giveaway Platform

## Live Demo
- **Website:** [https://rafflebot-site.onrender.com](https://rafflebot-site.onrender.com)
- **Chatbot:** [https://rafflebot-chatbot.onrender.com](https://rafflebot-chatbot.onrender.com)
- **Video Demo:** [https://youtu.be/7ylFb5CqVsI](https://youtu.be/7ylFb5CqVsI)

## Overview
RaffleBot is a comprehensive Twitch giveaway platform consisting of two integrated applications:

1. **Main Web Application** - Allows streamers to create, manage, and monitor giveaways
2. **Twitch Chatbot** - Connects to Twitch chat to handle viewer entry and prize distribution

## Features
- Secure Twitch OAuth authentication
- Create and customize giveaways with different frequency and threshold settings
- Easily manage item inventory for each giveaway
- Real-time giveaway management through Twitch chat
- Automatic winner selection based on configurable criteria
- View your winnings as a viewer
- Security features including rate limiting and protection against common web vulnerabilities

## Technology Stack
- **Backend:** Flask (Python)
- **Database:** SQLAlchemy with PostgreSQL
- **Authentication:** Twitch OAuth
- **Twitch Integration:** TwitchIO
- **Deployment:** Render

## Setup and Installation

### Prerequisites
- Python 3.8+
- PostgreSQL (optional, SQLite for local development)
- Twitch Developer Account (for API credentials)

### Installation
1. Clone the repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Set up the database:
   ```
   python models.py
   ```
4. Start the main application:
   ```
   python app.py
   ```
5. Start the chatbot in a separate terminal:
   ```
   python chatbot.py
   ```

## Usage

### For Streamers
1. Log in with your Twitch account
2. Create a new giveaway from the dashboard
3. Add items to the giveaway
4. Start the giveaway to activate the chatbot in your channel
5. The chatbot will automatically select winners based on your settings

### For Viewers
1. Join a stream where the RaffleBot is active
2. Type `!enter` in the chat to participate
3. If you win, log in to the RaffleBot website to claim your prize

## Chatbot Commands
- `!enter` - Enter the active giveaway
- `!startgiveaway` - (Streamer only) Start a giveaway
- `!endgiveaway` - (Streamer only) End the active giveaway
- `!listgiveaways` - (Streamer only) List available giveaways


