import argparse
import getpass

from .store import AuthStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Ducklingo user")
    parser.add_argument("username")
    parser.add_argument("--db", default="data/ducklingo.db")
    args = parser.parse_args()
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords do not match")
    AuthStore(args.db).create_user(args.username, first, "admin")
    print(f"Created admin: {args.username}")


if __name__ == "__main__":
    main()
