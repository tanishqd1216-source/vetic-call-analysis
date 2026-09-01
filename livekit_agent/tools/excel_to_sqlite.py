import pandas as pd
import sqlite3

def excel_to_sqlite(excel_file, sqlite_db, table_name):
    print(f"Reading {excel_file}...")
    df = pd.read_excel(excel_file)
    
    # Clean up column names
    df.columns = [str(c).strip().lower().replace(' ', '_').replace('-', '_') for c in df.columns]
    
    # Connect to SQLite
    print(f"Connecting to {sqlite_db}...")
    conn = sqlite3.connect(sqlite_db)
    
    # Write to SQLite
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    
    # Verify
    count = pd.read_sql(f"SELECT COUNT(*) as cnt FROM {table_name}", conn).iloc[0]['cnt']
    print(f"Successfully wrote {count} records to table '{table_name}' in {sqlite_db}!")
    
    conn.close()

if __name__ == "__main__":
    excel_path = r"c:\Users\hp\Documents\sample data\sample_ticket_summary.xlsx"
    db_path = r"c:\Users\hp\Documents\sample data\vetic_data.db"
    excel_to_sqlite(excel_path, db_path, "ticket_summary")
