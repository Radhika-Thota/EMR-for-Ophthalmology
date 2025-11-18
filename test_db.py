import psycopg2

DB_HOST = "localhost"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASS = "Radha@99" # Ensure this matches your actual password

try:
    conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
    cur = conn.cursor()
    cur.execute("SELECT version();")
    db_version = cur.fetchone()
    print(f"Successfully connected to PostgreSQL! Version: {db_version[0]}")
    cur.close()
    conn.close()
except psycopg2.Error as e:
    print(f"Failed to connect to database: {e}")