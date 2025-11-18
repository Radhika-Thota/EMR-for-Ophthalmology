import psycopg2

# Database Configuration (Make sure this matches your app.py)
DB_HOST = "localhost"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASS = "Radha@99"

def alter_patient_edit_history_table():
    """Connects to the database and alters the patient_edit_history table to allow NULL patient_id."""
    conn = None
    try:
        # Establish a connection to the database
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cursor = conn.cursor()

        # SQL command to alter the table
        alter_table_query = "ALTER TABLE patient_edit_history ALTER COLUMN patient_id DROP NOT NULL;"

        print("Attempting to alter the 'patient_edit_history' table...")
        cursor.execute(alter_table_query)
        conn.commit()
        print("Table 'patient_edit_history' has been altered successfully. The 'patient_id' column now accepts NULL values.")

    except psycopg2.Error as e:
        print(f"Database connection or query error: {e}")
        if conn:
            conn.rollback() # Rollback in case of error
    finally:
        # Close the connection and cursor
        if conn:
            cursor.close()
            conn.close()

if __name__ == '__main__':
    alter_patient_edit_history_table()
