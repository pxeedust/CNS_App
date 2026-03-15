import pandas as pd
import google.generativeai as genai
import os
from dotenv import load_dotenv
import time
from string import Template
import json

# Load environment variables from .env file
load_dotenv()

# Configure Google Generative AI with your API key - Fix this line
# Option 1: Use an environment variable (recommended for security)
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


# Function to read spreadsheet data with better error handling
def load_contacts(file_path):
    """Load contacts from a spreadsheet file with enhanced error checking."""
    print(f"Attempting to read file: {file_path}")

    # Check if file exists
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"The file {file_path} does not exist")

    try:
        if file_path.endswith(".csv"):
            df = pd.read_csv(file_path)
            print(f"CSV loaded successfully with {len(df)} rows")
        elif file_path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_path)
            print(f"Excel file loaded successfully with {len(df)} rows")
        else:
            raise ValueError("File format not supported. Please use CSV or Excel file.")

        # Verify if the dataframe contains expected columns
        required_columns = ["First Name", "Last Name", "Email", "Email Status"]
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            print(
                f"Warning: The following required columns are missing: {missing_columns}"
            )

        # Print the first few rows to verify data
        print("\nPreview of loaded data:")
        print(df.head(3))
        print(f"\nColumns found: {list(df.columns)}")

        return df

    except Exception as e:
        print(f"Error reading file: {e}")
        raise


# Function to generate personalized email using Gemini
def generate_email(contact, template_info, model="models/gemini-2.5-flash"):
    """Generate a personalized email using Gemini model."""

    # Extract relevant fields for email personalization
    first_name = contact.get("First Name", "")
    last_name = contact.get("Last Name", "")
    title = contact.get("Title", "")
    company = contact.get("Company Name", "")
    industry = contact.get("Industry", "")
    keywords = contact.get("Keywords", "")
    website = contact.get("Website", "")

    # Brief company research prompt - just for personalization
    company_research_prompt = f"""
    Based on the following information about {company}:
    - Industry: {industry}
    - Keywords: {keywords}
    - Website: {website}

    Write ONE very brief phrase (under 10 words) describing their core business or product.
    Be specific and professional.
    """

    try:
        # Generate company research
        research_model = genai.GenerativeModel(model)
        research_response = research_model.generate_content(company_research_prompt)
        company_highlight = research_response.text.strip()
    except Exception as e:
        print(f"Error generating company research: {e}")
        company_highlight = f"{company}'s work in the {industry} sector"

    # Create prompt for Gemini - Updated for punchy, short emails
    prompt = f"""
    Create a short, punchy outreach email body (NOT a complete email) to {first_name} {last_name} who works as {title} at {company}.
    
    Generate both a SUBJECT LINE and EMAIL BODY:
    1. Create a subject line in this EXACT format: "180DC IIT Kharagpur X {company}" (you may add a brief descriptor after the company name)
    2. Then, create the email body (separate from subject)
    
    Requirements for the body:
    1. EXTREMELY SHORT AND PUNCHY - max 75 words.
    2. NO long paragraphs. Use short, crisp sentences.
    3. Break the text into 2-3 very short paragraphs (1-2 sentences each).
    4. DO NOT include any introduction like "My name is" or "I am" - this will be handled by the template.
    5. DO NOT include any signature - this will be handled by the template.
    6. DO NOT include any greeting like "Dear [Name]," - this is handled by the template.
    7. Start directly with the value proposition or company acknowledgment.
    
    Structure:
    - Para 1: One sentence acknowledging {company}'s work in {company_highlight}.
    - Para 2: One sentence stating we've helped similar {industry} clients solve growth/ops challenges.
    - Para 3: Direct question asking for a 15-min call to discuss potential synergies.
    
    The tone should be: {template_info['tone']}
    
    Format your response exactly like this:
    SUBJECT: 180DC IIT Kharagpur X {company} [optional brief descriptor]

    [Email body starts here - punchy, short, no fluff]
    """

    # Generate email using Gemini
    model = genai.GenerativeModel(model)
    response = model.generate_content(prompt)

    # Parse response to separate subject and body
    content = response.text.strip()
    parts = content.split("\n\n", 1)

    # Extract subject and body
    if len(parts) == 2 and parts[0].startswith("SUBJECT:"):
        subject = parts[0].replace("SUBJECT:", "").strip()
        body = parts[1].strip()
    else:
        # Fallback if format wasn't followed - ensure correct subject format
        subject = f"180DC IIT Kharagpur X {company}"
        body = content

    # Make sure subject has the right format
    if not subject.startswith("180DC IIT Kharagpur X"):
        subject = f"180DC IIT Kharagpur X {company}"

    # Remove any accidental greetings that might have been added
    common_greetings = [
        f"Dear {first_name}",
        f"Dear Mr. {last_name}",
        f"Dear Ms. {last_name}",
        f"Dear {last_name}",
        "Dear Sir",
        "Dear Madam",
        "Hello",
        "Hi",
        "Greetings",
    ]

    # Check for these greetings at the beginning of the body
    for greeting in common_greetings:
        if body.startswith(greeting):
            # Remove the greeting and any following comma/period and whitespace
            body = body.replace(greeting, "", 1).lstrip(",. \n")
            break

    # Apply template formatting to body
    template = Template(template_info["template"])
    try:
        formatted_body = template.substitute(
            FIRST_NAME=first_name,
            LAST_NAME=last_name,
            TITLE=title,
            COMPANY=company,
            EMAIL_BODY=body,
            **contact,
        )
    except KeyError as e:
        print(
            f"Warning: Missing template variable {e} for contact {first_name} {last_name}"
        )
        formatted_body = body

    return subject, formatted_body


