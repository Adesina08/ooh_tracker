# OOH Tracker

This project tracks out-of-home consumption data using Flask and PostgreSQL.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your values.
3. Run the application:
   ```bash
   python app.py
   ```

## Environment Variables

The application reads configuration from environment variables. Create a `.env` file with the following keys:

```
SECRET_KEY=your_secret_key
DB_HOST=your_database_host
DB_NAME=your_database_name
DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_PORT=5432
UPLOAD_FOLDER=static/uploads
ALLOWED_EXTENSIONS=png,jpg,jpeg,mp4,mov,webm
MAX_CONTENT_LENGTH=16777216
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@example.com
MAIL_PASSWORD=your_email_password
MAIL_USE_TLS=True
MAIL_USE_SSL=False
MAIL_DEFAULT_SENDER=your_email@example.com
```

Only `SECRET_KEY`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `MAIL_USERNAME`, `MAIL_PASSWORD`, and `MAIL_DEFAULT_SENDER` are strictly required. The rest have sane defaults.
