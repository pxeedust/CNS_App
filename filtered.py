import json

# List of email addresses for the failed recipients
failed_emails = [
    "ketan@hopelectric.in",
    "dhruvil.sheth@tractorseva.com",
    "sanjeev@e3electric.ai",
    "ajit.patil@rivotmotors.com",
    "gopi@badboyev.in",
    "vimal@readyassist.in",
    "rajeev.mehtani@yatis.in",
    "himanshu.arya@myluxurycart.com",
    "shree@halamobility.in",
    "dinesh@raptee.com",
    "nikhil.gonsalves@ingoelectric.com",
    "satish@autorounders.com",
    "sandeep.yadav@yahhvi.com",
    "kaustubh@autonxt.in",
    "silambarasan@towman.in",
    "mayank@efill.co.in",
    "vivek@buymyev.in",
    "divyansh@bmrev.in",
]

# Load the original JSON file
with open("generated_emails.json", "r") as f:
    all_emails = json.load(f)

# Filter to keep only the failed recipients
filtered_emails = [
    email for email in all_emails if email.get("email_address") in failed_emails
]

# Save the filtered data back to the JSON file
with open("generated_emails.json", "w") as f:
    json.dump(filtered_emails, f, indent=2)

print(f"Filtered JSON file saved with {len(filtered_emails)} entries")
