"""
scripts/hash_admin_password.py

Run this ONCE to generate a secure hash of your admin password.
Then update ADMIN_PASSWORD in your Railway environment variables
with the output — replace the plaintext value entirely.

Usage:
    python scripts/hash_admin_password.py
"""
import getpass
from werkzeug.security import generate_password_hash

def main():
    print("=" * 60)
    print("  Magni Admin Password Hasher")
    print("=" * 60)
    print()
    print("Generates a PBKDF2-SHA256 hash of your password.")
    print("Copy the output into ADMIN_PASSWORD on Railway.\n")

    password = getpass.getpass("Enter your admin password: ")
    confirm  = getpass.getpass("Confirm your admin password: ")

    if password != confirm:
        print("\n❌ Passwords do not match. Exiting.")
        return

    if len(password) < 12:
        print("\n⚠️  Warning: password shorter than 12 characters.")
        if input("Continue anyway? (y/N): ").strip().lower() != "y":
            print("Exiting.")
            return

    hashed = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)

    print("\n" + "=" * 60)
    print("✅ Your hashed password — copy this entire line:\n")
    print(hashed)
    print("\n" + "=" * 60)
    print("\nNext steps:")
    print("1. Railway dashboard → Variables → ADMIN_PASSWORD")
    print("2. Replace the plaintext value with the hash above")
    print("3. Redeploy — your login password stays the same")

if __name__ == "__main__":
    main()
