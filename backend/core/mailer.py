import smtplib
from email.message import EmailMessage
import os

def send_certificate(sender_email, sender_password, recipient_email, recipient_name, subject, body_text, attachment_path):
    """
    Connects to Gmail SMTP, constructs the email with a PDF attachment, and sends it.
    """
    # 1. Construct the email
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = recipient_email
    
    # Personalize the body text
    personalized_body = body_text.replace("{name}", recipient_name)
    msg.set_content(personalized_body)

    # 2. Attach the PDF
    if not os.path.exists(attachment_path):
        return False, f"Attachment not found: {attachment_path}"

    try:
        with open(attachment_path, 'rb') as f:
            pdf_data = f.read()
            pdf_name = os.path.basename(attachment_path)
            
        msg.add_attachment(
            pdf_data, 
            maintype='application', 
            subtype='pdf', 
            filename=pdf_name
        )
    except Exception as e:
         return False, f"Failed to read attachment: {str(e)}"

    # 3. Send the email via Google's SMTP server
    try:
        # Port 465 is for SSL
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            
        return True, "Success"
        
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed. Check the App Password."
    except Exception as e:
        return False, f"Failed to send email: {str(e)}"

# Quick standalone test block (runs only if you execute this file directly)
if __name__ == "__main__":
    print("Core mailer module loaded. Ready to send.")