# Chat

Anonymous chat with admin support using Flask and SocketIO.

## Deploy to Railway

1. Push this code to GitHub.
2. Connect your GitHub repo to Railway.
3. Set environment variables in Railway dashboard:
   - `SECRET_KEY`: A random secret key for Flask sessions.
   - `ADMIN_PASS`: Password for admin login.
   - `DATABASE_URL`: Railway provides PostgreSQL automatically.
   - `ENVIRONMENT`: Set to `production`.
4. Railway will auto-detect the Python app and deploy it.