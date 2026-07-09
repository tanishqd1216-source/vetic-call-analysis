import pandas as pd
import sys
import os
import math
import unicodedata

def clean_text(text):
    if not isinstance(text, str):
        text = str(text)
    # Remove hidden formatting and bidirectional Unicode characters
    return ''.join(c for c in text if unicodedata.category(c) not in ('Cf', 'Cc'))

def get_sql_type(dtype):
    if pd.api.types.is_integer_dtype(dtype):
        return 'INTEGER'
    elif pd.api.types.is_float_dtype(dtype):
        return 'NUMERIC'
    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return 'TIMESTAMP'
    elif pd.api.types.is_bool_dtype(dtype):
        return 'BOOLEAN'
    else:
        return 'TEXT'

def excel_to_sql(excel_file, sql_file, table_name):
    print(f"Reading {excel_file}...")
    df = pd.read_excel(excel_file)
    
    # Generate CREATE TABLE statement
    columns = []
    for col_name, dtype in df.dtypes.items():
        # sanitize column name
        safe_col_name = clean_text(col_name).strip().lower().replace(' ', '_').replace('-', '_')
        sql_type = get_sql_type(dtype)
        columns.append(f"\"{safe_col_name}\" {sql_type}")
        
    create_table_stmt = f"CREATE TABLE {table_name} (\n    " + ",\n    ".join(columns) + "\n);\n\n"
    
    # Generate INSERT statements
    insert_stmts = []
    
    # prepare column names for insert
    safe_cols = [f"\"{clean_text(c).strip().lower().replace(' ', '_').replace('-', '_')}\"" for c in df.columns]
    
    for _, row in df.iterrows():
        values = []
        for val in row:
            if pd.isna(val):
                values.append('NULL')
            elif isinstance(val, (int, float)) and not math.isnan(val):
                values.append(str(val))
            else:
                # Clean hidden unicode, then escape single quotes
                safe_val = clean_text(val).replace("'", "''")
                values.append(f"'{safe_val}'")
                
        insert_stmt = f"INSERT INTO {table_name} ({', '.join(safe_cols)}) VALUES ({', '.join(values)});"
        insert_stmts.append(insert_stmt)
        
    with open(sql_file, 'w', encoding='utf-8') as f:
        f.write(create_table_stmt)
        f.write("\n".join(insert_stmts))
        
    print(f"Successfully wrote {len(insert_stmts)} records to {sql_file}")

if __name__ == "__main__":
    excel_path = r"c:\Users\hp\Desktop\sample data\sample_ticket_summary.xlsx"
    sql_path = r"c:\Users\hp\Desktop\sample data\sample_ticket_summary.sql"
    table_name = "ticket_summary"
    excel_to_sql(excel_path, sql_path, table_name)
