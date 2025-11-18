import psycopg2
import psycopg2.extras # Needed for DictCursor in app.py
from werkzeug.security import generate_password_hash
import uuid 

# Database Configuration (Match these with app.py)
DB_HOST = "localhost"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASS = "Radha@99"

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        return conn
    except psycopg2.Error as e:
        print(f"Database connection failed: {e}")
        return None

def ensure_uhid_column():
    """Adds the uhid column to the patients table if it does not exist."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        print("Checking for missing 'uhid' column in patients table...")

        # Use PostgreSQL's DO block to safely check and add the column
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='patients' AND column_name='uhid'
                ) THEN
                    ALTER TABLE patients ADD COLUMN uhid VARCHAR(50) UNIQUE;
                    -- After adding, update existing records to have a placeholder value
                    UPDATE patients SET uhid = 'TEMP-UHID-' || mrn WHERE uhid IS NULL OR uhid = '';
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_patients_uhid ON patients(uhid);
                    RAISE NOTICE 'Added and populated UHID column to patients table.';
                ELSE
                    -- If the column exists, ensure existing NULLs are updated to allow redirection
                    UPDATE patients SET uhid = 'TEMP-UHID-' || mrn WHERE uhid IS NULL OR uhid = '';
                    RAISE NOTICE 'UHID column already exists, ensuring no NULL values.';
                END IF;
            END$$;
        """)
        conn.commit()
        print("✅ UHID column check and data population complete.")
    except Exception as e:
        print(f"❌ Error ensuring UHID column: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            if 'cursor' in locals(): cursor.close()
            conn.close()

def ensure_prescription_columns():
    """
    Adds necessary prescription-related columns to the patient_prescriptions table.
    This is vital for existing installations.
    """
    conn = get_db_connection()
    if not conn:
        return
    
    # Define columns to be added: (column_name, data_type)
    columns_to_add = [
        ('lens_type', 'VARCHAR(100)'),
        ('systemic_medication', 'TEXT'),
        ('iol_notes', 'TEXT'),
        ('patient_instructions', 'TEXT'),
        ('follow_up_date', 'DATE'),
    ]

    try:
        cursor = conn.cursor()
        print("Checking for missing columns in patient_prescriptions table...")

        for col_name, col_type in columns_to_add:
            print(f"Checking for column: {col_name}...")
            # Use PostgreSQL's DO block for safe, idempotent column addition
            cursor.execute(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='patient_prescriptions' AND column_name='{col_name}'
                    ) THEN
                        ALTER TABLE patient_prescriptions ADD COLUMN {col_name} {col_type};
                        RAISE NOTICE 'Added column {col_name} to patient_prescriptions.';
                    END IF;
                END$$;
            """)
        
        conn.commit()
        print("✅ Prescription column checks and additions complete.")

    except Exception as e:
        print(f"❌ Error ensuring prescription columns: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            if 'cursor' in locals(): cursor.close()
            conn.close()


def create_tables():
    """Connects to the database and creates necessary tables."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()

        # Users table (assuming correct definition based on FKs in other tables)
        
        # Patients table (***NOTE: UHID IS DEFINED HERE, BUT THE ensure_uhid_column() 
        # IS THE FALLBACK FOR EXISTING DATABASES***)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id SERIAL PRIMARY KEY,
                mrn VARCHAR(20) UNIQUE NOT NULL,
                uhid VARCHAR(50) UNIQUE, -- Defined here for NEW installations
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100) NOT NULL,
                dob DATE,
                gender VARCHAR(10),
                address TEXT,
                phone VARCHAR(20),
                email VARCHAR(100)
            );
        """)
        print("Table 'patients' ensured.")
        
        # Patient Medical Records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patient_medical_records (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
                visit_date TIMESTAMP NOT NULL DEFAULT NOW(),
                diagnosis TEXT NOT NULL,
                treatment TEXT,
                test_results JSONB,
                prescribed_drops JSONB,
                prescribed_medication JSONB,
                surgery_recommendation TEXT,
                risk_assessment_score INTEGER,
                risk_assessment_category VARCHAR(50),
                risk_assessment_implication TEXT
            );
        """)
        print("Table 'patient_medical_records' ensured.")

        # Audit Logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT NOW(),
                user_id INTEGER, 
                action TEXT NOT NULL,
                details TEXT
            );
        """)
        print("Table 'audit_logs' ensured.")

        # Patient Edit History table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patient_edit_history (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
                editor_id INTEGER NOT NULL, 
                field_name VARCHAR(100) NOT NULL,
                old_value TEXT,
                new_value TEXT,
                edited_at TIMESTAMP DEFAULT NOW()
            );
        """)
        print("Table 'patient_edit_history' ensured.")

        # Patient Prescriptions table (Updated to include all form fields)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patient_prescriptions (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                medical_record_id INTEGER REFERENCES patient_medical_records(id) ON DELETE CASCADE,
                
                -- Refraction/Spectacle Data
                spectacle_lens JSONB,
                lens_type VARCHAR(100),          -- NEW
                
                -- Medication Data
                drops JSONB,
                medications JSONB,

                -- Notes and Follow-up
                systemic_medication TEXT,        -- NEW
                surgery_recommendation TEXT,
                iol_notes TEXT,                  -- NEW
                patient_instructions TEXT,       -- NEW
                follow_up_date DATE,             -- NEW
                
                created_by INTEGER, 
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """)
        print("✅ patient_prescriptions table verified/created with full schema.")

        # Insert a default admin user if not exists (omitted for brevity)
        admin_username = "admin"
        admin_password_hash = generate_password_hash("adminpass")
        # NOTE: You'll need to uncomment and adjust the following block if your 'users' table is included here:
        # cursor.execute("SELECT COUNT(*) FROM users WHERE username = %s", (admin_username,))
        # if cursor.fetchone()[0] == 0:
        #     cursor.execute(
        #         "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
        #         (admin_username, admin_password_hash, 'admin')
        #     )
        #     print(f"Default admin user '{admin_username}' created.")
        
        conn.commit()
        print("Database tables created/ensured successfully!")

    except psycopg2.Error as e:
        print(f"Error creating tables or connecting to database: {e}")
        if conn:
            conn.rollback() # Rollback in case of error
    finally:
        if conn:
            if 'cursor' in locals(): cursor.close()
            conn.close()


if __name__ == '__main__':
    create_tables()
    ensure_uhid_column()
    ensure_prescription_columns() # CRITICAL: Ensure existing installations get the new columns



