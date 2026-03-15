import smtplib
import ssl
import time


def test_gmail_connection(email, password, test_mode=1):
    """Test Gmail connection with different methods."""
    print(f"Testing connection for {email} (Method {test_mode})")

    try:
        if test_mode == 1:
            # Method 1: Standard TLS on port 587
            print("Using TLS on port 587...")
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.ehlo()
            server.starttls()
            server.login(email, password)
        elif test_mode == 2:
            # Method 2: SSL on port 465
            print("Using SSL on port 465...")
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context)
            server.login(email, password)
        elif test_mode == 3:
            # Method 3: TLS with explicit SSL context
            print("Using TLS with explicit SSL context...")
            context = ssl.create_default_context()
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.ehlo()
            server.starttls(context=context)
            server.login(email, password)

        print("✓ CONNECTION SUCCESSFUL!")
        server.quit()
        return True

    except Exception as e:
        print(f"✗ CONNECTION FAILED: {str(e)}")
        return False


def main():
    print("=" * 80)
    print("GMAIL CONNECTION DIAGNOSTIC TOOL")
    print("=" * 80)

    email = "parthsethi@180dc.org"

    print("\nThis tool will try different methods to connect to Gmail.")
    print("For this to work, you MUST:")
    print("1. Have 2-Step Verification enabled on your Google Account")
    print("2. Have generated an App Password for mail")

    # Check if using regular password
    password = input("\nEnter your App Password (16 characters, no spaces): ")
    if len(password) != 16 or " " in password:
        print("WARNING: This doesn't look like a valid App Password.")
        print("App Passwords should be exactly 16 characters with no spaces.")
        proceed = input("Do you want to proceed anyway? (y/n): ")
        if proceed.lower() != "y":
            return

    print("\nTrying 3 different connection methods...")

    # Try different authentication methods
    success = False
    for method in range(1, 4):
        if test_gmail_connection(email, password, method):
            success = True
            print(
                f"\nMethod {method} was successful! Use these settings in credentials.py:"
            )

            if method == 1:
                print("smtp_server = 'smtp.gmail.com'")
                print("smtp_port = 587")
            elif method == 2:
                print("smtp_server = 'smtp.gmail.com'")
                print("smtp_port = 465")
                print(
                    "\nAnd change your script to use smtplib.SMTP_SSL() instead of smtplib.SMTP()"
                )
            elif method == 3:
                print("smtp_server = 'smtp.gmail.com'")
                print("smtp_port = 587")
                print("\nAnd make sure to use an SSL context with starttls()")

            break
        else:
            print(f"Method {method} failed. Trying next method...\n")
            time.sleep(1)

    if not success:
        print("\nAll connection methods failed. Please check:")
        print(
            "1. Verify that 2-Step Verification is enabled: https://myaccount.google.com/security"
        )
        print(
            "2. Generate a new App Password: https://myaccount.google.com/apppasswords"
        )
        print("3. If using a Google Workspace account, check with your administrator")
        print(
            "4. Try with a personal Gmail account to isolate if it's an account-specific issue"
        )


if __name__ == "__main__":
    main()
