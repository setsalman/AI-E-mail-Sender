from flask import Flask, render_template, request, redirect, flash
import smtplib
import ssl
import pandas as pd
import pdfplumber
import re
from email.message import EmailMessage
import os
import socket
import time
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def extract_data_from_pdf(filepath, name_header, email_header, business_header, niche_header):
    data = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    
                    # Find header row
                    header_found = False
                    headers = []
                    data_start = 0
                    
                    for i, line in enumerate(lines):
                        if (email_header.lower() in line.lower() and 
                            name_header.lower() in line.lower()):
                            headers = re.split(r'\s{2,}', line.lower())
                            data_start = i + 1
                            header_found = True
                            break
                    
                    if header_found and data_start < len(lines):
                        for line in lines[data_start:]:
                            if not line.strip():
                                continue
                            values = re.split(r'\s{2,}', line)
                            if len(values) >= len(headers):
                                record = {}
                                for i, header in enumerate(headers):
                                    if i < len(values):
                                        value = values[i].strip()
                                        if name_header.lower() in header:
                                            record['name'] = value
                                        elif email_header.lower() in header:
                                            record['email'] = value
                                        elif business_header.lower() in header:
                                            record['business'] = value
                                        elif niche_header.lower() in header:
                                            record['niche'] = value
                                
                                if 'email' in record and '@' in record['email']:
                                    data.append({
                                        'email': record.get('email', ''),
                                        'name': record.get('name', 'Valued Customer'),
                                        'business': record.get('business', 'Your Business'),
                                        'niche': record.get('niche', 'Your Industry')
                                    })
    except Exception as e:
        flash(f"PDF processing error: {str(e)}", "error")
    return data

def replace_placeholders(text, recipient):
    if not isinstance(text, str):
        return text
    
    # Find all placeholders in the format {column_name}
    placeholders = re.findall(r'\{([^}]+)\}', text)
    
    for placeholder in placeholders:
        # Try to find the placeholder in the recipient data (case-insensitive)
        found = False
        for key, value in recipient.items():
            if str(key).lower() == placeholder.lower():
                text = text.replace(f'{{{placeholder}}}', str(value))
                found = True
                break
        
        # If not found, replace with a generic value
        if not found:
            text = text.replace(f'{{{placeholder}}}', f'[{placeholder}]')
    
    return text

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        sender_email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        subject = request.form.get('subject', '').strip()
        message_body = request.form.get('message', '').strip()
        file = request.files.get('file')
        
        # Get user-specified headers
        name_header = request.form.get('name_header', 'Name').strip()
        email_header = request.form.get('email_header', 'Email').strip()
        business_header = request.form.get('business_header', 'Business').strip()
        niche_header = request.form.get('niche_header', 'Niche').strip()

        if not all([sender_email, password, subject, message_body, file]):
            flash("All fields are required", "error")
            return redirect('/')

        try:
            filename = secure_filename(file.filename)
            if not filename:
                flash("Invalid file name", "error")
                return redirect('/')

            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            email_data = []
            if filename.lower().endswith(('.xlsx', '.xls')):
                try:
                    df = pd.read_excel(file_path)
                    
                    # Convert all column names to strings and lowercase for comparison
                    df.columns = [str(col) for col in df.columns]
                    
                    # Find email column (required)
                    email_col = None
                    for col in df.columns:
                        if email_header.lower() in col.lower():
                            email_col = col
                            break
                    
                    if not email_col:
                        flash(f"No column matching '{email_header}' found in Excel file", "error")
                        return redirect('/')
                    
                    # Process each row
                    for _, row in df.iterrows():
                        email = str(row[email_col]).strip()
                        if not email or '@' not in email:
                            continue
                        
                        # Create recipient dictionary with all columns
                        recipient_data = {'email': email}
                        
                        # Add all columns to recipient data
                        for col in df.columns:
                            if pd.notna(row[col]):
                                recipient_data[col] = str(row[col]).strip()
                            else:
                                recipient_data[col] = ''
                        
                        # Add default values for known headers if not present
                        if name_header.lower() not in [col.lower() for col in df.columns]:
                            recipient_data[name_header] = 'Valued Customer'
                        if business_header.lower() not in [col.lower() for col in df.columns]:
                            recipient_data[business_header] = 'Your Business'
                        if niche_header.lower() not in [col.lower() for col in df.columns]:
                            recipient_data[niche_header] = 'Your Industry'
                        
                        email_data.append(recipient_data)

                except Exception as e:
                    flash(f"Excel processing error: {str(e)}", "error")
                    return redirect('/')

            elif filename.lower().endswith('.pdf'):
                email_data = extract_data_from_pdf(file_path, name_header, email_header, business_header, niche_header)
                if not email_data:
                    flash(f"""
                    Could not extract data from PDF. Ensure your PDF has:
                    1. A header row with '{name_header}', '{email_header}', '{business_header}', and '{niche_header}'
                    2. Data rows with values separated by multiple spaces or tabs
                    """, "error")
                    return redirect('/')
            else:
                flash("Unsupported file format. Only Excel or PDF allowed", "error")
                return redirect('/')

            context = ssl.create_default_context()
            success_count = 0
            
            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10, context=context) as server:
                    server.login(sender_email, password)
                    
                    for recipient in email_data:
                        try:
                            msg = EmailMessage()
                            msg['Subject'] = replace_placeholders(subject, recipient)
                            msg['From'] = sender_email
                            msg['To'] = recipient['email']
                            msg.set_content(replace_placeholders(message_body, recipient))
                            
                            server.send_message(msg)
                            success_count += 1
                            time.sleep(1)  # Rate limiting
                            
                        except Exception as e:
                            flash(f"Failed to send to {recipient.get('email')}: {str(e)}", "error")
                    
                    flash(f"Successfully sent {success_count} emails!", "success")
                    
            except (smtplib.SMTPException, socket.gaierror) as e:
                flash(f"SMTP Error: {str(e)}", "error")

        except Exception as e:
            flash(f"Unexpected error: {str(e)}", "error")

        return redirect('/')

    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)