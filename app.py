from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import boto3
from botocore.exceptions import ClientError
import os
import uuid
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ─── Database Config ───────────────────────────────────────────
app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('DB_NAME')}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ─── S3 Config (uses EC2 IAM Role — no keys needed) ────────────
s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
S3_BUCKET = os.getenv('S3_BUCKET_NAME')

# ─── Notification Config ────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GMAIL_USER       = os.getenv('GMAIL_USER')
GMAIL_PASSWORD   = os.getenv('GMAIL_PASSWORD')
NOTIFY_EMAIL     = os.getenv('NOTIFY_EMAIL')


# ─── Database Models ────────────────────────────────────────────
class Applicant(db.Model):
    __tablename__ = 'applicants'

    id           = db.Column(db.Integer, primary_key=True)
    applicant_id = db.Column(db.String(50), unique=True, nullable=False)
    first_name   = db.Column(db.String(100), nullable=False)
    last_name    = db.Column(db.String(100), nullable=False)
    email        = db.Column(db.String(150), unique=True, nullable=False)
    phone        = db.Column(db.String(30))
    dob          = db.Column(db.String(20))
    nationality  = db.Column(db.String(100))
    gender       = db.Column(db.String(20))
    job_title    = db.Column(db.String(150))
    linkedin     = db.Column(db.String(255))
    cover_letter = db.Column(db.Text)
    status       = db.Column(db.String(30), default='pending')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    documents    = db.relationship('ApplicantDocument', backref='applicant', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id':           self.id,
            'applicant_id': self.applicant_id,
            'first_name':   self.first_name,
            'last_name':    self.last_name,
            'email':        self.email,
            'phone':        self.phone,
            'dob':          self.dob,
            'nationality':  self.nationality,
            'gender':       self.gender,
            'job_title':    self.job_title,
            'linkedin':     self.linkedin,
            'cover_letter': self.cover_letter,
            'status':       self.status,
            'documents':    [d.to_dict() for d in self.documents],
            'created_at':   self.created_at.isoformat(),
        }


class ApplicantDocument(db.Model):
    __tablename__ = 'applicant_documents'

    id           = db.Column(db.Integer, primary_key=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('applicants.id'), nullable=False)
    doc_type     = db.Column(db.String(50))
    s3_key       = db.Column(db.String(500), nullable=False)
    file_name    = db.Column(db.String(255))
    file_type    = db.Column(db.String(100))
    uploaded_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        url = f"https://{S3_BUCKET}.s3.amazonaws.com/{self.s3_key}"
        return {
            'id':        self.id,
            'doc_type':  self.doc_type,
            's3_key':    self.s3_key,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'url':       url,
        }


# ─── Notification Functions ─────────────────────────────────────

def send_telegram(applicant):
    """Send Telegram message when someone applies"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram not configured — skipping")
        return

    msg = (
        f"🔔 *New Job Application — JobVault*\n\n"
        f"👤 *Name:* {applicant.first_name} {applicant.last_name}\n"
        f"📧 *Email:* {applicant.email}\n"
        f"📱 *Phone:* {applicant.phone or '—'}\n"
        f"💼 *Position:* {applicant.job_title}\n"
        f"🌍 *Nationality:* {applicant.nationality or '—'}\n"
        f"🆔 *Reference ID:* `{applicant.applicant_id}`\n"
        f"📅 *Applied:* {applicant.created_at.strftime('%d %b %Y %H:%M')} UTC\n"
        f"📎 *Documents:* {len(applicant.documents)} file(s) uploaded to S3"
    )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       msg,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        if response.status_code == 200:
            print(f"✅ Telegram notification sent for {applicant.applicant_id}")
        else:
            print(f"⚠️  Telegram error: {response.text}")
    except Exception as e:
        print(f"⚠️  Telegram failed: {e}")


def send_email(applicant):
    """Send email notification when someone applies"""
    if not GMAIL_USER or not GMAIL_PASSWORD or not NOTIFY_EMAIL:
        print("⚠️  Email not configured — skipping")
        return

    subject = f"New Application: {applicant.first_name} {applicant.last_name} — {applicant.job_title}"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
      <div style="background:#0f0e0c;padding:24px;border-radius:12px 12px 0 0;">
        <h1 style="color:#d4a843;margin:0;font-size:1.4rem;">📋 New Job Application</h1>
        <p style="color:rgba(255,255,255,0.5);margin:4px 0 0;font-size:0.85rem;">JobVault Career Portal</p>
      </div>
      <div style="background:#f9f9f9;padding:24px;border:1px solid #eee;border-radius:0 0 12px 12px;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;width:140px;">REFERENCE ID</td><td style="padding:10px 0;border-bottom:1px solid #eee;font-weight:700;color:#b8860b;">{applicant.applicant_id}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">FULL NAME</td><td style="padding:10px 0;border-bottom:1px solid #eee;font-weight:600;">{applicant.first_name} {applicant.last_name}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">EMAIL</td><td style="padding:10px 0;border-bottom:1px solid #eee;">{applicant.email}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">PHONE</td><td style="padding:10px 0;border-bottom:1px solid #eee;">{applicant.phone or '—'}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">POSITION</td><td style="padding:10px 0;border-bottom:1px solid #eee;font-weight:600;">{applicant.job_title}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">NATIONALITY</td><td style="padding:10px 0;border-bottom:1px solid #eee;">{applicant.nationality or '—'}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">LINKEDIN</td><td style="padding:10px 0;border-bottom:1px solid #eee;">{applicant.linkedin or '—'}</td></tr>
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;color:#888;font-size:0.8rem;">DOCUMENTS</td><td style="padding:10px 0;border-bottom:1px solid #eee;">{len(applicant.documents)} file(s) uploaded to S3</td></tr>
          <tr><td style="padding:10px 0;color:#888;font-size:0.8rem;">APPLIED AT</td><td style="padding:10px 0;">{applicant.created_at.strftime('%d %B %Y at %H:%M')} UTC</td></tr>
        </table>
        {"<div style='margin-top:20px;padding:16px;background:#fff;border-radius:8px;border:1px solid #eee;'><p style='color:#888;font-size:0.75rem;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px;'>Cover Letter</p><p style='font-size:0.88rem;line-height:1.6;color:#444;margin:0;'>" + applicant.cover_letter[:500] + ('...' if len(applicant.cover_letter or '') > 500 else '') + "</p></div>" if applicant.cover_letter else ""}
        <div style="margin-top:24px;padding:16px;background:#0f0e0c;border-radius:8px;text-align:center;">
          <p style="color:rgba(255,255,255,0.5);font-size:0.78rem;margin:0;">JobVault Career Portal · Powered by Flask + MySQL + AWS S3</p>
        </div>
      </div>
    </body></html>
    """

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = GMAIL_USER
        msg['To']      = NOTIFY_EMAIL
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())

        print(f"✅ Email notification sent for {applicant.applicant_id}")
    except Exception as e:
        print(f"⚠️  Email failed: {e}")


