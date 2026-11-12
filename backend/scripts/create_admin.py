import argparse
import getpass
from pathlib import Path
import sys

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.core.security import hash_password
from app.database.connection import SessionLocal
from app.models.user import User, UserRole


def parse_args() -> argparse.Namespace:
    # Explicitly disallow any CLI password argument for security
    for arg in sys.argv[1:]:
        if arg.startswith("--pass") or arg == "-p":
            print("SECURITY ERROR: Passing passwords via CLI arguments is strictly forbidden. Use interactive prompt or ADMIN_PASSWORD in .env.")
            sys.exit(1)

    parser = argparse.ArgumentParser(
        description="SentinelX Admin Provisioning CLI. Creates or promotes an admin user."
    )
    parser.add_argument(
        "--email",
        "-e",
        type=str,
        help="Admin email address (overrides ADMIN_EMAIL in .env)",
    )
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default="System Administrator",
        help="Admin full name (default: System Administrator)",
    )
    return parser.parse_args()


def main() -> None:
    print("=== SentinelX Admin Provisioning CLI ===")
    args = parse_args()

    email = args.email or settings.ADMIN_EMAIL
    name = args.name
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
            if args.name != "System Administrator":
                existing.full_name = args.name
            db.commit()
            print(f"Success: Existing user '{norm_email}' updated to active ADMIN role.")
        else:
            admin_user = User(
                email=norm_email,
                hashed_password=hashed,
                full_name=name,
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
