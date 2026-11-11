import getpass
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.core.security import hash_password
from app.database.connection import SessionLocal
from app.models.user import User, UserRole


def main() -> None:
    print("=== SentinelX Admin Provisioning CLI ===")
    email = settings.ADMIN_EMAIL
    password = settings.ADMIN_PASSWORD

    if not email:
        email = input("Enter admin email: ").strip()

    if not password:
        password = getpass.getpass("Enter admin password (min 8 chars): ")
        confirm_pass = getpass.getpass("Confirm admin password: ")
        if password != confirm_pass:
            print("Error: Passwords do not match.")
            sys.exit(1)

    if len(password) < 8:
        print("Error: Password must be at least 8 characters long.")
        sys.exit(1)

    norm_email = email.lower().strip()
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == norm_email).first()
        hashed = hash_password(password)

        if existing:
            existing.hashed_password = hashed
            existing.role = UserRole.ADMIN
            existing.is_active = True
            db.commit()
            print(f"Success: Existing user '{norm_email}' updated to active ADMIN role.")
        else:
            admin_user = User(
                email=norm_email,
                hashed_password=hashed,
                full_name="System Administrator",
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(admin_user)
            db.commit()
            print(f"Success: Admin user '{norm_email}' provisioned successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
