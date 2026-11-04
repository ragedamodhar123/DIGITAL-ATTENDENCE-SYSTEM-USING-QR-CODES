"""Entry point.

Development:  flask --app wsgi run --debug
Production:   waitress-serve --listen=0.0.0.0:8000 wsgi:app
"""
from app import create_app

app = create_app()