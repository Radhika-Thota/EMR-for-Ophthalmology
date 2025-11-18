import psycopg2
import ast
import json

# Database Configuration (Make sure this matches your app.py)
DB_HOST = "localhost"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASS = "Radha@99"

def migrate_test_results():
    """Migrates old, improperly formatted test_results strings to valid JSONB."""
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cursor = conn.cursor()

        print("Fetching records with potential legacy data...")
        # Select records where test_results might be a malformed string
        cursor.execute("SELECT id, test_results FROM patient_medical_records WHERE test_results IS NOT NULL")
        records_to_migrate = cursor.fetchall()
        
        migrated_count = 0
        for record_id, raw_data_string in records_to_migrate:
            # Check if the data is already valid JSON
            if isinstance(raw_data_string, dict):
                print(f"Record {record_id} is already a dictionary. Skipping.")
                continue

            try:
                # Use ast.literal_eval to safely parse the Python dictionary-like string
                parsed_dict = ast.literal_eval(raw_data_string)
                
                # Convert the Python dictionary to a valid JSON string
                new_json_string = json.dumps(parsed_dict)
                
                # Update the database record with the new, valid JSON string
                cursor.execute(
                    "UPDATE patient_medical_records SET test_results = %s WHERE id = %s",
                    (new_json_string, record_id)
                )
                migrated_count += 1
                print(f"Successfully migrated record ID {record_id}.")

            except (ValueError, SyntaxError) as e:
                print(f"Could not parse record {record_id}. Data is likely not a dictionary. Error: {e}")
        
        conn.commit()
        print(f"\nMigration complete. {migrated_count} records were updated.")

    except psycopg2.Error as e:
        print(f"Database connection error: {e}")
    finally:
        if conn:
            cursor.close()
            conn.close()

if __name__ == '__main__':
    migrate_test_results()