# ─── Routes ────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.utcnow().isoformat()})


@app.route('/api/get-upload-url', methods=['POST'])
def get_upload_url():
    data         = request.get_json()
    file_name    = data.get('fileName', 'file')
    file_type    = data.get('fileType', 'application/octet-stream')
    applicant_id = data.get('applicantId', 'unknown')
    doc_type     = data.get('docType', 'document')

    s3_key = f"applicants/{applicant_id}/{doc_type}/{uuid.uuid4()}_{file_name}"

    try:
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key, 'ContentType': file_type},
            ExpiresIn=300
        )
        return jsonify({'uploadUrl': presigned_url, 'key': s3_key})
    except ClientError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/applicants', methods=['POST'])
def create_applicant():
    data = request.get_json()

    if Applicant.query.filter_by(email=data.get('email')).first():
        return jsonify({'error': 'Email already registered'}), 409

    applicant = Applicant(
        applicant_id = f"JV-{uuid.uuid4().hex[:8].upper()}",
        first_name   = data.get('firstName'),
        last_name    = data.get('lastName'),
        email        = data.get('email'),
        phone        = data.get('phone'),
        dob          = data.get('dob'),
        nationality  = data.get('nationality'),
        gender       = data.get('gender'),
        job_title    = data.get('jobTitle'),
        linkedin     = data.get('linkedin'),
        cover_letter = data.get('coverLetter'),
    )
    db.session.add(applicant)
    db.session.flush()

    for doc in data.get('documents', []):
        db.session.add(ApplicantDocument(
            applicant_id = applicant.id,
            doc_type     = doc.get('docType'),
            s3_key       = doc.get('key'),
            file_name    = doc.get('fileName'),
            file_type    = doc.get('fileType'),
        ))

    db.session.commit()

    # ─── Send Notifications ───────────────────────────────────
    send_telegram(applicant)
    send_email(applicant)

    return jsonify({
        'message':     'Application submitted successfully',
        'applicantId': applicant.applicant_id,
        'id':          applicant.id
    }), 201


@app.route('/api/applicants', methods=['GET'])
def get_applicants():
    applicants = Applicant.query.order_by(Applicant.created_at.desc()).all()
    return jsonify([a.to_dict() for a in applicants])


@app.route('/api/applicants/<applicant_id>', methods=['GET'])
def get_applicant(applicant_id):
    applicant = Applicant.query.filter_by(applicant_id=applicant_id).first_or_404()
    return jsonify(applicant.to_dict())


@app.route('/api/applicants/<applicant_id>', methods=['DELETE'])
def delete_applicant(applicant_id):
    applicant = Applicant.query.filter_by(applicant_id=applicant_id).first_or_404()
    for doc in applicant.documents:
        try:
            s3_client.delete_object(Bucket=S3_BUCKET, Key=doc.s3_key)
        except ClientError:
            pass
    db.session.delete(applicant)
    db.session.commit()
    return jsonify({'message': f'Applicant {applicant_id} deleted'})


# ─── Init DB & Run ─────────────────────────────────────────────
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("✅ Database tables created")
    app.run(host='0.0.0.0', port=5000, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
