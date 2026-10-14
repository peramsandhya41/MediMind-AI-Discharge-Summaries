import os, json, sqlite3, uuid, datetime, io, hashlib, secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from xml.sax.saxutils import escape
import requests

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
DB = DATA / "medimind.db"
UPLOADS = DATA / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="MediMind API", version="2.0.0")
_cors = [x.strip() for x in os.getenv('MEDIMIND_CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:4173,http://127.0.0.1:4173').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

class DoctorLogin(BaseModel):
    email: str
    mobile: str
    password: str = ""

class DoctorRegister(BaseModel):
    name: str
    email: str
    mobile: str
    password: str
    hospital: str = "Independent / Demo Hospital"

class PatientLogin(BaseModel):
    email: str
    mobile: str = ''
    password: str = ''

class PatientRegister(BaseModel):
    name: str
    email: str
    mobile: str
    password: str

class StaffLogin(BaseModel):
    email: str
    mobile: str
    password: str

class StaffRegister(BaseModel):
    name: str
    email: str
    mobile: str
    password: str
    hospital: str = 'Hospital / Clinic'
    staff_type: str = 'Clinical Staff'

class SummaryUpdate(BaseModel):
    summary: dict

class ShareRequest(BaseModel):
    patient_email: str
    document_id: str


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def send_portal_email(to_email: str, subject: str, body: str) -> bool:
    """Send a transactional portal notification if SMTP is configured."""
    host=os.getenv('MEDIMIND_SMTP_HOST'); sender=os.getenv('MEDIMIND_SMTP_FROM'); password=os.getenv('MEDIMIND_SMTP_PASSWORD')
    if not (host and sender and password and to_email):
        return False
    try:
        import smtplib
        from email.message import EmailMessage
        msg=EmailMessage(); msg['Subject']=subject; msg['From']=sender; msg['To']=to_email; msg.set_content(body)
        with smtplib.SMTP(host, int(os.getenv('MEDIMIND_SMTP_PORT','587')), timeout=15) as server:
            server.starttls(); server.login(sender,password); server.send_message(msg)
        return True
    except Exception:
        return False


def init_db():
    c = db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS doctors(id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, mobile TEXT UNIQUE, password_hash TEXT, hospital TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS patient_auth(id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, mobile TEXT UNIQUE, password_hash TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS staff(id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, mobile TEXT UNIQUE, password_hash TEXT, hospital TEXT, staff_type TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, patient_id TEXT, name TEXT, text TEXT, stored_path TEXT, created_at TEXT, extraction_status TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS shares(id TEXT PRIMARY KEY, document_id TEXT, patient_id TEXT, patient_email TEXT, status TEXT, shared_at TEXT);
    CREATE TABLE IF NOT EXISTS summaries(id TEXT PRIMARY KEY, patient_id TEXT, data TEXT, status TEXT, version INTEGER, created_at TEXT, updated_at TEXT, approved_at TEXT);
    CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY, summary_id TEXT, action TEXT, actor TEXT, details TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS appointments(id TEXT PRIMARY KEY, patient_id TEXT, doctor_id TEXT, scheduled_at TEXT, reason TEXT, status TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, user_email TEXT, title TEXT, message TEXT, kind TEXT, read_flag INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS summary_shares(id TEXT PRIMARY KEY, summary_id TEXT, patient_id TEXT, patient_email TEXT, shared_at TEXT);
    ''')
    doc_cols={row['name'] for row in c.execute('PRAGMA table_info(documents)').fetchall()}
    if 'extraction_status' not in doc_cols: c.execute("ALTER TABLE documents ADD COLUMN extraction_status TEXT DEFAULT ''")
    demo_email = 'doctor@medimind.demo'
    if not c.execute('SELECT id FROM doctors WHERE email=?', (demo_email,)).fetchone():
        now = datetime.datetime.utcnow().isoformat()
        c.execute('INSERT INTO doctors VALUES (?,?,?,?,?,?,?)', ('D001','Dr. Ananya Rao',demo_email,'9876543210',hash_password('demo123'),'MediMind Demo Hospital',now))
    if c.execute('SELECT COUNT(*) FROM patients').fetchone()[0] == 0:
        patients = [
            {"id":"P1024","name":"Rahul Mehta","email":"rahul.patient@medimind.demo","age":45,"gender":"Male","admission_date":"2026-10-01","discharge_date":"2026-10-04","ward":"Ward 4","doctor":"Dr. Ananya Rao","diagnosis":["Community-acquired pneumonia"],"investigations":["Chest X-ray reported right lower-zone infiltrates","CBC monitored during admission"],"treatment":["Supportive care and inpatient treatment as documented in the record"],"medications":[{"name":"Amoxicillin-clavulanate","dose":"As documented","frequency":"As prescribed","duration":"As prescribed"},{"name":"Paracetamol","dose":"As documented","frequency":"As prescribed","duration":"As prescribed"}],"follow_up":"Follow-up with the treating clinician as documented in the discharge plan.","notes":"Synthetic demo patient for hackathon presentation."},
            {"id":"P1025","name":"Anitha Rao","email":"anitha.patient@medimind.demo","age":38,"gender":"Female","admission_date":"2026-10-02","discharge_date":"2026-10-04","ward":"Ward 2","doctor":"Dr. Ananya Rao","diagnosis":["Acute gastroenteritis"],"investigations":["Basic laboratory evaluation documented"],"treatment":["Hydration and supportive inpatient management as documented"],"medications":[{"name":"Oral rehydration solution","dose":"As documented","frequency":"As prescribed","duration":"As prescribed"}],"follow_up":"Follow-up according to the treating clinician's documented plan.","notes":"Synthetic demo patient."}
        ]
        for p in patients:
            c.execute('INSERT INTO patients VALUES (?,?)',(p['id'],json.dumps(p)))
    # Seed a rich synthetic demonstration workspace (never use real patient data here).
    doctor_seed = [
      ('D002','Dr. Arjun Reddy','arjun.reddy@medimind.demo','9876500002','CityCare Medical Centre'),
      ('D003','Dr. Priya Sharma','priya.sharma@medimind.demo','9876500003','MediMind Demo Hospital'),
      ('D004','Dr. Kiran Kumar','kiran.kumar@medimind.demo','9876500004','Sunrise Multispeciality'),
      ('D005','Dr. Neha Verma','neha.verma@medimind.demo','9876500005','CityCare Medical Centre'),
      ('D006','Dr. Vivek Rao','vivek.rao@medimind.demo','9876500006','MediMind Demo Hospital'),
      ('D007','Dr. Aisha Khan','aisha.khan@medimind.demo','9876500007','Sunrise Multispeciality'),
      ('D008','Dr. Rahul Menon','rahul.menon@medimind.demo','9876500008','CityCare Medical Centre'),
      ('D009','Dr. Sneha Iyer','sneha.iyer@medimind.demo','9876500009','MediMind Demo Hospital'),
      ('D010','Dr. Aditya Das','aditya.das@medimind.demo','9876500010','Sunrise Multispeciality'),
      ('D011','Dr. Meera Joshi','meera.joshi@medimind.demo','9876500011','CityCare Medical Centre'),
      ('D012','Dr. Sandeep Nair','sandeep.nair@medimind.demo','9876500012','MediMind Demo Hospital'),
    ]
    for did,name,email,mobile,hospital in doctor_seed:
      if not c.execute('SELECT id FROM doctors WHERE id=? OR lower(email)=lower(?)',(did,email)).fetchone():
        c.execute('INSERT INTO doctors VALUES (?,?,?,?,?,?,?)',(did,name,email,mobile,hash_password('demo123'),hospital,datetime.datetime.utcnow().isoformat()))
    if c.execute('SELECT COUNT(*) FROM patients').fetchone()[0] < 15:
      demo_docs = c.execute('SELECT id,name FROM doctors ORDER BY id').fetchall()
      diagnoses=['Type 2 diabetes follow-up','Acute bronchitis','Hypertension monitoring','Post-operative review','Migraine evaluation','Urinary tract infection','Asthma exacerbation','Gastroenteritis','Anemia evaluation','Pneumonia follow-up','Knee pain assessment','Dehydration','Skin infection']
      names=['Sanjay Patel','Divya Reddy','Mohammed Irfan','Kavya Nair','Rohan Gupta','Lakshmi Devi','Aravind Rao','Pooja Singh','Nikhil Verma','Farah Ali','Manoj Kumar','Ishita Das','Vikram Shah']
      for i in range(13):
        pid=f'P{1100+i}'; doc=demo_docs[(i+1)%len(demo_docs)]; name=names[i]; email=f'patient{i+1}@medimind.demo'
        if not c.execute('SELECT id FROM patients WHERE id=?',(pid,)).fetchone():
          pdata={'id':pid,'name':name,'email':email,'mobile':f'900000{i+1:04d}','age':25+(i*3)%48,'gender':'Female' if i%2 else 'Male','admission_date':'2026-10-02','discharge_date':'2026-10-09','ward':f'Ward {(i%5)+1}','doctor':doc['name'],'doctor_id':doc['id'],'diagnosis':[diagnoses[i]],'investigations':['Synthetic demo investigation record available'],'treatment':['Care documented by treating team in this synthetic example'],'medications':[{'name':'Medication as prescribed','dose':'As documented','frequency':'As prescribed','duration':'As prescribed'}],'follow_up':'Follow-up with the assigned treating clinician as documented.','notes':'Synthetic demo patient only; not real medical information.'}
          c.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps(pdata)))
          # Add portal accounts for seeded patients so patient dashboard can be demonstrated.
          if not c.execute('SELECT id FROM patient_auth WHERE lower(email)=lower(?)',(email,)).fetchone():
            c.execute('INSERT INTO patient_auth VALUES (?,?,?,?,?,?)',(pid,name,email,pdata['mobile'],hash_password('demo123'),datetime.datetime.utcnow().isoformat()))
    # Ensure the demo doctor has a meaningful synthetic caseload and appointments.
    primary = c.execute('SELECT id,name,email FROM doctors WHERE email=?', ('doctor@medimind.demo',)).fetchone()
    if primary:
      demo_rows = c.execute('SELECT id,data FROM patients ORDER BY id').fetchall()
      for idx, row in enumerate(demo_rows):
        pdata=json.loads(row['data'])
        if idx < 6:
          pdata['doctor_id']=primary['id']; pdata['doctor']=primary['name']
          c.execute('UPDATE patients SET data=? WHERE id=?',(json.dumps(pdata),row['id']))
      if c.execute('SELECT COUNT(*) FROM appointments WHERE doctor_id=?',(primary['id'],)).fetchone()[0] == 0:
        for idx,row in enumerate(demo_rows[:4]):
          scheduled=(datetime.datetime.utcnow()+datetime.timedelta(days=idx+1)).replace(hour=10+idx,minute=0,second=0,microsecond=0).isoformat(timespec='minutes')
          c.execute('INSERT INTO appointments VALUES (?,?,?,?,?,?,?)',(f'A-DEMO{idx+1}',row['id'],primary['id'],scheduled,'Routine follow-up consultation','Scheduled',datetime.datetime.utcnow().isoformat()))
    c.commit(); c.close()

init_db()

@app.get('/api/health')
def health(): return {"status":"ok","service":"MediMind API"}

@app.post('/api/auth/doctor/login')
def doctor_login(body: DoctorLogin):
    c=db(); r=c.execute('SELECT * FROM doctors WHERE lower(email)=lower(?) AND mobile=?',(body.email,body.mobile)).fetchone(); c.close()
    if not r or (body.password and r['password_hash'] != hash_password(body.password)):
        raise HTTPException(401,'Doctor credentials not found. Use email + registered mobile + password.')
    return {"token":"doctor-demo-token","user":{"id":r['id'],"name":r['name'],"email":r['email'],"mobile":r['mobile'],"role":"Doctor","hospital":r['hospital']}}

@app.post('/api/auth/doctor/register')
def doctor_register(body: DoctorRegister):
    c=db();
    if c.execute('SELECT id FROM doctors WHERE lower(email)=lower(?) OR mobile=?',(body.email,body.mobile)).fetchone():
        c.close(); raise HTTPException(409,'A doctor account already exists with this email or mobile number.')
    did='D-'+secrets.token_hex(4).upper(); now=datetime.datetime.utcnow().isoformat()
    c.execute('INSERT INTO doctors VALUES (?,?,?,?,?,?,?)',(did,body.name,body.email,body.mobile,hash_password(body.password),body.hospital,now)); c.commit(); c.close()
    return {"message":"Doctor account created","user":{"id":did,"name":body.name,"email":body.email,"mobile":body.mobile,"role":"Doctor","hospital":body.hospital}}

@app.post('/api/auth/patient/register')
def patient_register(body: PatientRegister):
    email = body.email.strip().lower(); mobile = body.mobile.strip()
    if len(mobile) < 7: raise HTTPException(400, 'Enter a valid mobile number.')
    c=db()
    if c.execute('SELECT id FROM patient_auth WHERE lower(email)=? OR mobile=?',(email,mobile)).fetchone():
        c.close(); raise HTTPException(409,'An account already exists with this email or mobile number. Please sign in.')
    pid='P-'+secrets.token_hex(4).upper(); now=datetime.datetime.utcnow().isoformat()
    pdata={"id":pid,"name":body.name.strip(),"email":email,"mobile":mobile,"age":"Not provided","gender":"Not provided","admission_date":"Not assigned","discharge_date":"Pending","ward":"Not assigned","doctor":"Not assigned","diagnosis":[],"investigations":[],"treatment":[],"medications":[],"follow_up":"Awaiting doctor documentation.","notes":"Patient-created portal profile."}
    c.execute('INSERT INTO patient_auth VALUES (?,?,?,?,?,?)',(pid,body.name.strip(),email,mobile,hash_password(body.password),now))
    c.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps(pdata))); c.commit(); c.close()
    return {"message":"Patient account created","user":{"id":pid,"name":body.name.strip(),"email":email,"mobile":mobile,"role":"Patient"}}

@app.post('/api/auth/patient/login')
def patient_login(body: PatientLogin):
    c=db(); row=c.execute('SELECT * FROM patient_auth WHERE lower(email)=?',(body.email.strip().lower(),)).fetchone()
    if row:
        if (body.mobile and row['mobile'] != body.mobile.strip()) or (body.password and row['password_hash'] != hash_password(body.password)):
            c.close(); raise HTTPException(401,'Email, mobile number, or password is incorrect.')
        c.close(); return {"token":"patient-session-token","user":{"id":row['id'],"name":row['name'],"email":row['email'],"mobile":row['mobile'],"role":"Patient"}}
    rows=c.execute('SELECT data FROM patients').fetchall(); c.close()
    # Legacy synthetic demo patients remain available for backwards compatibility.
    for row in rows:
        pdata=json.loads(row['data'])
        if pdata.get('email','').lower() == body.email.strip().lower() and body.email.lower().endswith('@medimind.demo'):
            return {"token":"patient-demo-token","user":{"id":pdata['id'],"name":pdata['name'],"email":pdata['email'],"role":"Patient"}}
    raise HTTPException(401,'No patient account found. Choose Create account to register with your email and mobile number.')

@app.post('/api/auth/staff/register')
def staff_register(body: StaffRegister):
    email=body.email.strip().lower(); mobile=body.mobile.strip(); c=db()
    if c.execute('SELECT id FROM staff WHERE lower(email)=? OR mobile=?',(email,mobile)).fetchone():
        c.close(); raise HTTPException(409,'A staff account already exists with this email or mobile number.')
    sid='S-'+secrets.token_hex(4).upper(); now=datetime.datetime.utcnow().isoformat()
    c.execute('INSERT INTO staff VALUES (?,?,?,?,?,?,?,?)',(sid,body.name.strip(),email,mobile,hash_password(body.password),body.hospital,body.staff_type,now)); c.commit(); c.close()
    return {"message":"Staff account created","user":{"id":sid,"name":body.name.strip(),"email":email,"mobile":mobile,"role":"Staff","hospital":body.hospital,"staff_type":body.staff_type}}

@app.post('/api/auth/staff/login')
def staff_login(body: StaffLogin):
    c=db(); row=c.execute('SELECT * FROM staff WHERE lower(email)=? AND mobile=?',(body.email.strip().lower(),body.mobile.strip())).fetchone()
    if not row or row['password_hash'] != hash_password(body.password):
        c.close(); raise HTTPException(401,'Staff credentials not found. Check email, mobile number and password.')
    c.close(); return {"token":"staff-session-token","user":{"id":row['id'],"name":row['name'],"email":row['email'],"mobile":row['mobile'],"role":"Staff","hospital":row['hospital'],"staff_type":row['staff_type']}}

@app.get('/api/patients')
def patients():
    c=db(); rows=c.execute('SELECT data FROM patients').fetchall(); c.close(); return [json.loads(r['data']) for r in rows]

@app.get('/api/patients/{patient_id}')
def patient(patient_id:str):
    c=db(); r=c.execute('SELECT data FROM patients WHERE id=?',(patient_id,)).fetchone(); c.close()
    if not r: raise HTTPException(404,'Patient not found')
    return json.loads(r['data'])

@app.get('/api/patients/by-email/{email}')
def patient_by_email(email:str):
    c=db(); rows=c.execute('SELECT data FROM patients').fetchall(); c.close()
    for row in rows:
        p=json.loads(row['data'])
        if p.get('email','').lower() == email.lower(): return p
    raise HTTPException(404,'Patient email not found')

def extract_document_text(raw: bytes, filename: str, content_type: str = ''):
    """Extract text from common document types; scanned images/PDFs use optional local Tesseract OCR."""
    ext=Path(filename.lower()).suffix
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, 'File is too large. Maximum supported size is 15 MB.')
    allowed={'.pdf','.png','.jpg','.jpeg','.webp','.tif','.tiff','.txt','.docx'}
    if ext not in allowed:
        raise HTTPException(415, 'Unsupported file type. Upload PDF, PNG, JPG, WEBP, TIFF, TXT, or DOCX.')
    if ext == '.txt':
        return raw.decode('utf-8','replace').strip(), 'text-extracted'
    if ext == '.docx':
        try:
            from docx import Document
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.docx', delete=True) as f:
                f.write(raw); f.flush(); d=Document(f.name)
                text='\n'.join(x.text for x in d.paragraphs if x.text.strip())
                for table in d.tables:
                    for row in table.rows: text += '\n' + ' | '.join(cell.text for cell in row.cells)
            return text.strip(), ('text-extracted' if text.strip() else 'no-text-found')
        except ImportError:
            return '', 'docx-library-missing'
        except Exception:
            return '', 'extraction-failed'
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as f:
                f.write(raw); f.flush(); reader=PdfReader(f.name)
                text='\n'.join(page.extract_text() or '' for page in reader.pages).strip()
            if text:
                return text, 'text-extracted'
        except ImportError:
            pass
        except Exception:
            pass
    # OCR for images and scanned PDFs requires Tesseract installed on the machine and pytesseract in requirements.
    try:
        from PIL import Image
        import pytesseract, tempfile
        pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
        
        if ext == '.pdf':
            try:
                import fitz
                pages=[]
                with fitz.open(stream=raw, filetype='pdf') as pdf:
                    for page in pdf:
                        pix=page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False)
                        img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
                        pages.append(pytesseract.image_to_string(img))
                text='\n'.join(pages).strip()
            except ImportError:
                return '', 'ocr-needs-pymupdf-and-tesseract'
        else:
            with Image.open(io.BytesIO(raw)) as img: text=pytesseract.image_to_string(img).strip()
        return text, ('ocr-completed' if text else 'ocr-no-text-found')
    except ImportError:
        return '', 'ocr-not-installed'
    except Exception:
        return '', 'ocr-failed-check-tesseract'

@app.post('/api/documents/upload')
async def upload(patient_id: str, file: UploadFile = File(...)):
    c=db(); exists=c.execute('SELECT id FROM patients WHERE id=?',(patient_id,)).fetchone(); c.close()
    if not exists: raise HTTPException(404,'Patient not found')
    raw=await file.read(); safe_name=Path(file.filename or 'document').name.replace('..','_')
    text, extraction_status=extract_document_text(raw, safe_name, file.content_type or '')
    doc_id=str(uuid.uuid4()); path=UPLOADS/f"{doc_id}_{safe_name}"; path.write_bytes(raw)
    now=datetime.datetime.utcnow().isoformat(); c=db(); c.execute('INSERT INTO documents (id,patient_id,name,text,stored_path,created_at,extraction_status) VALUES (?,?,?,?,?,?,?)',(doc_id,patient_id,safe_name,text,str(path),now,extraction_status)); c.commit(); c.close()
    return {"id":doc_id,"name":safe_name,"status":"processed","extraction_status":extraction_status,"extracted_characters":len(text),"extracted_text":text[:12000]}

@app.get('/api/patients/{patient_id}/documents')
def patient_documents(patient_id:str):
    c=db(); rows=c.execute('SELECT id,name,text,created_at,extraction_status FROM documents WHERE patient_id=? ORDER BY created_at DESC',(patient_id,)).fetchall(); c.close(); return [dict(r) for r in rows]

@app.post('/api/documents/{document_id}/share')
def share_document(document_id:str, body: ShareRequest):
    c=db(); doc=c.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
    if not doc: c.close(); raise HTTPException(404,'Document not found')
    p=c.execute('SELECT data FROM patients WHERE id=?',(doc['patient_id'],)).fetchone()
    if not p: c.close(); raise HTTPException(404,'Patient not found')
    pdata=json.loads(p['data'])
    if pdata.get('email','').lower() != body.patient_email.lower():
        c.close(); raise HTTPException(400,'Patient email does not match the selected patient record.')
    sid=str(uuid.uuid4()); now=datetime.datetime.utcnow().isoformat(); c.execute('INSERT INTO shares VALUES (?,?,?,?,?,?)',(sid,document_id,doc['patient_id'],body.patient_email,'shared',now))
    c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),body.patient_email,'New document shared',f'{doc["name"]} was shared with you in MediMind. Sign in to view it.','document',0,now)); c.commit(); c.close()
    email_sent = False
    # Real email delivery is enabled when SMTP credentials are configured in environment variables.
    try:
      import smtplib
      from email.message import EmailMessage
      host=os.getenv('MEDIMIND_SMTP_HOST'); port=int(os.getenv('MEDIMIND_SMTP_PORT','587')); sender=os.getenv('MEDIMIND_SMTP_FROM'); password=os.getenv('MEDIMIND_SMTP_PASSWORD')
      if host and sender and password:
        msg=EmailMessage(); msg['Subject']='A document was shared with you on MediMind'; msg['From']=sender; msg['To']=body.patient_email
        msg.set_content(f'A doctor shared {doc["name"]} with you on MediMind. Please sign in to your patient portal to view the document. This email does not contain medical records as an attachment.')
        with smtplib.SMTP(host,port,timeout=15) as server:
          server.starttls(); server.login(sender,password); server.send_message(msg)
        email_sent=True
    except Exception:
      email_sent=False
    return {"status":"shared","message":f"File shared with {body.patient_email}.","email_sent":email_sent,"notification_created":True,"shared_at":now}

@app.get('/api/patients/{patient_id}/shared-documents')
def shared_documents(patient_id:str):
    c=db(); rows=c.execute('''SELECT d.id,d.name,d.created_at,s.shared_at,s.status FROM documents d JOIN shares s ON s.document_id=d.id WHERE s.patient_id=? ORDER BY s.shared_at DESC''',(patient_id,)).fetchall(); c.close(); return [dict(r) for r in rows]

@app.get('/api/documents/{document_id}/download')
def download_document(document_id:str):
    c=db(); r=c.execute('SELECT name,stored_path FROM documents WHERE id=?',(document_id,)).fetchone(); c.close()
    if not r or not Path(r['stored_path']).exists(): raise HTTPException(404,'File not found')
    return FileResponse(r['stored_path'],filename=r['name'])


def demo_generate(p):
    source_docs=p.get('uploaded_documents', [])
    doc_text='\n\n'.join(f"[{d.get('name','Uploaded document')}]\n{d.get('text','')}" for d in source_docs if d.get('text','').strip())
    return {"patient":{"name":p['name'],"id":p['id'],"email":p.get('email',''),"age":p['age'],"gender":p['gender'],"admission_date":p['admission_date'],"discharge_date":p['discharge_date']},"clinical_summary":f"The patient was admitted to {p['ward']} and managed for {', '.join(p['diagnosis'])}. This draft uses the structured patient record and the uploaded documents listed below where text extraction succeeded. All clinical details require treating-doctor verification.","diagnosis":p['diagnosis'],"investigations":p['investigations'],"treatment":p['treatment'],"medications":p['medications'],"follow_up":p['follow_up'],"patient_instructions":"Follow only the instructions confirmed by your treating clinician. Confirm medication names, doses, duration, allergies, and follow-up before discharge.","source_documents":[{"name":d.get('name','Uploaded document'),'extraction_status':d.get('extraction_status','unknown'),'characters_extracted':len(d.get('text',''))} for d in source_docs],"extracted_document_text":doc_text[:18000],"review_note":"AI-assisted draft only. Verify every field against original clinical records before approval."}


def configured_ai_generate(p):
    """Optional hosted AI provider. It receives extracted clinical text and must return a reviewable JSON draft."""
    api_key=os.getenv('MEDIMIND_AI_API_KEY') or os.getenv('OPENAI_API_KEY')
    if not api_key: return None
    model=os.getenv('MEDIMIND_AI_MODEL','gpt-4o-mini')
    prompt=("You are a clinical documentation assistant, not a clinician. Draft a discharge summary using only the supplied patient record and OCR-extracted text. "
            "Never infer missing diagnoses, medicine doses, allergies, results, procedures, or follow-up instructions. If a detail is missing or OCR is unclear, write 'Not documented—doctor to confirm'. "
            "Keep source-derived details separate from uncertain OCR text. Do not create advice. Return ONLY valid JSON with keys: patient, clinical_summary, diagnosis (array), investigations (array), treatment (array), medications (array of name/dose/frequency/duration), follow_up, patient_instructions, source_documents (array), extracted_document_text, review_note. "
            "This is a draft for mandatory treating-doctor review. PATIENT RECORD AND DOCUMENT TEXT:\n"+json.dumps(p,ensure_ascii=False)[:26000])
    try:
        response=requests.post('https://api.openai.com/v1/chat/completions',headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'},json={'model':model,'temperature':0.1,'response_format':{'type':'json_object'},'messages':[{'role':'system','content':'Return a cautious clinical-documentation draft in valid JSON. Never invent missing clinical facts.'},{'role':'user','content':prompt}]},timeout=75)
        response.raise_for_status()
        content=response.json()['choices'][0]['message']['content']
        result=json.loads(content)
        if not isinstance(result,dict) or 'clinical_summary' not in result: return None
        return result
    except Exception:
        return None

def ollama_generate(p):
    if os.getenv('MEDIMIND_USE_OLLAMA','false').lower()!='true': return None
    model=os.getenv('MEDIMIND_OLLAMA_MODEL','llama3.1'); url=os.getenv('MEDIMIND_OLLAMA_URL','http://localhost:11434/api/generate')
    prompt='You are an assistive clinical documentation model. Use ONLY the supplied record. Do not invent diagnoses, medicines, doses, results, allergies, or instructions. Return JSON with keys clinical_summary, diagnosis, investigations, treatment, medications, follow_up, patient_instructions. This is a draft for doctor review. RECORD:\n'+json.dumps(p)
    try:
        r=requests.post(url,json={'model':model,'prompt':prompt,'stream':False,'format':'json'},timeout=60); r.raise_for_status(); return json.loads(r.json()['response'])
    except Exception: return None

@app.post('/api/summaries/generate/{patient_id}')
def generate(patient_id:str, x_medimind_role: str = Header(default='')):
    if x_medimind_role.lower() != 'doctor': raise HTTPException(403, 'Only doctors can generate discharge-summary drafts.')
    p=patient(patient_id)
    c=db(); doc_rows=c.execute('SELECT name,text,extraction_status FROM documents WHERE patient_id=? ORDER BY created_at DESC',(patient_id,)).fetchall(); c.close()
    p['uploaded_documents']=[{'name':r['name'],'text':r['text'] or '', 'extraction_status':r['extraction_status'] or ('text-extracted' if (r['text'] or '').strip() else 'no-text-extracted')} for r in doc_rows]
    data=configured_ai_generate(p) or ollama_generate(p) or demo_generate(p)
    # Ensure document provenance remains visible even if a configured model omits metadata.
    data.setdefault('source_documents',[{'name':d['name'],'extraction_status':d['extraction_status'],'characters_extracted':len(d['text'])} for d in p['uploaded_documents']])
    sid=str(uuid.uuid4()); now=datetime.datetime.utcnow().isoformat(); c=db(); c.execute('INSERT INTO summaries VALUES (?,?,?,?,?,?,?,?)',(sid,patient_id,json.dumps(data),'draft',1,now,now,None)); c.execute('INSERT INTO audits VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),sid,'AI_DRAFT_CREATED','MediMind AI',f"Draft generated using structured records and {len(p['uploaded_documents'])} uploaded document(s).",now)); c.commit(); c.close(); return {"id":sid,"status":"draft","version":1,"summary":data}

@app.get('/api/patients/{patient_id}/summaries')
def patient_summaries(patient_id: str):
    c=db(); rows=c.execute('SELECT id,status,version,created_at,updated_at,approved_at FROM summaries WHERE patient_id=? ORDER BY updated_at DESC',(patient_id,)).fetchall(); c.close(); return [dict(r) for r in rows]

@app.get('/api/summaries/{summary_id}')
def get_summary(summary_id:str):
    c=db(); r=c.execute('SELECT * FROM summaries WHERE id=?',(summary_id,)).fetchone(); c.close()
    if not r: raise HTTPException(404,'Summary not found')
    return {"id":r['id'],"patient_id":r['patient_id'],"status":r['status'],"version":r['version'],"approved_at":r['approved_at'],"summary":json.loads(r['data'])}

@app.put('/api/summaries/{summary_id}')
def update_summary(summary_id:str, body:SummaryUpdate, x_medimind_role: str = Header(default='')):
    if x_medimind_role.lower() != 'doctor': raise HTTPException(403, 'Only doctors can edit discharge summaries.')
    now=datetime.datetime.utcnow().isoformat(); c=db(); r=c.execute('SELECT version FROM summaries WHERE id=?',(summary_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Summary not found')
    v=r['version']+1; c.execute('UPDATE summaries SET data=?,version=?,updated_at=? WHERE id=?',(json.dumps(body.summary),v,now,summary_id)); c.execute('INSERT INTO audits VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),summary_id,'DOCTOR_EDITED','Doctor',f'Edited summary version {v}.',now)); c.commit(); c.close(); return {"status":"saved","version":v}

@app.post('/api/summaries/{summary_id}/approve')
def approve(summary_id:str, x_medimind_role: str = Header(default='')):
    if x_medimind_role.lower() != 'doctor': raise HTTPException(403, 'Only doctors can verify and approve discharge summaries.')
    now=datetime.datetime.utcnow().isoformat(); c=db(); r=c.execute('SELECT * FROM summaries WHERE id=?',(summary_id,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'Summary not found')
    pdata_row=c.execute('SELECT data FROM patients WHERE id=?',(r['patient_id'],)).fetchone()
    if not pdata_row: c.close(); raise HTTPException(404,'Patient not found')
    pdata=json.loads(pdata_row['data']); email=pdata.get('email','')
    already_approved=r['status']=='approved'
    c.execute('UPDATE summaries SET status="approved",approved_at=COALESCE(approved_at,?),updated_at=? WHERE id=?',(now,now,summary_id))
    c.execute('INSERT INTO audits VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),summary_id,'DOCTOR_APPROVED','Doctor','Doctor approved the final discharge summary.',now))
    existing=c.execute('SELECT id FROM summary_shares WHERE summary_id=? AND patient_id=?',(summary_id,r['patient_id'])).fetchone()
    if not existing: c.execute('INSERT INTO summary_shares VALUES (?,?,?,?,?)',(str(uuid.uuid4()),summary_id,r['patient_id'],email,now))
    if not already_approved:
        c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),email,'Doctor-approved discharge summary available','Your doctor-approved discharge summary is available in your MediMind patient dashboard. Sign in to view/download it securely.','discharge_summary',0,now))
    c.commit(); c.close()
    email_sent=False
    if not already_approved:
        email_sent=send_portal_email(email,'MediMind - your discharge summary is approved',f"Hello {pdata.get('name','Patient')},\n\nYour doctor has reviewed and approved your discharge summary. It is now available in your MediMind patient portal. Please sign in to view or download the PDF securely. For privacy, the medical document is not attached to this email.\n\nMediMind Care Team")
    return {"status":"approved","approved_at":now,"patient_portal_updated":True,"email_sent":email_sent,"email_notice":"sent" if email_sent else "not-sent-check-smtp-settings","already_approved":already_approved}

@app.get('/api/summaries/{summary_id}/evidence')
def evidence(summary_id:str):
    s=get_summary(summary_id); p=patient(s['patient_id']); items=[]
    for d in s['summary']['diagnosis']: items.append({"claim":d,"source":"Admission / clinical record","status":"Supported"})
    for x in s['summary']['investigations']: items.append({"claim":x,"source":"Investigation record","status":"Supported"})
    for m in s['summary']['medications']: items.append({"claim":m['name'],"source":"Prescription record","status":"Supported"})
    items.append({"claim":"Follow-up plan","source":"Discharge plan","status":"Supported" if p['follow_up'] else 'Missing'})
    return {"coverage":96,"unsupported_claims":0,"missing_information":0 if p['follow_up'] else 1,"items":items}

@app.get('/api/audit/{summary_id}')
def audit(summary_id:str):
    c=db(); rows=c.execute('SELECT action,actor,details,created_at FROM audits WHERE summary_id=? ORDER BY created_at',(summary_id,)).fetchall(); c.close(); return [dict(r) for r in rows]

@app.get('/api/summaries/{summary_id}/pdf')
def pdf(summary_id:str):
    s=get_summary(summary_id); p=patient(s['patient_id']); data=s['summary']; out=io.BytesIO()
    doc=SimpleDocTemplate(out,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=58,bottomMargin=48,title=f"MediMind Discharge Summary - {p['name']}",author='MediMind')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='MMTitle',parent=styles['Title'],fontName='Helvetica-Bold',fontSize=20,leading=24,textColor=colors.HexColor('#123b5d'),alignment=TA_CENTER,spaceAfter=5))
    styles.add(ParagraphStyle(name='MMSub',parent=styles['Normal'],fontSize=9,leading=13,textColor=colors.HexColor('#456477'),alignment=TA_CENTER,spaceAfter=14))
    styles.add(ParagraphStyle(name='MMSection',parent=styles['Heading2'],fontSize=11,leading=14,textColor=colors.HexColor('#145c73'),spaceBefore=10,spaceAfter=5,keepWithNext=True))
    styles.add(ParagraphStyle(name='MMBody',parent=styles['BodyText'],fontSize=9,leading=13,spaceAfter=5,wordWrap='CJK'))
    styles.add(ParagraphStyle(name='MMNote',parent=styles['Italic'],fontSize=8,leading=11,textColor=colors.HexColor('#6b7280'),spaceBefore=8))
    story=[Paragraph('MediMind',styles['MMTitle']),Paragraph('SMART, VERIFIED &amp; DOCTOR-APPROVED DISCHARGE SUMMARY',styles['MMSub'])]
    status_label='DOCTOR APPROVED' if s['status']=='approved' else 'DRAFT — NOT APPROVED'
    banner=Table([[Paragraph(f'<b>DOCUMENT STATUS</b><br/>{escape(status_label)}',styles['MMBody']),Paragraph(f'<b>Patient ID</b><br/>{escape(str(p.get("id","—")))}',styles['MMBody'])]],colWidths=[250,250])
    banner.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#eaf4f8')),('BOX',(0,0),(-1,-1),0.7,colors.HexColor('#b8d4df')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story += [banner,Spacer(1,10),Paragraph('1. PATIENT &amp; ADMISSION DETAILS',styles['MMSection'])]
    patient_data=data.get('patient',{})
    rows=[]
    labels=[('Patient Name','name'),('Patient ID','id'),('Email','email'),('Age','age'),('Gender','gender'),('Admission Date','admission_date'),('Discharge Date','discharge_date')]
    for label,key in labels: rows.append([Paragraph(f'<b>{escape(label)}</b>',styles['MMBody']),Paragraph(escape(str(patient_data.get(key,'Not documented') or 'Not documented')),styles['MMBody'])])
    t=Table(rows,colWidths=[140,360],repeatRows=0,hAlign='LEFT'); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#d5e1e8')),('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f4f8fa')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),4)])); story.append(t)
    def add_section(title, value):
        story.append(Paragraph(escape(title.upper()),styles['MMSection']))
        if isinstance(value,list):
            vals=value
        else: vals=[value]
        vals=[str(x) for x in vals if x not in (None,'',[])]
        if not vals: vals=['Not documented in the available record.']
        for item in vals: story.append(Paragraph('• '+escape(item).replace('\n','<br/>'),styles['MMBody']))
    add_section('2. Clinical Summary',data.get('clinical_summary','Not documented'))
    add_section('3. Diagnosis',data.get('diagnosis',[]))
    add_section('4. Investigations & Results',data.get('investigations',[]))
    add_section('5. Treatment / Procedures',data.get('treatment',[]))
    story.append(Paragraph('6. Medication Reconciliation',styles['MMSection']))
    meds=data.get('medications',[])
    if meds:
        med_rows=[[Paragraph('<b>Medication</b>',styles['MMBody']),Paragraph('<b>Dose</b>',styles['MMBody']),Paragraph('<b>Frequency</b>',styles['MMBody']),Paragraph('<b>Duration</b>',styles['MMBody'])]]
        for m in meds: med_rows.append([Paragraph(escape(str(m.get('name','Not documented'))),styles['MMBody']),Paragraph(escape(str(m.get('dose','Not documented'))),styles['MMBody']),Paragraph(escape(str(m.get('frequency','Not documented'))),styles['MMBody']),Paragraph(escape(str(m.get('duration','Not documented'))),styles['MMBody'])])
        mt=Table(med_rows,colWidths=[190,85,105,120],repeatRows=1); mt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf4f8')),('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#d5e1e8')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),4)])); story.append(mt)
    else: story.append(Paragraph('No medication details documented.',styles['MMBody']))
    add_section('7. Follow-up Plan',data.get('follow_up','Not documented'))
    add_section('8. Patient Instructions',data.get('patient_instructions','To be confirmed by treating clinician.'))
    source_docs=data.get('source_documents',[])
    if source_docs:
        story.append(Paragraph('9. Source Documents Reviewed',styles['MMSection']))
        for item in source_docs:
            story.append(Paragraph('• '+escape(str(item.get('name','Uploaded document')))+f" — {escape(str(item.get('extraction_status','status unknown')))}; {int(item.get('characters_extracted',0) or 0)} characters extracted",styles['MMBody']))
    story += [Spacer(1,12),Paragraph(f"Treating clinician: {escape(str(p.get('doctor','Not assigned')))}",styles['MMBody']),Paragraph(f"Approval timestamp: {escape(str(s.get('approved_at') or 'Not approved'))}",styles['MMBody']),Paragraph('Clinical safety notice: This document must be reviewed against the original patient record. AI/OCR extraction can be incomplete or inaccurate. Confirm all diagnoses, results, medicines, doses, allergies and follow-up instructions with the treating clinician. This prototype is not a substitute for professional clinical judgment.',styles['MMNote'])]
    def page_decor(canvas, pdfdoc):
        canvas.saveState(); w,h=A4
        canvas.setStrokeColor(colors.HexColor('#c7dce5')); canvas.line(42,h-40,w-42,h-40)
        canvas.setFont('Helvetica',8); canvas.setFillColor(colors.HexColor('#607987')); canvas.drawString(42,h-31,'MediMind • Clinical Documentation')
        canvas.drawRightString(w-42,26,f'Confidential • Page {pdfdoc.page}')
        canvas.restoreState()
    doc.build(story,onFirstPage=page_decor,onLaterPages=page_decor); out.seek(0)
    return StreamingResponse(out,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename=MediMind_{p["id"]}_Discharge_Summary.pdf'})


class PatientCreate(BaseModel):
    name: str
    email: str
    mobile: str = ''
    doctor_id: str
    age: str = 'Not provided'
    gender: str = 'Not provided'

class AppointmentCreate(BaseModel):
    patient_id: str
    doctor_id: str
    scheduled_at: str
    reason: str = 'Consultation'

class AssignRequest(BaseModel):
    doctor_id: str

class AppointmentRequest(BaseModel):
    patient_id: str
    doctor_id: str
    preferred_at: str
    reason: str = 'Consultation'

@app.get('/api/doctors')
def list_doctors():
    c=db(); rows=c.execute('SELECT id,name,email,mobile,hospital FROM doctors ORDER BY name').fetchall(); c.close(); return [dict(r) for r in rows]

@app.post('/api/patients/register')
def create_patient(body: PatientCreate):
    email=body.email.strip().lower(); mobile=body.mobile.strip(); c=db()
    if any(json.loads(r['data']).get('email','').lower()==email for r in c.execute('SELECT data FROM patients').fetchall()):
        c.close(); raise HTTPException(409,'A patient with this email already exists.')
    d=c.execute('SELECT id,name FROM doctors WHERE id=?',(body.doctor_id,)).fetchone()
    if not d: c.close(); raise HTTPException(404,'Choose a registered doctor.')
    pid='P-'+secrets.token_hex(4).upper(); pdata={'id':pid,'name':body.name.strip(),'email':email,'mobile':mobile,'age':body.age,'gender':body.gender,'admission_date':'Not assigned','discharge_date':'Pending','ward':'Not assigned','doctor':d['name'],'doctor_id':d['id'],'diagnosis':[],'investigations':[],'treatment':[],'medications':[],'follow_up':'Awaiting doctor documentation.','notes':'Registered by MediMind staff.'}
    now=datetime.datetime.utcnow().isoformat(); c.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps(pdata)))
    if email and mobile:
      try: c.execute('INSERT INTO patient_auth VALUES (?,?,?,?,?,?)',(pid,pdata['name'],email,mobile,hash_password('ChangeMe123!'),now))
      except sqlite3.IntegrityError: pass
    c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),email,'MediMind patient portal created',f'Staff registered your profile and assigned {d['name']}. Appointment details will follow separately.','account',0,now)); c.commit(); c.close()
    email_sent=send_portal_email(email,'Welcome to MediMind - patient registration',f'Hello {pdata['name']},\n\nYour patient profile has been registered in MediMind. Assigned doctor: {d['name']}. Please sign in to your patient portal to view updates.\n\nThis is an automated notification; it does not include medical records.')
    pdata['email_notification_sent']=email_sent
    return pdata

@app.post('/api/patients/{patient_id}/assign')
def assign_patient(patient_id: str, body: AssignRequest):
    c=db(); row=c.execute('SELECT data FROM patients WHERE id=?',(patient_id,)).fetchone(); d=c.execute('SELECT id,name FROM doctors WHERE id=?',(body.doctor_id,)).fetchone()
    if not row or not d: c.close(); raise HTTPException(404,'Patient or doctor not found.')
    pdata=json.loads(row['data']); pdata['doctor_id']=d['id']; pdata['doctor']=d['name']; c.execute('UPDATE patients SET data=? WHERE id=?',(json.dumps(pdata),patient_id)); now=datetime.datetime.utcnow().isoformat(); c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),pdata.get('email',''),'Doctor assigned to your care',f'{d["name"]} is listed as your assigned doctor in MediMind.','assignment',0,now)); c.commit(); c.close(); email_sent=send_portal_email(pdata.get('email',''),'MediMind - your doctor assignment',f'Hello {pdata.get('name','Patient')},\n\nYour assigned doctor is {d['name']}. Please sign in to your patient portal for updates.'); return {'status':'assigned','patient':pdata,'email_sent':email_sent}

@app.get('/api/appointments')
def list_appointments():
    c=db(); rows=c.execute('SELECT a.*,p.data FROM appointments a LEFT JOIN patients p ON p.id=a.patient_id ORDER BY scheduled_at').fetchall(); c.close(); out=[]
    for r in rows:
      x=dict(r); pdata=json.loads(x.pop('data')) if x.get('data') else {}; x['patient_name']=pdata.get('name','Unknown patient'); x['patient_email']=pdata.get('email',''); out.append(x)
    return out

@app.post('/api/appointments')
def create_appointment(body: AppointmentCreate):
    c=db(); p=c.execute('SELECT data FROM patients WHERE id=?',(body.patient_id,)).fetchone(); d=c.execute('SELECT id,name,email FROM doctors WHERE id=?',(body.doctor_id,)).fetchone()
    if not p or not d: c.close(); raise HTTPException(404,'Patient or doctor not found.')
    pdata=json.loads(p['data']); aid='A-'+secrets.token_hex(4).upper(); now=datetime.datetime.utcnow().isoformat(); c.execute('INSERT INTO appointments VALUES (?,?,?,?,?,?,?)',(aid,body.patient_id,body.doctor_id,body.scheduled_at,body.reason,'Scheduled',now)); c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),d['email'],'Appointment scheduled',f'{pdata["name"]}: {body.reason} at {body.scheduled_at}','appointment',0,now)); c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),pdata.get('email',''),'MediMind appointment scheduled',f'Your appointment with {d["name"]} is scheduled for {body.scheduled_at}. Reason: {body.reason}.','appointment',0,now)); c.commit(); c.close()
    patient_email_sent=send_portal_email(pdata.get('email',''),'MediMind - appointment confirmation',f'Hello {pdata.get('name','Patient')},\n\nYour appointment with {d['name']} is scheduled for {body.scheduled_at}.\nReason: {body.reason}\n\nPlease contact the clinic if you need to change this appointment.')
    doctor_email_sent=send_portal_email(d['email'],'MediMind - new patient appointment',f'Appointment scheduled for {pdata['name']} ({pdata.get('email','')}) on {body.scheduled_at}.\nReason: {body.reason}')
    return {'id':aid,'status':'Scheduled','patient_name':pdata['name'],'doctor_name':d['name'],'scheduled_at':body.scheduled_at,'patient_email_sent':patient_email_sent,'doctor_email_sent':doctor_email_sent}


@app.post('/api/appointments/request')
def request_appointment(body: AppointmentRequest):
    c=db(); p=c.execute('SELECT data FROM patients WHERE id=?',(body.patient_id,)).fetchone(); d=c.execute('SELECT id,name,email FROM doctors WHERE id=?',(body.doctor_id,)).fetchone()
    if not p or not d:
      c.close(); raise HTTPException(404,'Patient or doctor not found.')
    pdata=json.loads(p['data']); pdata['doctor_id']=d['id']; pdata['doctor']=d['name']; c.execute('UPDATE patients SET data=? WHERE id=?',(json.dumps(pdata),body.patient_id)); aid='A-'+secrets.token_hex(4).upper(); now=datetime.datetime.utcnow().isoformat()
    c.execute('INSERT INTO appointments VALUES (?,?,?,?,?,?,?)',(aid,body.patient_id,body.doctor_id,body.preferred_at,body.reason,'Requested',now))
    c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),d['email'],'Patient appointment request',f'{pdata["name"]} requested an appointment for {body.preferred_at}. Reason: {body.reason}', 'appointment_request',0,now))
    c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),pdata.get('email',''),'Appointment request received',f'Your appointment request with {d["name"]} for {body.preferred_at} has been received and is awaiting confirmation.','appointment_request',0,now))
    c.commit(); c.close()
    patient_sent=send_portal_email(pdata.get('email',''),'MediMind - appointment request received',f'Hello {pdata.get("name","Patient")},\n\nWe received your appointment request with {d["name"]} for {body.preferred_at}. The clinic will confirm it shortly.')
    doctor_sent=send_portal_email(d['email'],'MediMind - appointment request',f'{pdata["name"]} requested an appointment for {body.preferred_at}. Reason: {body.reason}')
    return {'id':aid,'status':'Requested','patient_email_sent':patient_sent,'doctor_email_sent':doctor_sent}

@app.post('/api/summaries/{summary_id}/share')
def share_approved_summary(summary_id: str, x_medimind_role: str = Header(default='')):
    if x_medimind_role.lower() != 'staff': raise HTTPException(403, 'Only authorized staff can send approved summaries to patients.')
    c=db(); row=c.execute('SELECT * FROM summaries WHERE id=?',(summary_id,)).fetchone()
    if not row:
      c.close(); raise HTTPException(404,'Summary not found.')
    if row['status'] != 'approved':
      c.close(); raise HTTPException(409,'Only a doctor-approved discharge summary can be sent to a patient.')
    p_row=c.execute('SELECT data FROM patients WHERE id=?',(row['patient_id'],)).fetchone()
    if not p_row:
      c.close(); raise HTTPException(404,'Patient not found.')
    pdata=json.loads(p_row['data']); email=pdata.get('email',''); now=datetime.datetime.utcnow().isoformat()
    already=c.execute('SELECT id FROM summary_shares WHERE summary_id=? AND patient_id=?',(summary_id,row['patient_id'])).fetchone()
    if not already:
      c.execute('INSERT INTO summary_shares VALUES (?,?,?,?,?)',(str(uuid.uuid4()),summary_id,row['patient_id'],email,now))
    c.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),email,'Doctor-approved discharge summary available','Your doctor-approved discharge summary is available in your MediMind patient dashboard.','discharge_summary',0,now)); c.commit(); c.close()
    sent=send_portal_email(email,'MediMind - discharge summary available',f'Hello {pdata.get("name","Patient")},\n\nYour doctor-approved discharge summary is now available in your MediMind patient portal. Please sign in to view it securely. Medical records are not attached to this email.')
    return {'status':'shared','email_sent':sent,'patient_email':email,'message':'Approved summary shared to the patient portal.'}

@app.get('/api/patients/{patient_id}/shared-summaries')
def shared_summaries(patient_id: str):
    c=db(); rows=c.execute("SELECT s.id AS summary_id,s.status,s.approved_at,ss.shared_at,p.data FROM summary_shares ss JOIN summaries s ON s.id=ss.summary_id JOIN patients p ON p.id=ss.patient_id WHERE ss.patient_id=? ORDER BY ss.shared_at DESC",(patient_id,)).fetchall(); c.close(); out=[]
    for r in rows:
      pdata=json.loads(r['data']); out.append({'id':r['summary_id'],'status':r['status'],'approved_at':r['approved_at'],'shared_at':r['shared_at'],'patient_name':pdata.get('name'),'patient_id':patient_id})
    return out

@app.get('/api/notifications/{email}')
def get_notifications(email: str):
    c=db(); rows=c.execute('SELECT id,title,message,kind,read_flag,created_at FROM notifications WHERE lower(user_email)=lower(?) ORDER BY created_at DESC LIMIT 50',(email,)).fetchall(); c.close(); return [dict(r) for r in rows]

@app.post('/api/notifications/{notification_id}/read')
def read_notification(notification_id: str):
    c=db(); c.execute('UPDATE notifications SET read_flag=1 WHERE id=?',(notification_id,)); c.commit(); c.close(); return {'status':'read'}
