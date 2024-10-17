import os
import boto3
import sqlite3
from datetime import datetime
from config import DATABASE_FILE, BUCKET_NAME, DT_RULE  # Import the variables from config

# Set up your S3 client (assumes credentials are configured)
s3 = boto3.client('s3')

# Standardized datetime format
DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S.%f'[:-4]

# Helper function to format datetime based on DT_RULE
def format_datetime():
    now = datetime.now()
    if DT_RULE == 'seconds':
        return now.strftime('%Y%m%d%H%M%S')
    elif DT_RULE == 'hours':
        return now.strftime('%Y%m%d%H')
    elif DT_RULE == 'days':
        return now.strftime('%Y%m%d')
    elif DT_RULE == 'weeks':
        return now.strftime('%Y%U')
    elif DT_RULE == 'months':
        return now.strftime('%Y%m')
    elif DT_RULE == 'years':
        return now.strftime('%Y')
    elif DT_RULE == 'never':
        return ''
    else:
        raise ValueError("Invalid DT_RULE value")

def ensure_database_exists():
    """Ensures that the s3_files table exists in the database."""
    if not os.path.exists(DATABASE_FILE):
        print(f"Database file {DATABASE_FILE} does not exist. Creating it now.")
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    # Create the s3_files table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS s3_files (
        filename TEXT PRIMARY KEY,
        size INTEGER,
        updated_at TEXT
      )
    ''')
    conn.commit()
    conn.close()

def build_s3_filename(mac, filename):
    """Builds the S3 filename string based on DT_RULE."""
    datetime_str = format_datetime()
    if datetime_str:
        return f"{mac}/{datetime_str}/{filename}"
    else:
        return f"{mac}/{filename}"

def update_local_database():
    """Updates the local database to reflect what is in the S3 bucket."""
    ensure_database_exists()
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    # Get the list of files from S3
    try:
        s3_files = s3.list_objects_v2(Bucket=BUCKET_NAME).get('Contents', [])
    except s3.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'AllAccessDisabled':
            print("Access to the S3 bucket is disabled. Please check permissions.")
            return
        else:
            raise
    
    # Update the s3_files table to reflect the current state of the S3 bucket
    existing_files = {row[0]: row[1] for row in cursor.execute('SELECT filename, size FROM s3_files').fetchall()}
    current_files = {file['Key']: file['Size'] for file in s3_files}

    # Insert or update entries from S3
    for filename, size in current_files.items():
        updated_at = datetime.now().strftime(DATETIME_FORMAT)
        if filename in existing_files:
            if existing_files[filename] != size:
                cursor.execute('''
                    UPDATE s3_files SET size = ?, updated_at = ? WHERE filename = ?
                ''', (size, updated_at, filename))
                print(f"Updated file in database: {filename}")
        else:
            cursor.execute('''
                INSERT INTO s3_files (filename, size, updated_at)
                VALUES (?, ?, ?)
            ''', (filename, size, updated_at))
            print(f"Added to database: {filename}")

    # Delete entries that are no longer in S3
    for filename in existing_files:
        if filename not in current_files:
            cursor.execute('DELETE FROM s3_files WHERE filename = ?', (filename,))
            print(f"Deleted from database: {filename}")
    
    conn.commit()
    conn.close()

def needFile(mac, filename, size):
    """Checks if a file with the given filename and size is needed in the local cache."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    # Build the filename string
    s3_filename = build_s3_filename(mac, filename)

    # Check if the file exists with the same size
    cursor.execute('SELECT filename, size FROM s3_files WHERE filename = ? AND size = ?', (s3_filename, size))
    result = cursor.fetchone()
    conn.close()

    # If no exact match is found, return True
    return result is None

def upload_files(data_directory):
    """Uploads files from the local directory if they are not already in S3 and updates the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    # Iterate through each MAC address folder
    for mac in os.listdir(data_directory):
        mac_path = os.path.join(data_directory, mac)
        if not os.path.isdir(mac_path):
            continue

        # Iterate through each file in the MAC address folder
        for filename in os.listdir(mac_path):
            file_path = os.path.join(mac_path, filename)
            if not os.path.isfile(file_path):
                continue

            size = os.path.getsize(file_path)

            if needFile(mac, filename, size):
                # Build the S3 key
                s3_key = build_s3_filename(mac, filename)

                # Upload file to S3
                s3.upload_file(file_path, BUCKET_NAME, s3_key)
                print(f'Uploaded: {s3_key}')

                # Update the database with the newly uploaded file
                updated_at = datetime.now().strftime(DATETIME_FORMAT)
                cursor.execute('''
                    INSERT OR REPLACE INTO s3_files (filename, size, updated_at)
                    VALUES (?, ?, ?)
                ''', (s3_key, size, updated_at))
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    update_local_database()
