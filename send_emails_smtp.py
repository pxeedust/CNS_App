import json
import time
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# Create a credentials.py file (or update if it exists)
def create_credentials_file():
    """Create or update credentials.py file"""
    if not os.path.exists("credentials.py"):
        print("Creating credentials.py file...")

        # Get Gmail credentials
        email = (
            input("Enter your Gmail address (parthsethi@180dc.org): ")
            or "parthsethi@180dc.org"
        )
        password = input("Enter your Gmail app password: ")

        # Write to file
        with open("credentials.py", "w") as f:
            f.write(f"# Gmail SMTP credentials\n")
            f.write(f"sender_email = '{email}'\n")
            f.write(f"sender_password = '{password}'\n")
            f.write(f"smtp_server = 'smtp.gmail.com'\n")
            f.write(f"smtp_port = 587\n")

        print("Credentials file created successfully!")
    else:
        print("credentials.py file already exists")


def read_emails_from_json(json_file_path):
    """Read emails from JSON file."""
    with open(json_file_path, "r") as file:
        return json.load(file)


def send_email(
    subject,
    message,
    sender_email,
    sender_password,
    recipient_email,
    cc_emails,
    smtp_server,
    smtp_port,
):
    """Send email using SMTP."""
    try:
        # Create message
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = recipient_email

        # Add CC if provided
        if cc_emails:
            msg["Cc"] = ", ".join(cc_emails)

        # Add message body
        msg.attach(MIMEText(message))

        # Connect to server and send
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls()
        server.login(sender_email, sender_password)

        # Get all recipients
        all_recipients = [recipient_email]
        if cc_emails:
            all_recipients.extend(cc_emails)

        server.send_message(msg, from_addr=sender_email, to_addrs=all_recipients)
        server.quit()

        return True, "Email sent successfully!"
    except Exception as e:
        return False, f"Error sending email: {str(e)}"


def main():
    print("=" * 80)
    print("GMAIL SMTP EMAIL SENDER")
    print("This script uses Python's smtplib to send emails via Gmail")
    print("=" * 80)

    # Make sure credentials file exists
    create_credentials_file()

    # Import credentials after creating the file if needed
    try:
        from credentials import sender_email, sender_password, smtp_server, smtp_port
    except ImportError:
        print("Error importing credentials. Please check your credentials.py file.")
        return

    # Set CC emails
    cc_emails = ["arghyadip.pal@180dc.org", "mishra.moulik@180dc.org"]

    # Read emails from JSON file
    json_file_path = "generated_emails.json"
    try:
        emails_data = read_emails_from_json(json_file_path)
        print(f"\nFound {len(emails_data)} emails to send.")
    except Exception as e:
        print(f"Error reading {json_file_path}: {str(e)}")
        return

    # Test connection before proceeding
    print("\nTesting SMTP connection with a test email...")
    success, message = send_email(
        "Test Connection",
        "This is a test message to verify SMTP connection.",
        sender_email,
        sender_password,
        sender_email,  # Send to self for testing
        [],
        smtp_server,
        smtp_port,
    )

    if not success:
        print(f"Test email failed: {message}")
        print("Please check your Gmail credentials and try again.")
        return
    else:
        print("Test email sent successfully! Proceeding with sending emails.")

    # Track success and failures
    success_count = 0
    failure_count = 0
    failed_recipients = []

    # Ask for confirmation
    confirm = input(
        f"\nDo you want to send {len(emails_data)} emails? (yes/no): "
    ).lower()
    if confirm != "yes":
        print("Operation cancelled.")
        return

    # Send emails
    for i, email_data in enumerate(emails_data):
        recipient_name = email_data["contact"]
        recipient_email = email_data["email_address"]
        email_content = email_data["generated_email"]

        # Get subject from JSON if available, otherwise default
        if "subject" in email_data:
            subject = email_data["subject"]
        else:
            subject = "Introduction: 180 Degrees Consulting, IIT Kharagpur"

        print(
            f"\nSending email {i+1}/{len(emails_data)} to {recipient_name} <{recipient_email}>"
        )
        print(f"Subject: {subject}")

        success, message = send_email(
            subject,
            email_content,
            sender_email,
            sender_password,
            recipient_email,
            cc_emails,
            smtp_server,
            smtp_port,
        )

        if success:
            print(f"✓ Email sent successfully to {recipient_name}")
            success_count += 1
        else:
            print(f"✗ Failed to send email to {recipient_name}: {message}")
            failure_count += 1
            failed_recipients.append(
                {"name": recipient_name, "email": recipient_email, "error": message}
            )

        # Add a delay between sends to respect Gmail rate limits
        if i < len(emails_data) - 1:
            print("Waiting 2 seconds before sending next email...")
            time.sleep(2)

    # Summary
    print("\n" + "=" * 40)
    print("--- Email Sending Summary ---")
    print(f"Total emails: {len(emails_data)}")
    print(f"Successfully sent: {success_count}")
    print(f"Failed: {failure_count}")

    if failed_recipients:
        print("\nFailed recipients:")
        for item in failed_recipients:
            print(f"- {item['name']} <{item['email']}>: {item['error']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")
