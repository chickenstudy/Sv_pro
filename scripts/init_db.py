import os
import psycopg2
from urllib.parse import urlparse
import sys

# Đọc cấu hình từ biến môi trường, fallback về mặc định
POSTGRES_USER = os.getenv("POSTGRES_USER", "svpro_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "svpro_pass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "svpro_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

# Kết nối database
conn_string = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

try:
    print(f"Connecting to database at {POSTGRES_HOST}:{POSTGRES_PORT}...")
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cursor = conn.cursor()

    # Đường dẫn tới schema
    schema_path = os.path.join(os.path.dirname(__file__), "sql", "schema.sql")
    
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    print("Executing schema migration...")
    cursor.execute(schema_sql)
    print("Database initialization completed successfully!")
    
except Exception as e:
    print(f"Error during database initialization: {e}")
    sys.exit(1)
finally:
    if 'conn' in locals() and conn:
        cursor.close()
        conn.close()
