import os

from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

load_dotenv()

try:
    DATABASE_URL = os.environ["DATABASE_URL"]
except KeyError:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
    ) from None

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)