# Function to process all contacts and generate emails
def process_contacts(contacts_df, template_info):
    """Process all contacts and generate emails."""
    results = []

    # First, check for the Email column
    if "Email" not in contacts_df.columns:
        print("ERROR: 'Email' column not found in the spreadsheet!")
        print(f"Available columns: {list(contacts_df.columns)}")
        print("Please check your column names and try again.")
        return results

    # Check if Email Status column exists
    has_email_status = "Email Status" in contacts_df.columns

    # Print unique email status values to understand what we're working with
    if has_email_status:
        unique_statuses = contacts_df["Email Status"].unique()
        print(f"Found {len(unique_statuses)} unique email statuses: {unique_statuses}")
    else:
        print("Note: 'Email Status' column not found in the spreadsheet")

    # Count before filtering
    total_contacts = len(contacts_df)
    print(f"Total contacts before filtering: {total_contacts}")

    # Check for empty emails and print summary
    empty_emails = contacts_df["Email"].isna().sum()
    if empty_emails > 0:
        print(f"WARNING: Found {empty_emails} rows with missing email addresses")

    skipped_count = 0
    for idx, row in contacts_df.iterrows():
        contact = row.to_dict()
        email = contact.get("Email")

        # Skip contacts with missing emails - improved error message
        if pd.isna(email) or str(email).strip() == "":
            print(
                f"Skipping contact {idx}: {contact.get('First Name', '')} {contact.get('Last Name', '')} - No email address"
            )
            # Print the row data for debugging
            print(f"Row data: {row.to_dict()}")
            skipped_count += 1
            continue

        # If Email Status exists, check it - MODIFIED: more lenient status checking
        if has_email_status:
            status = str(contact.get("Email Status", "")).strip()

            # Skip only if status explicitly indicates a problem
            # Modify these to match the negative statuses in your data
            negative_statuses = ["invalid", "bounced", "undeliverable", "bad"]
            if any(neg in status.lower() for neg in negative_statuses):
                print(
                    f"Skipping contact {idx}: {contact.get('First Name', '')} {contact.get('Last Name', '')} - Status: '{status}'"
                )
                skipped_count += 1
                continue

        try:
            print(
                f"Generating email for {contact.get('First Name', '')} {contact.get('Last Name', '')} ({email})"
            )

            # Generate email - now returns subject and body separately
            subject, body = generate_email(
                contact, template_info
            )  # CHANGE HERE - unpack the tuple

            # Store result
            results.append(
                {
                    "contact": f"{contact.get('First Name', '')} {contact.get('Last Name', '')}",
                    "email_address": email,
                    "subject": subject,  # CHANGE HERE - store subject
                    "generated_email": body,  # CHANGE HERE - store body
                }
            )

            # Add delay to avoid rate limiting
            time.sleep(1)

        except Exception as e:
            print(
                f"Error generating email for {contact.get('First Name', '')} {contact.get('Last Name', '')}: {e}"
            )
            skipped_count += 1

    print(
        f"\nProcessing summary:\n- Total contacts: {total_contacts}\n- Processed: {len(results)}\n- Skipped: {skipped_count}"
    )

    return results


