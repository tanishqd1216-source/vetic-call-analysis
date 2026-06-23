import pandas as pd
from sqlalchemy import create_engine

# Database connection URL
DATABASE_URL = "postgresql://postgres:postgres123@localhost:5432/nps_database"
engine = create_engine(DATABASE_URL)

csv_file = "Call Analysis Model - Analysis.csv"
table_name = "call_analysis"

print(f"Loading {csv_file} into PostgreSQL table '{table_name}'...")
try:
    df = pd.read_csv(csv_file)
    # Write the dataframe to the database
    df.to_sql(table_name, engine, if_exists='replace', index=False)
    print(f"Successfully loaded {len(df)} rows into '{table_name}'.")
except Exception as e:
    print(f"Error loading data: {e}")
