import os
import sys
import shutil
import sqlite3
import time
import zipfile

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPENWA_DATA_DIR = r"C:\Users\Bishwajit Bag\OpenWA\data"
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

timestamp = time.strftime("%Y%m%d_%H%M%S")
zip_filename = f"solarise_state_backup_{timestamp}.zip"
zip_path = os.path.join(BACKUP_DIR, zip_filename)

print(f"=== Creating Production State Backup: {zip_filename} ===")

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
    # 1. Backup solarise_leads.db safely using SQLite backup API
    leads_db_source = os.path.join(PROJECT_DIR, "solarise_leads.db")
    leads_db_temp = os.path.join(BACKUP_DIR, f"temp_leads_{timestamp}.db")
    if os.path.exists(leads_db_source):
        print("Backing up SQLite leads database via backup API...")
        src_conn = sqlite3.connect(leads_db_source)
        dst_conn = sqlite3.connect(leads_db_temp)
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
        zipf.write(leads_db_temp, arcname="solarise_leads.db")
        os.remove(leads_db_temp)
        print("  - solarise_leads.db added to backup archive.")

    # 2. Backup OpenWA session files (sqlite and session folders)
    if os.path.exists(OPENWA_DATA_DIR):
        print(f"Backing up OpenWA data from {OPENWA_DATA_DIR}...")
        for root, dirs, files in os.walk(OPENWA_DATA_DIR):
            for file in files:
                # Skip ephemeral logs or socket files if any
                if file.endswith((".log", ".tmp")):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, OPENWA_DATA_DIR)
                arc_name = os.path.join("openwa_data", rel_path)
                try:
                    zipf.write(file_path, arcname=arc_name)
                except Exception as e:
                    print(f"  Warning: could not add {file}: {e}")
        print("  - OpenWA data added to backup archive.")

archive_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
print(f"Backup complete! Archive saved at: {zip_path} ({archive_size_mb:.2f} MB)")