# Function to save generated emails
def save_emails(results, output_path="generated_emails.json"):
    """Save generated emails to file."""
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    # Also create a text file with all emails for easy review
    with open("generated_emails.txt", "w") as f:
        for result in results:
            f.write(f"TO: {result['contact']} <{result['email_address']}>\n")
            f.write(
                f"SUBJECT: {result['subject']}\n\n"
            )  # CHANGE HERE - add subject line
            f.write(result["generated_email"])
            f.write("\n\n" + "=" * 80 + "\n\n")


# Main function
def main():
    # Define the template info based on the 180DC format
    default_template_info = {
        "description": """
        This email is for outreach to potential partners for 180 Degrees Consulting, IIT Kharagpur.
        It should introduce myself as Parth Sethi, Executive Head at 180 Degrees Consulting,
        acknowledge something specific about the recipient's company, explain 180DC's services,
        suggest potential collaboration areas, and request a brief call.
        """,
        "tone": "Professional, friendly, and concise",
        "key_points": """
        - Introduction: I am Parth Sethi, Executive Head at 180 Degrees Consulting, IIT Kharagpur
        - Acknowledge the recipient's company with specific details that show research
        - Explain that 180DC IIT Kharagpur is a student-run consultancy providing data-driven strategic and operational services
        - Mention that our expertise can support their company in relevant areas (be specific to their industry)
        - Explain our consultants offer fresh, analytical perspectives to address business challenges
        - Request for a brief call to discuss strategic priorities and potential collaboration
        """,
        "template": """Respected $TITLE $LAST_NAME,

I am Parth Sethi, Executive Head at 180 Degrees Consulting, IIT Kharagpur. $EMAIL_BODY

Best regards,
Parth Sethi
Executive Head
180 Degrees Consulting, IIT Kharagpur
https://www.180dc.org/branches/IITKGP
""",
    }

    # Get user input for file paths
    file_path = input("Enter the path to your contacts spreadsheet (CSV or Excel): ")

    # Ask if user wants to customize the template
    customize = (
        input("Do you want to customize the email template? (yes/no): ").lower()
        == "yes"
    )

    if customize:
        template_info = {}
        print("\n=== Email Template Customization ===")
        template_info["description"] = input(
            "Enter the purpose and context of the email: "
        )
        template_info["tone"] = input("Enter the desired tone of the email: ")
        template_info["key_points"] = input(
            "Enter key points to include (use line breaks): "
        )
        print(
            "\nEnter the email template with placeholders like $FIRST_NAME, $COMPANY, $EMAIL_BODY, etc.:"
        )
        template_info["template"] = input("Template: ")
    else:
        template_info = default_template_info

    # Load contacts
    try:
        contacts_df = load_contacts(file_path)
        print(f"Loaded {len(contacts_df)} contacts from the spreadsheet")
    except Exception as e:
        print(f"Error loading contacts: {e}")
        return

    # Check if required columns exist
    required_columns = ["First Name", "Last Name", "Email"]
    missing_columns = [
        col for col in required_columns if col not in contacts_df.columns
    ]
    if missing_columns:
        print(f"ERROR: Missing required columns: {missing_columns}")
        print(f"Available columns: {list(contacts_df.columns)}")
        print("Please check your spreadsheet and try again.")
        return

    # Preview email column
    print("Email column preview:")
    print(contacts_df["Email"].head(10))

    # Process contacts
    print("Generating personalized emails...")
    results = process_contacts(contacts_df, template_info)
    print(f"Generated {len(results)} emails")

    # Save results
    output_path = (
        input("Enter path to save generated emails (default: generated_emails.json): ")
        or "generated_emails.json"
    )
    save_emails(results, output_path)
    print(f"Emails saved to {output_path} and generated_emails.txt")

    # Ask if user wants to see a sample
    if (
        results
        and input("Would you like to see a sample email? (yes/no): ").lower() == "yes"
    ):
        sample = results[0]
        print("\n" + "=" * 80 + "\n")
        print(f"TO: {sample['contact']} <{sample['email_address']}>\n")
        print(sample["generated_email"])
        print("\n" + "=" * 80)


def test_spreadsheet_loading(file_path):
    """Test function to check if the spreadsheet can be loaded properly."""
    try:
        df = load_contacts(file_path)
        print(f"SUCCESS: Loaded {len(df)} contacts")
        return True
    except Exception as e:
        print(f"FAILED: Could not load spreadsheet: {str(e)}")
        return False


if __name__ == "__main__":
    main()
