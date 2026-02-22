from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import boto3
from botocore.exceptions import ClientError
import os
import uuid
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
s3_client = boto3.client(
    's3',
    region_name=os.getenv('AWS_REGION', 'us-east-1'),
)
S3_BUCKET = os.getenv('S3_BUCKET_NAME')


# ─── Database Models ────────────────────────────────────────────
class Applicant(db.Model):
    __tablename__ = 'applicants'

    id             = db.Column(db.Integer, primary_key=True)
    applicant_id   = db.Column(db.String(50), unique=True, nullable=False)
    first_name     = db.Column(db.String(100), nullable=False)
    last_name      = db.Column(db.String(100), nullable=False)
    email          = db.Column(db.String(150), unique=True, nullable=False)
    phone          = db.Column(db.String(30))
    dob            = db.Column(db.String(20))
    nationality    = db.Column(db.String(100))
    gender         = db.Column(db.String(20))
    job_title      = db.Column(db.String(150))
    linkedin       = db.Column(db.String(255))
    cover_letter   = db.Column(db.Text)
    status         = db.Column(db.String(30), default='pending')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    documents      = db.relationship('ApplicantDocument', backref='applicant', lazy=True, cascade='all, delete-orphan')

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
    doc_type     = db.Column(db.String(50))   # 'resume', 'cover_letter', 'id_doc'
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


# ─── Routes ────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.utcnow().isoformat()})


# Generate presigned S3 URL for direct browser upload
@app.route('/api/get-upload-url', methods=['POST'])
def get_upload_url():
    data      = request.get_json()
    file_name = data.get('fileName', 'file')
    file_type = data.get('fileType', 'application/octet-stream')
    applicant_id = data.get('applicantId', 'unknown')
    doc_type  = data.get('docType', 'document')

    s3_key = f"applicants/{applicant_id}/{doc_type}/{uuid.uuid4()}_{file_name}"

    try:
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket':      S3_BUCKET,
                'Key':         s3_key,
                'ContentType': file_type,
            },
            ExpiresIn=300
        )
        return jsonify({'uploadUrl': presigned_url, 'key': s3_key})
    except ClientError as e:
        return jsonify({'error': str(e)}), 500


# Submit job application
@app.route('/api/applicants', methods=['POST'])
def create_applicant():
    data = request.get_json()

    if Applicant.query.filter_by(email=data.get('email')).first():
        return jsonify({'error': 'Email already registered'}), 409

    applicant_id = f"JV-{uuid.uuid4().hex[:8].upper()}"

    applicant = Applicant(
        applicant_id = applicant_id,
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
        document = ApplicantDocument(
            applicant_id = applicant.id,
            doc_type     = doc.get('docType'),
            s3_key       = doc.get('key'),
            file_name    = doc.get('fileName'),
            file_type    = doc.get('fileType'),
        )
        db.session.add(document)

    db.session.commit()
    return jsonify({
        'message':     'Application submitted successfully',
        'applicantId': applicant.applicant_id,
        'id':          applicant.id
    }), 201


# Get all applicants
@app.route('/api/applicants', methods=['GET'])
def get_applicants():
    applicants = Applicant.query.order_by(Applicant.created_at.desc()).all()
    return jsonify([a.to_dict() for a in applicants])


# Get single applicant
@app.route('/api/applicants/<applicant_id>', methods=['GET'])
def get_applicant(applicant_id):
    applicant = Applicant.query.filter_by(applicant_id=applicant_id).first_or_404()
    return jsonify(applicant.to_dict())


# Delete applicant and their S3 files
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
